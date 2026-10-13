from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.service import record
from billing import services as bs
from reservations.models import Reservation

from .models import Dish, Order, OrderLine


@transaction.atomic
def create_order(request, *, reservation_id=None, room=None, guest_name="", notes="", items):
    """items : liste de (dish_id, quantity). Au moins un plat disponible requis."""
    hotel = request.hotel
    reservation = None
    if reservation_id:
        reservation = Reservation.objects.filter(pk=reservation_id, hotel=hotel).first()
        if not reservation:
            raise ValidationError("Séjour introuvable.")
        if reservation.status != Reservation.Status.CHECKED_IN:
            raise ValidationError("La commande ne peut être rattachée qu'à un client actuellement en séjour.")
        room = reservation.room
        guest_name = guest_name or reservation.guest_name

    cleaned = []
    for dish_id, qty in items:
        qty = int(qty or 0)
        if qty < 1:
            continue
        dish = Dish.objects.filter(pk=dish_id, hotel=hotel, is_available=True).first()
        if not dish:
            raise ValidationError("Un des plats choisis n'est plus disponible.")
        cleaned.append((dish, qty))
    if not cleaned:
        raise ValidationError("Choisissez au moins un plat.")

    order = Order.objects.create(
        hotel=hotel, reservation=reservation, room=room, guest_name=guest_name.strip(),
        notes=notes.strip(), created_by=request.user,
    )
    for dish, qty in cleaned:
        OrderLine.objects.create(order=order, dish=dish, dish_name=dish.name, quantity=qty, unit_price=dish.price)

    if reservation is not None:
        invoice = bs.ensure_invoice(request, reservation)
        for dish, qty in cleaned:
            bs.add_line(request, invoice, "meal", f"Restauration · {dish.name}", qty, dish.price)

    record(request, "order.create", order, after={"total": order.total}, hotel=hotel)
    return order


def advance(request, order, to_status):
    order_map = {
        Order.Status.PREPARING: ("accepted_at", Order.Status.NEW),
        Order.Status.READY: ("ready_at", Order.Status.PREPARING),
        Order.Status.DELIVERED: ("delivered_at", Order.Status.READY),
    }
    if to_status not in order_map:
        raise ValidationError("Statut invalide.")
    field, required_from = order_map[to_status]
    if order.status != required_from:
        raise ValidationError("Cette commande n'est plus à cette étape.")
    order.status = to_status
    setattr(order, field, timezone.now())
    if to_status == Order.Status.DELIVERED:
        order.delivered_by = request.user
        order.save(update_fields=[field, "status", "delivered_by"])
    else:
        order.save(update_fields=[field, "status"])
    record(request, f"order.{to_status}", order, hotel=order.hotel)
    return order


@transaction.atomic
def cancel(request, order, reason):
    if len((reason or "").strip()) < 5:
        raise ValidationError("Motif obligatoire (5 caractères minimum) pour annuler une commande.")
    if order.status == Order.Status.DELIVERED:
        raise ValidationError("Une commande déjà livrée ne peut plus être annulée.")
    order.status = Order.Status.CANCELLED
    order.cancel_reason = reason.strip()
    order.save(update_fields=["status", "cancel_reason"])
    record(request, "order.cancel", order, reason=reason, hotel=order.hotel)
    # Note : si des lignes « Restauration » ont déjà été ajoutées à la facture du séjour,
    # elles doivent être annulées une à une depuis la facturation (avec motif), jamais supprimées.
    return order
