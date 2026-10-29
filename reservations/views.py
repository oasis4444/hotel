from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import AuditLog
from billing.services import ensure_invoice, line_allocations
from hotels.models import Formula, Room
from hotels.permissions import has_perm, perm_required
from hotels.utils import parse_local

from . import services as rs
from .forms import ClientForm
from .models import Client, Reservation

S = Reservation.Status

TABS = [
    ("todo", "À traiter"), ("upcoming", "À venir"), ("stay", "En séjour"), ("past", "Terminées"),
]


def _fail(request, exc):
    for m in getattr(exc, "messages", [str(exc)]):
        messages.error(request, m)


@perm_required("reservation.view")
def reservation_list(request):
    hotel = request.hotel
    qs = Reservation.objects.filter(hotel=hotel).select_related("room", "formula", "client")
    tab = request.GET.get("tab", "todo")
    q = request.GET.get("q", "").strip()
    now = timezone.now()
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(guest_name__icontains=q) | Q(phone__icontains=q) | Q(room__number__icontains=q))
        tab = "all"
    elif tab == "todo":
        qs = qs.filter(Q(status=S.PENDING) | Q(status=S.ACCEPTED)).order_by("start_at")
    elif tab == "upcoming":
        qs = qs.filter(status__in=[S.ACCEPTED, S.CONFIRMED], start_at__gte=now - timedelta(hours=6)).order_by("start_at")
    elif tab == "stay":
        qs = qs.filter(status=S.CHECKED_IN).order_by("end_at")
    else:
        qs = qs.filter(status__in=[S.CHECKED_OUT, S.CANCELLED, S.NO_SHOW]).order_by("-start_at")
    counts = {
        "todo": Reservation.objects.filter(hotel=hotel, status__in=[S.PENDING, S.ACCEPTED]).count(),
        "stay": Reservation.objects.filter(hotel=hotel, status=S.CHECKED_IN).count(),
    }
    return render(request, "reservations/list.html", {
        "items": qs[:150], "tabs": TABS, "tab": tab, "q": q, "counts": counts, "page": "reservations"})


def _form_context(request, res=None):
    hotel = request.hotel
    return {
        "formulas": Formula.objects.filter(hotel=hotel, is_active=True),
        "rooms": Room.objects.filter(hotel=hotel, is_active=True).select_related("room_type"),
        "res": res, "page": "reservations",
    }


@perm_required("reservation.create")
def reservation_new(request):
    ctx = _form_context(request)
    if request.method == "POST":
        p = request.POST
        start = parse_local(p.get("start"))
        try:
            if not start:
                raise ValidationError("Indiquez la date et l'heure d'arrivée.")
            res = rs.create_reservation(
                request, request.hotel, int(p.get("room") or 0), int(p.get("formula") or 0), start,
                p.get("source", Reservation.Source.ONSITE), p.get("guest_name", ""), p.get("phone", ""), p.get("notes", ""),
            )
            messages.success(request, f"Réservation {res.code} créée.")
            return redirect("reservation_detail", pk=res.pk)
        except (ValidationError, Room.DoesNotExist, Formula.DoesNotExist, ValueError) as e:
            _fail(request, e if isinstance(e, ValidationError) else ValidationError("Formule ou chambre invalide."))
        ctx["form"] = p
    else:
        ctx["form"] = {"start": timezone.localtime().strftime("%Y-%m-%dT%H:%M"), "source": "onsite"}
        if request.GET.get("room"):
            ctx["form"]["room"] = request.GET["room"]
    return render(request, "reservations/form.html", ctx)


@perm_required("reservation.create")
def reservation_edit(request, pk):
    res = get_object_or_404(Reservation, pk=pk, hotel=request.hotel)
    ctx = _form_context(request, res)
    if request.method == "POST":
        p = request.POST
        try:
            start = parse_local(p.get("start"))
            if not start:
                raise ValidationError("Indiquez la date et l'heure d'arrivée.")
            rs.modify_reservation(request, res, int(p.get("room") or 0), int(p.get("formula") or 0), start,
                                  p.get("guest_name", ""), p.get("phone", ""), p.get("notes", ""))
            messages.success(request, "Réservation modifiée (l'ancienne valeur est conservée dans le journal).")
            return redirect("reservation_detail", pk=res.pk)
        except (ValidationError, Room.DoesNotExist, Formula.DoesNotExist, ValueError) as e:
            _fail(request, e if isinstance(e, ValidationError) else ValidationError("Formule ou chambre invalide."))
        ctx["form"] = p
    else:
        ctx["form"] = {"start": res.start_at, "room": res.room_id, "formula": res.formula_id,
                       "guest_name": res.guest_name, "phone": res.phone, "notes": res.notes, "source": res.source}
    return render(request, "reservations/form.html", ctx)


@perm_required("reservation.view")
def available_api(request):
    """Chambres disponibles pour (formule, début) : alimente le formulaire en direct."""
    try:
        formula = Formula.objects.get(pk=int(request.GET.get("formula", 0)), hotel=request.hotel, is_active=True)
    except (Formula.DoesNotExist, ValueError):
        return JsonResponse({"rooms": [], "error": "Choisissez une formule."})
    start = parse_local(request.GET.get("start"))
    if not start:
        return JsonResponse({"rooms": [], "error": "Choisissez l'heure d'arrivée."})
    try:
        s, e = formula.compute_slot(start)
    except ValidationError as exc:
        return JsonResponse({"rooms": [], "error": exc.messages[0]})
    exclude = None
    if request.GET.get("exclude", "").isdigit():
        exclude = Reservation.objects.filter(pk=int(request.GET["exclude"]), hotel=request.hotel).first()
    rooms = [
        {"id": r.id, "number": r.number, "type": r.room_type.name}
        for r, _s, _e in rs.available_rooms(request.hotel, formula, start, exclude=exclude)
    ]
    return JsonResponse({
        "rooms": rooms, "price": formula.price,
        "start": timezone.localtime(s).strftime("%d/%m %Hh%M"), "end": timezone.localtime(e).strftime("%d/%m %Hh%M"),
    })


@perm_required("reservation.view")
def reservation_detail(request, pk):
    res = get_object_or_404(Reservation.objects.select_related("room", "formula", "client", "hotel"), pk=pk, hotel=request.hotel)
    invoice = ensure_invoice(request, res) if res.status != S.CANCELLED or hasattr(res, "invoice") else None
    now = timezone.now()
    history = AuditLog.objects.filter(hotel=request.hotel, object_type="reservation", object_id=str(res.pk)).select_related("user")[:20]
    limit = rs.extension_limit(res) if res.status == S.CHECKED_IN else None
    needs = rs.checkout_requirements(res, now) if res.status == S.CHECKED_IN else []
    stay_link = None
    if res.status == S.CHECKED_IN:
        from portal.views import make_stay_token

        stay_link = request.build_absolute_uri(f"/h/{res.hotel.slug}/sejour/{make_stay_token(res)}/")
    ctx = {
        "res": res, "invoice": invoice, "history": history, "limit": limit, "needs": needs,
        "allocs": line_allocations(invoice) if invoice else [],
        "formulas": Formula.objects.filter(hotel=request.hotel, is_active=True),
        "call_choices": [c for c in Reservation.Call.choices if c[0]],
        "stay_link": stay_link, "page": "reservations", "now": now,
    }
    return render(request, "reservations/detail.html", ctx)


@perm_required("reservation.view")
@require_POST
def reservation_action(request, pk, action):
    res = get_object_or_404(Reservation, pk=pk, hotel=request.hotel)
    m = request.membership
    p = request.POST
    try:
        if action == "accept":
            _need(m, "reservation.validate")
            rs.accept(request, res)
            messages.success(request, "Réservation acceptée. Pensez à l'appel de confirmation.")
        elif action == "call":
            _need(m, "reservation.validate")
            rs.record_call(request, res, p.get("result", ""), p.get("note", ""))
            messages.success(request, "Résultat de l'appel enregistré.")
        elif action == "cancel":
            _need(m, "reservation.cancel")
            rs.cancel(request, res, p.get("reason", ""))
            messages.success(request, "Réservation annulée. Le gérant est informé via le journal.")
        elif action == "noshow":
            _need(m, "reservation.cancel")
            rs.mark_no_show(request, res)
            messages.success(request, "Marquée comme no-show. La chambre est libérée.")
        elif action == "checkout":
            _need(m, "stay.checkout")
            rs.check_out(request, res, p.get("reason", ""))
            messages.success(request, f"Check-out enregistré. La chambre {res.room.number} passe à « À nettoyer ».")
        elif action == "extend":
            _need(m, "stay.extend")
            rs.extend(request, res, int(p.get("formula") or 0))
            messages.success(request, "Prolongation enregistrée et ajoutée à la facture (à encaisser).")
    except (ValidationError, Formula.DoesNotExist, ValueError) as e:
        _fail(request, e if isinstance(e, ValidationError) else ValidationError("Formule invalide."))
    return redirect("reservation_detail", pk=res.pk)


def _need(membership, perm):
    if not has_perm(membership, perm):
        raise ValidationError("Vous n'avez pas le droit d'effectuer cette action.")


@perm_required("stay.checkin")
def checkin(request, pk):
    res = get_object_or_404(Reservation.objects.select_related("room", "formula", "client"), pk=pk, hotel=request.hotel)
    if request.method == "POST":
        number = request.POST.get("id_number", "").strip()
        instance = None
        if number:
            instance = Client.objects.filter(hotel=request.hotel, id_number=number).first()
        instance = instance or res.client
        form = ClientForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            client = form.save(commit=False)
            client.hotel = request.hotel
            if not client.signature and instance and instance.signature:
                client.signature = instance.signature
            client.save()
            override = request.POST.get("override_reason", "").strip()
            if override and not has_perm(request.membership, "*") and request.membership.role not in ("manager", "owner"):
                messages.error(request, "Seul le gérant peut autoriser un check-in avec identité incomplète.")
            else:
                try:
                    if client.is_blacklisted:
                        messages.warning(request, "Ce client figure sur la liste noire.")
                    rs.check_in(request, res, client, override)
                    messages.success(request, f"Check-in effectué : chambre {res.room.number} occupée.")
                    return redirect("reservation_detail", pk=res.pk)
                except ValidationError as e:
                    _fail(request, e)
    else:
        initial = {}
        parts = res.guest_name.split(" ", 1)
        if not res.client:
            initial = {"last_name": parts[0], "first_name": parts[1] if len(parts) > 1 else "", "phone": res.phone}
        form = ClientForm(instance=res.client, initial=initial)
    return render(request, "reservations/checkin.html", {
        "res": res, "form": form, "page": "reservations",
        "can_override": request.membership.role in ("manager", "owner"),
    })


@perm_required("client.view")
def client_list(request):
    q = request.GET.get("q", "").strip()
    qs = Client.objects.filter(hotel=request.hotel)
    if q:
        qs = qs.filter(Q(last_name__icontains=q) | Q(first_name__icontains=q) | Q(phone__icontains=q) | Q(id_number__icontains=q))
    return render(request, "reservations/clients.html", {"items": qs[:100], "q": q, "page": "clients"})


@perm_required("client.edit")
def client_edit(request, pk):
    client = get_object_or_404(Client, pk=pk, hotel=request.hotel)
    stays = client.reservations.select_related("room", "formula").order_by("-start_at")[:20]
    if request.method == "POST":
        form = ClientForm(request.POST, request.FILES, instance=client)
        if form.is_valid():
            c = form.save(commit=False)
            c.is_vip = bool(request.POST.get("is_vip"))
            c.is_blacklisted = bool(request.POST.get("is_blacklisted"))
            c.save()
            messages.success(request, "Fiche client enregistrée.")
            return redirect("client_edit", pk=client.pk)
    else:
        form = ClientForm(instance=client)
    return render(request, "reservations/client_edit.html", {"client": client, "form": form, "stays": stays, "page": "clients"})
