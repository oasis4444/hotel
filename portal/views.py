from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from hotels.models import Formula, Hotel, Room
from hotels.utils import parse_local, to_int
from kitchen import services as ks
from kitchen.models import Dish
from reservations import services as rs
from reservations.models import Client, Reservation


class _AnonRequest:
    """Requête publique minimale : pas d'utilisateur connecté, mais hôtel + META présents
    pour que les services (audit, facturation) fonctionnent sans adaptation."""

    def __init__(self, request, hotel):
        self.user = None
        self.hotel = hotel
        self.META = request.META


def _hotel(slug):
    return get_object_or_404(Hotel, slug=slug, is_active=True)


def _ip(request):
    return (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "?"))


def home(request, slug):
    hotel = _hotel(slug)
    ctx = {
        "hotel": hotel, "tiles": rs.rooms_with_state(hotel),
        "formulas": Formula.objects.filter(hotel=hotel, is_active=True),
        "start": timezone.localtime().strftime("%Y-%m-%dT%H:%M"), "form": {},
    }
    return render(request, "portal/home.html", ctx)


def rooms_fragment(request, slug):
    hotel = _hotel(slug)
    return render(request, "portal/_rooms.html", {"hotel": hotel, "tiles": rs.rooms_with_state(hotel)})


def available(request, slug):
    hotel = _hotel(slug)
    try:
        formula = Formula.objects.get(pk=int(request.GET.get("formula", 0)), hotel=hotel, is_active=True)
    except (Formula.DoesNotExist, ValueError):
        return JsonResponse({"rooms": [], "error": "Choisissez une formule."})
    start = parse_local(request.GET.get("start"))
    if not start:
        return JsonResponse({"rooms": [], "error": "Choisissez l'heure d'arrivée."})
    try:
        s, e = formula.compute_slot(start)
    except ValidationError as exc:
        return JsonResponse({"rooms": [], "error": exc.messages[0]})
    rooms = [{"id": r.id, "number": r.number, "type": r.room_type.name} for r, _s, _e in rs.available_rooms(hotel, formula, start)]
    return JsonResponse({"rooms": rooms, "price": formula.price,
                         "start": timezone.localtime(s).strftime("%d/%m %Hh%M"), "end": timezone.localtime(e).strftime("%d/%m %Hh%M")})


@require_POST
def book(request, slug):
    hotel = _hotel(slug)
    p = request.POST
    ctx = {
        "hotel": hotel, "tiles": rs.rooms_with_state(hotel),
        "formulas": Formula.objects.filter(hotel=hotel, is_active=True),
        "start": p.get("start", ""), "form": p,
    }
    errors = []
    if p.get("website"):  # honeypot anti-robot
        return redirect("portal_home", slug=slug)
    ip_key = f"portal-book:{hotel.pk}:{_ip(request)}"
    hits = cache.get(ip_key, 0)
    if hits >= 5:
        errors.append("Trop de demandes depuis cet appareil. Appelez l'hôtel directement.")
    phone = "".join(ch for ch in p.get("phone", "") if ch.isdigit() or ch == "+")
    name = p.get("guest_name", "").strip()
    if len(phone) < 9:
        errors.append("Indiquez un numéro de téléphone joignable : la réception vous rappellera pour confirmer.")
    if len(name) < 2:
        errors.append("Indiquez votre nom.")
    if not errors:
        if Client.objects.filter(hotel=hotel, phone=phone, is_blacklisted=True).exists():
            errors.append("Nous ne pouvons pas traiter cette demande en ligne. Merci d'appeler l'hôtel.")
        pending = Reservation.objects.filter(hotel=hotel, phone=phone, status=Reservation.Status.PENDING).count()
        if pending >= hotel.online_max_pending_per_phone:
            errors.append("Vous avez déjà des demandes en attente de confirmation avec ce numéro.")
    if not errors:
        start = parse_local(p.get("start"))
        try:
            if not start:
                raise ValidationError("Indiquez la date et l'heure d'arrivée.")

            class Anon:  # requête publique : pas d'utilisateur connecté
                user = None
                hotel = None
                META = request.META

            anon = Anon()
            anon.hotel = hotel
            res = rs.create_reservation(anon, hotel, int(p.get("room") or 0), int(p.get("formula") or 0), start,
                                        Reservation.Source.ONLINE, name, phone, p.get("notes", "")[:300])
            cache.set(ip_key, hits + 1, 3600)
            return redirect("portal_status", slug=slug, code=res.code)
        except (ValidationError, Room.DoesNotExist, Formula.DoesNotExist, ValueError) as e:
            errors.extend(e.messages if isinstance(e, ValidationError) else ["Choix de chambre ou de formule invalide."])
    ctx["errors"] = errors
    return render(request, "portal/home.html", ctx)


def _find_stay(hotel, room_number, last_name):
    return Reservation.objects.filter(
        hotel=hotel, status=Reservation.Status.CHECKED_IN, room__number=room_number.strip(),
        guest_name__icontains=last_name.strip(),
    ).select_related("room").first() if room_number.strip() and last_name.strip() else None


def my_stay(request, slug):
    """Espace client en libre-service : commander à manger ou prolonger son séjour,
    sans passer par la réception. Vérification légère par numéro de chambre + nom."""
    hotel = _hotel(slug)
    room_number = request.POST.get("room_number", request.GET.get("room_number", ""))
    last_name = request.POST.get("last_name", request.GET.get("last_name", ""))
    res = _find_stay(hotel, room_number, last_name)
    dishes = list(Dish.objects.filter(hotel=hotel, is_available=True).order_by("category", "sort_order", "name")) if res else []
    grouped_dishes = []
    for cat, label in Dish.Category.choices:
        items = [d for d in dishes if d.category == cat]
        if items:
            grouped_dishes.append((label, items))
    ctx = {
        "hotel": hotel, "res": res, "room_number": room_number, "last_name": last_name,
        "dishes": dishes, "grouped_dishes": grouped_dishes,
        "formulas": Formula.objects.filter(hotel=hotel, is_active=True) if res else [],
    }
    if request.method == "POST" and res:
        action = request.POST.get("action")
        req = _AnonRequest(request, hotel)
        try:
            if action == "order":
                items = [(d.pk, request.POST.get(f"qty_{d.pk}", "0")) for d in ctx["dishes"]]
                ks.create_order(req, reservation_id=res.pk, notes=request.POST.get("notes", "")[:300], items=items, self_service=True)
                ctx["notice"] = "Commande envoyée à la réception pour confirmation. Elle partira ensuite en cuisine et sera livrée dans votre chambre."
            elif action == "extend":
                formula_id = to_int(request.POST.get("formula"))
                limit = rs.extend(req, res, formula_id)
                ctx["notice"] = "Séjour prolongé." + (f" Limite : {timezone.localtime(limit):%d/%m %Hh%M}." if limit else "")
                res.refresh_from_db()
                ctx["res"] = res
        except (ValidationError, Formula.DoesNotExist, Dish.DoesNotExist, ValueError) as e:
            ctx["errors"] = e.messages if isinstance(e, ValidationError) else ["Choix invalide. Merci de réessayer."]
    elif request.method == "POST" and not res:
        ctx["errors"] = ["Aucun séjour en cours trouvé pour cette chambre et ce nom. Vérifiez l'orthographe ou appelez la réception."]
    return render(request, "portal/my_stay.html", ctx)


def status(request, slug, code):
    hotel = _hotel(slug)
    res = get_object_or_404(Reservation.objects.select_related("room", "formula"), hotel=hotel, code=code)
    phone_tail = request.GET.get("t", "")
    # Le suivi n'affiche aucune donnée sensible : code de réservation + état seulement.
    steps = [("pending", "En attente de confirmation"), ("accepted", "Acceptée"), ("confirmed", "Confirmée par appel")]
    order = {"pending": 0, "accepted": 1, "confirmed": 2, "checked_in": 3}
    return render(request, "portal/status.html", {
        "hotel": hotel, "res": res, "steps": steps, "reached": order.get(res.status, 3), "order": order})
