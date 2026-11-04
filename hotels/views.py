from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from audit.service import record
from reservations import services as rs

from .models import Formula, Hotel, Membership, Room, RoomType
from .permissions import perm_required
from .utils import compress_image, to_int


@perm_required("dashboard.view")
def dashboard(request):
    from billing.models import Payment
    from frontdesk.models import Expense, Shift
    from kitchen.models import Order
    from reservations.models import Reservation

    hotel = request.hotel
    now = timezone.now()
    today = timezone.localdate()
    try:
        d1 = datetime.strptime(request.GET.get("from", ""), "%Y-%m-%d").date()
    except ValueError:
        d1 = today
    try:
        d2 = datetime.strptime(request.GET.get("to", ""), "%Y-%m-%d").date()
    except ValueError:
        d2 = today
    period_start = timezone.make_aware(datetime.combine(d1, datetime.min.time()))
    period_end = timezone.make_aware(datetime.combine(d2, datetime.min.time())) + timedelta(days=1)
    is_today_only = d1 == today and d2 == today

    rs.tick(hotel)
    tiles = rs.rooms_with_state(hotel, now)
    counts = {}
    for t in tiles:
        counts[t["state"]["code"]] = counts.get(t["state"]["code"], 0) + 1
    total_rooms = len(tiles) or 1
    occupancy = int(counts.get("occupied", 0) / total_rooms * 100)

    pays = Payment.objects.filter(invoice__hotel=hotel, created_at__gte=period_start, created_at__lt=period_end)
    revenue_period = sum((-p.amount if p.kind == Payment.Kind.REFUND else p.amount) for p in pays)

    arrivals = Reservation.objects.filter(
        hotel=hotel, start_at__gte=period_start, start_at__lt=period_end,
        status__in=[Reservation.Status.ACCEPTED, Reservation.Status.CONFIRMED],
    ).select_related("room").order_by("start_at")

    return render(request, "hotels/dashboard.html", {
        "hotel": hotel, "tiles": tiles, "counts": counts, "occupancy": occupancy, "total_rooms": total_rooms,
        "d1": d1, "d2": d2, "is_today_only": is_today_only,
        "revenue_period": revenue_period, "pay_count_period": pays.count(),
        "open_shifts": Shift.objects.filter(hotel=hotel, closed_at__isnull=True).select_related("user"),
        "pending_expenses": Expense.objects.filter(hotel=hotel, status=Expense.Status.REQUESTED).count(),
        "kitchen_open": Order.objects.filter(hotel=hotel, status__in=[
            Order.Status.REQUESTED, Order.Status.NEW, Order.Status.PREPARING, Order.Status.READY]).count(),
        "arrivals": arrivals[:15],
        "low_stock": len(__import__("stock.services", fromlist=["x"]).low_stock_items(hotel)),
        "open_tickets": __import__("frontdesk.models", fromlist=["x"]).MaintenanceTicket.objects.filter(
            hotel=hotel).exclude(status="done").count(),
        "memberships": request.memberships,
        "page": "dashboard",
    })


@perm_required("board.view")
def room_links_print(request):
    from .models import Room

    hotel = request.hotel
    base = request.build_absolute_uri(f"/h/{hotel.slug}/ma-chambre/")
    rooms = Room.objects.filter(hotel=hotel, is_active=True).order_by("number")
    links = [{"room": r, "url": f"{base}?room_number={r.number}"} for r in rooms]
    return render(request, "hotels/room_links_print.html", {"hotel": hotel, "links": links})


@perm_required("hotel.manage")
def hotel_list(request):
    return render(request, "hotels/list.html", {
        "memberships": request.memberships, "page": "hotels",
    })


@perm_required("hotel.manage")
def hotel_new(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        try:
            if len(name) < 2:
                raise ValidationError("Le nom de l'hôtel est obligatoire.")
            base_slug = slugify(name) or "hotel"
            slug = base_slug
            n = 1
            while Hotel.objects.filter(slug=slug).exists():
                n += 1
                slug = f"{base_slug}-{n}"
            hotel = Hotel.objects.create(
                name=name, slug=slug,
                tagline=request.POST.get("tagline", "").strip(),
                address=request.POST.get("address", "").strip(),
                phone=request.POST.get("phone", "").strip(),
                email=request.POST.get("email", "").strip(),
            )
            Membership.objects.create(user=request.user, hotel=hotel, role=Membership.Role.OWNER)
            record(request, "hotel.create", hotel, after={"name": hotel.name, "slug": hotel.slug}, hotel=hotel)
            request.session["hotel_id"] = hotel.id
            messages.success(
                request,
                f"« {hotel.name} » créé. Ajoutez maintenant ses chambres, types de chambre et formules "
                "tarifaires depuis l'administration (/admin/), puis affectez vos employés à cet hôtel.",
            )
            return redirect("board")
        except ValidationError as e:
            for m in e.messages:
                messages.error(request, m)
    return render(request, "hotels/new.html", {"page": "hotels"})


SETTING_FIELDS = [
    ("overrun_tolerance_minutes", "Tolérance de dépassement"),
    ("warn_before_end_minutes", "Préavis de fin de créneau"),
    ("turnaround_minutes", "Remise en état entre deux séjours"),
    ("cleaning_alert_minutes", "Alerte nettoyage trop long"),
    ("cleaning_fraud_minutes", "Alerte anti-fraude nettoyage"),
    ("unreachable_release_minutes", "Libération client injoignable"),
    ("cash_variance_alert", "Seuil d'écart de caisse"),
    ("expense_manager_threshold", "Seuil dépense gérant"),
]


@perm_required("hotel.manage")
def tariffs(request):
    """Formules tarifaires, types de chambre et chambres, sans passer par l'administration."""
    hotel = request.hotel
    if request.method == "POST":
        p = request.POST
        what = p.get("what")
        try:
            if what == "formula":
                name = p.get("name", "").strip()
                kind = p.get("kind", Formula.HOURLY)
                price = to_int(p.get("price"), -1)
                if len(name) < 2 or price < 0 or kind not in (Formula.HOURLY, Formula.WINDOW):
                    raise ValidationError("Nom, type et tarif valides obligatoires.")
                data = dict(
                    name=name, kind=kind, price=price,
                    duration_minutes=max(to_int(p.get("duration_minutes"), 60), 1),
                    start_hour=min(to_int(p.get("start_hour"), 8), 23), end_hour=min(to_int(p.get("end_hour"), 17), 23),
                    end_next_day=bool(p.get("end_next_day")), min_minutes=max(to_int(p.get("min_minutes"), 0), 0),
                    sort_order=to_int(p.get("sort_order"), 0), is_active=True,
                )
                fid = to_int(p.get("id"), 0)
                if fid:
                    f = get_object_or_404(Formula, pk=fid, hotel=hotel)
                    for k, v in data.items():
                        setattr(f, k, v)
                    f.save()
                else:
                    base = slugify(name)[:18] or "formule"
                    code, n = base, 1
                    while Formula.objects.filter(hotel=hotel, code=code).exists():
                        n += 1
                        code = f"{base}-{n}"
                    f = Formula.objects.create(hotel=hotel, code=code, **data)
                record(request, "formula.save", f, after={"name": f.name, "price": f.price}, hotel=hotel)
                messages.success(request, f"Formule « {f.name} » enregistrée. Les réservations existantes gardent leur prix.")
            elif what == "formula_toggle":
                f = get_object_or_404(Formula, pk=to_int(p.get("id")), hotel=hotel)
                f.is_active = not f.is_active
                f.save(update_fields=["is_active"])
                record(request, "formula.toggle", f, after={"active": f.is_active}, hotel=hotel)
            elif what == "type":
                name = p.get("name", "").strip()
                if len(name) < 2:
                    raise ValidationError("Nom du type de chambre obligatoire.")
                tid = to_int(p.get("id"), 0)
                cap = max(to_int(p.get("capacity"), 2), 1)
                if tid:
                    t = get_object_or_404(RoomType, pk=tid, hotel=hotel)
                    t.name, t.capacity = name, cap
                    t.save()
                else:
                    RoomType.objects.create(hotel=hotel, name=name, capacity=cap)
                messages.success(request, "Type de chambre enregistré.")
            elif what == "room":
                number = p.get("number", "").strip()
                t = get_object_or_404(RoomType, pk=to_int(p.get("room_type")), hotel=hotel)
                if not number:
                    raise ValidationError("Numéro de chambre obligatoire.")
                if Room.objects.filter(hotel=hotel, number=number).exists():
                    raise ValidationError("Ce numéro de chambre existe déjà.")
                r = Room.objects.create(hotel=hotel, number=number, room_type=t, floor=p.get("floor", "").strip())
                record(request, "room.create", r, after={"number": number}, hotel=hotel)
                messages.success(request, f"Chambre {number} ajoutée.")
            elif what == "room_toggle":
                r = get_object_or_404(Room, pk=to_int(p.get("id")), hotel=hotel)
                r.is_active = not r.is_active
                r.save(update_fields=["is_active"])
                record(request, "room.toggle", r, after={"active": r.is_active}, hotel=hotel)
        except ValidationError as e:
            for m in e.messages:
                messages.error(request, m)
        return redirect("tariffs")
    return render(request, "hotels/tariffs.html", {
        "formulas": Formula.objects.filter(hotel=hotel).order_by("sort_order", "price"),
        "types": RoomType.objects.filter(hotel=hotel), "rooms": Room.objects.filter(hotel=hotel).select_related("room_type"),
        "page": "hotels",
    })


@perm_required("hotel.manage")
def hotel_settings(request):
    hotel = request.hotel
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        color = request.POST.get("theme_color", "").strip()
        try:
            if len(name) < 2:
                raise ValidationError("Le nom de l'hôtel est obligatoire.")
            if color and (len(color) != 7 or not color.startswith("#")):
                raise ValidationError("Couleur invalide (format #RRGGBB).")
            before = {"name": hotel.name, "theme_color": hotel.theme_color}
            hotel.name = name
            hotel.tagline = request.POST.get("tagline", "").strip()
            hotel.address = request.POST.get("address", "").strip()
            hotel.phone = request.POST.get("phone", "").strip()
            hotel.email = request.POST.get("email", "").strip()
            hotel.theme_color = color
            for field, label in SETTING_FIELDS:
                if field in request.POST:
                    value = to_int(request.POST.get(field), getattr(hotel, field))
                    if value < 0:
                        raise ValidationError(f"{label} : valeur invalide.")
                    setattr(hotel, field, value)
            if request.POST.get("remove_logo"):
                hotel.logo = None
            if request.FILES.get("logo"):
                hotel.logo = compress_image(request.FILES["logo"], max_side=600, quality=85)
            hotel.save()
            record(request, "hotel.settings.update", hotel, before=before,
                   after={"name": hotel.name, "theme_color": hotel.theme_color}, hotel=hotel)
            messages.success(request, "Paramètres de l'hôtel mis à jour.")
            return redirect("hotel_settings")
        except ValidationError as e:
            for m in e.messages:
                messages.error(request, m)
    return render(request, "hotels/settings.html", {
        "hotel": hotel, "page": "hotels",
        "rules": [(f, l, getattr(hotel, f)) for f, l in SETTING_FIELDS]})


@perm_required("hotel.manage")
def team(request):
    hotel = request.hotel
    if request.method == "POST":
        U = get_user_model()
        username = request.POST.get("username", "").strip()
        role = request.POST.get("role", "")
        try:
            if len(username) < 3:
                raise ValidationError("Identifiant trop court (3 caractères minimum).")
            if role not in dict(Membership.Role.choices):
                raise ValidationError("Rôle invalide.")
            user = U.objects.filter(username=username).first()
            if user is None:
                password = request.POST.get("password", "")
                if len(password) < 6:
                    raise ValidationError("Mot de passe trop court (6 caractères minimum) pour un nouvel utilisateur.")
                user = U.objects.create(username=username, first_name=request.POST.get("first_name", "").strip())
                user.set_password(password)
                user.save()
            if Membership.objects.filter(user=user, hotel=hotel).exists():
                raise ValidationError("Cette personne a déjà un accès à cet hôtel.")
            Membership.objects.create(user=user, hotel=hotel, role=role)
            record(request, "team.add", user, after={"role": role}, hotel=hotel)
            messages.success(request, f"{username} ajouté à l'équipe.")
            return redirect("team")
        except ValidationError as e:
            for m in e.messages:
                messages.error(request, m)
    members = Membership.objects.filter(hotel=hotel).select_related("user").order_by("-is_active", "role")
    return render(request, "hotels/team.html", {
        "members": members, "roles": Membership.Role.choices, "page": "hotels",
    })


@perm_required("hotel.manage")
@require_POST
def team_action(request, pk, action):
    m = get_object_or_404(Membership, pk=pk, hotel=request.hotel)
    if action == "toggle":
        if m.user_id == request.user.id and m.is_active:
            messages.error(request, "Vous ne pouvez pas désactiver votre propre accès.")
        else:
            m.is_active = not m.is_active
            m.save(update_fields=["is_active"])
            record(request, "team.toggle", m.user, after={"is_active": m.is_active}, hotel=request.hotel)
    elif action == "role":
        role = request.POST.get("role", "")
        if role in dict(Membership.Role.choices):
            m.role = role
            m.save(update_fields=["role"])
            record(request, "team.role", m.user, after={"role": role}, hotel=request.hotel)
    return redirect("team")
