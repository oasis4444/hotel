from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from hotels.permissions import has_perm, perm_required
from reservations.models import Reservation

from . import services as ks
from .models import Dish, Order


def _fail(request, exc):
    for m in getattr(exc, "messages", [str(exc)]):
        messages.error(request, m)


@perm_required("kitchen.view")
def queue(request):
    hotel = request.hotel
    qs = Order.objects.filter(hotel=hotel).exclude(
        status__in=[Order.Status.DELIVERED, Order.Status.CANCELLED]
    ).select_related("room", "reservation").prefetch_related("lines")
    done = Order.objects.filter(hotel=hotel, status=Order.Status.DELIVERED).select_related("room")[:15]
    return render(request, "kitchen/queue.html", {
        "new": qs.filter(status=Order.Status.NEW),
        "preparing": qs.filter(status=Order.Status.PREPARING),
        "ready": qs.filter(status=Order.Status.READY),
        "done": done,
        "page": "kitchen",
    })


@perm_required("kitchen.view")
@require_POST
def order_action(request, pk, action):
    order = get_object_or_404(Order, pk=pk, hotel=request.hotel)
    m = request.membership
    try:
        if action == "prepare":
            if not has_perm(m, "kitchen.manage"):
                raise ValidationError("Réservé à la cuisine.")
            ks.advance(request, order, Order.Status.PREPARING)
        elif action == "ready":
            if not has_perm(m, "kitchen.manage"):
                raise ValidationError("Réservé à la cuisine.")
            ks.advance(request, order, Order.Status.READY)
        elif action == "deliver":
            if not has_perm(m, "order.deliver"):
                raise ValidationError("Réservé à la réception ou au personnel chargé des livraisons.")
            ks.advance(request, order, Order.Status.DELIVERED)
        elif action == "cancel":
            if not has_perm(m, "order.create") and not has_perm(m, "kitchen.manage"):
                raise ValidationError("Action non autorisée.")
            ks.cancel(request, order, request.POST.get("reason", ""))
        messages.success(request, "Commande mise à jour.")
    except ValidationError as e:
        _fail(request, e)
    return redirect("kitchen_queue")


@perm_required("order.create")
def order_new(request):
    hotel = request.hotel
    dishes = Dish.objects.filter(hotel=hotel, is_available=True)
    stays = Reservation.objects.filter(hotel=hotel, status=Reservation.Status.CHECKED_IN).select_related("room")
    if request.method == "POST":
        items = []
        for dish in dishes:
            qty = request.POST.get(f"qty_{dish.pk}", "0")
            items.append((dish.pk, qty))
        try:
            ks.create_order(
                request,
                reservation_id=request.POST.get("reservation") or None,
                guest_name=request.POST.get("guest_name", ""),
                notes=request.POST.get("notes", ""),
                items=items,
            )
            messages.success(request, "Commande envoyée en cuisine.")
            return redirect("kitchen_queue")
        except ValidationError as e:
            _fail(request, e)
    return render(request, "kitchen/order_new.html", {"dishes": dishes, "stays": stays, "page": "kitchen"})


@perm_required("kitchen.menu_view")
def menu(request):
    hotel = request.hotel
    if request.method == "POST":
        if not has_perm(request.membership, "kitchen.menu_manage"):
            messages.error(request, "Réservé à la cuisine ou à la direction.")
            return redirect("kitchen_menu")
        try:
            price = int(request.POST.get("price") or 0)
            if not request.POST.get("name", "").strip() or price <= 0:
                raise ValidationError("Nom et prix (> 0) obligatoires.")
            Dish.objects.create(
                hotel=hotel, name=request.POST.get("name").strip(),
                category=request.POST.get("category", Dish.Category.MAIN),
                price=price, description=request.POST.get("description", "").strip(),
            )
            messages.success(request, "Plat ajouté à la carte.")
        except ValidationError as e:
            _fail(request, e)
        return redirect("kitchen_menu")
    return render(request, "kitchen/menu.html", {
        "dishes": Dish.objects.filter(hotel=hotel), "categories": Dish.Category.choices, "page": "kitchen",
    })


@perm_required("kitchen.menu_view")
def menu_print(request):
    hotel = request.hotel
    dishes = list(Dish.objects.filter(hotel=hotel, is_available=True).order_by("category", "sort_order", "name"))
    grouped = []
    for cat, label in Dish.Category.choices:
        items = [d for d in dishes if d.category == cat]
        if items:
            grouped.append((label, items))
    return render(request, "kitchen/menu_print.html", {"hotel": hotel, "grouped": grouped})


@perm_required("kitchen.menu_view")
@require_POST
def menu_action(request, pk, action):
    if not has_perm(request.membership, "kitchen.menu_manage"):
        messages.error(request, "Réservé à la cuisine ou à la direction.")
        return redirect("kitchen_menu")
    dish = get_object_or_404(Dish, pk=pk, hotel=request.hotel)
    if action == "toggle":
        dish.is_available = not dish.is_available
        dish.save(update_fields=["is_available"])
    return redirect("kitchen_menu")
