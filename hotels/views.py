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

from .models import Hotel, Membership
from .permissions import perm_required


@perm_required("dashboard.view")
def dashboard(request):
    from billing.models import Payment
    from frontdesk.models import Expense, Shift
    from kitchen.models import Order
    from reservations.models import Reservation

    hotel = request.hotel
    now = timezone.now()
    today_start = timezone.make_aware(datetime.combine(timezone.localdate(), datetime.min.time()))
    today_end = today_start + timedelta(days=1)

    rs.tick(hotel)
    tiles = rs.rooms_with_state(hotel, now)
    counts = {}
    for t in tiles:
        counts[t["state"]["code"]] = counts.get(t["state"]["code"], 0) + 1
    total_rooms = len(tiles) or 1
    occupancy = int(counts.get("occupied", 0) / total_rooms * 100)

    pays_today = Payment.objects.filter(invoice__hotel=hotel, created_at__gte=today_start, created_at__lt=today_end)
    revenue_today = sum((-p.amount if p.kind == Payment.Kind.REFUND else p.amount) for p in pays_today)

    arrivals_today = Reservation.objects.filter(
        hotel=hotel, start_at__gte=today_start, start_at__lt=today_end,
        status__in=[Reservation.Status.ACCEPTED, Reservation.Status.CONFIRMED],
    ).select_related("room").order_by("start_at")

    return render(request, "hotels/dashboard.html", {
        "hotel": hotel, "tiles": tiles, "counts": counts, "occupancy": occupancy, "total_rooms": total_rooms,
        "revenue_today": revenue_today, "pay_count_today": pays_today.count(),
        "open_shifts": Shift.objects.filter(hotel=hotel, closed_at__isnull=True).select_related("user"),
        "pending_expenses": Expense.objects.filter(hotel=hotel, status=Expense.Status.REQUESTED).count(),
        "kitchen_open": Order.objects.filter(hotel=hotel, status__in=[Order.Status.NEW, Order.Status.PREPARING, Order.Status.READY]).count(),
        "arrivals_today": arrivals_today[:10],
        "page": "dashboard",
    })


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
            if request.POST.get("remove_logo"):
                hotel.logo = None
            if request.FILES.get("logo"):
                hotel.logo = request.FILES["logo"]
            hotel.save()
            record(request, "hotel.settings.update", hotel, before=before,
                   after={"name": hotel.name, "theme_color": hotel.theme_color}, hotel=hotel)
            messages.success(request, "Paramètres de l'hôtel mis à jour.")
            return redirect("hotel_settings")
        except ValidationError as e:
            for m in e.messages:
                messages.error(request, m)
    return render(request, "hotels/settings.html", {"hotel": hotel, "page": "hotels"})


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
