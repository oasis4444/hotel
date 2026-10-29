from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.service import record

from .models import PurchaseLine, PurchaseOrder, StockItem, StockMovement


@transaction.atomic
def adjust_stock(request, item, kind, quantity, reason=""):
    """Mouvement manuel : entrée, sortie, perte ou ajustement d'inventaire."""
    try:
        quantity = abs(float(quantity))
    except (TypeError, ValueError):
        raise ValidationError("Quantité invalide.")
    if quantity <= 0:
        raise ValidationError("Quantité invalide.")
    if kind not in dict(StockMovement.Kind.choices):
        raise ValidationError("Type de mouvement invalide.")
    if kind in (StockMovement.Kind.OUT, StockMovement.Kind.LOSS):
        if float(item.quantity) < quantity:
            raise ValidationError(f"Stock insuffisant pour « {item.name} » ({item.quantity} en stock).")
        item.quantity = float(item.quantity) - quantity
    elif kind == StockMovement.Kind.IN:
        item.quantity = float(item.quantity) + quantity
    elif kind == StockMovement.Kind.ADJUST:
        item.quantity = quantity
    item.save(update_fields=["quantity"])
    mv = StockMovement.objects.create(
        hotel=item.hotel, item=item, kind=kind, quantity=quantity, reason=(reason or "")[:300],
        created_by=getattr(request, "user", None) if getattr(request.user, "is_authenticated", False) else None,
    )
    record(request, f"stock.{kind}", item, after={"quantity": str(item.quantity)}, reason=reason, hotel=item.hotel)
    return mv


def consume_for_order(request, stock_item, quantity, order=None):
    """Déduction automatique via la fiche recette d'un plat. Ne bloque jamais la commande si le
    stock est insuffisant (la cuisine ne doit pas être paralysée) mais trace tout, y compris en
    négatif, pour signaler un écart à corriger lors du prochain inventaire."""
    quantity = float(quantity or 0)
    if quantity <= 0:
        return None
    stock_item.quantity = float(stock_item.quantity) - quantity
    stock_item.save(update_fields=["quantity"])
    return StockMovement.objects.create(
        hotel=stock_item.hotel, item=stock_item, kind=StockMovement.Kind.OUT, quantity=quantity,
        reason="Consommation recette", order=order,
        created_by=getattr(request, "user", None) if getattr(getattr(request, "user", None), "is_authenticated", False) else None,
    )


@transaction.atomic
def add_purchase_line(request, po, item, quantity, unit_cost):
    if po.status == PurchaseOrder.Status.RECEIVED:
        raise ValidationError("Cette commande d'achat est déjà réceptionnée.")
    try:
        quantity = float(quantity)
    except (TypeError, ValueError):
        raise ValidationError("Quantité invalide.")
    if quantity <= 0:
        raise ValidationError("Quantité invalide.")
    return PurchaseLine.objects.create(purchase=po, item=item, quantity=quantity, unit_cost=int(unit_cost or 0))


@transaction.atomic
def receive_purchase(request, po):
    if po.status == PurchaseOrder.Status.RECEIVED:
        raise ValidationError("Cette commande est déjà réceptionnée.")
    lines = list(po.lines.select_related("item"))
    if not lines:
        raise ValidationError("Ajoutez au moins une ligne avant de réceptionner.")
    for line in lines:
        item = line.item
        item.quantity = float(item.quantity) + float(line.quantity)
        if line.unit_cost:
            item.cost_price = line.unit_cost
        item.save(update_fields=["quantity", "cost_price"])
        StockMovement.objects.create(
            hotel=po.hotel, item=item, kind=StockMovement.Kind.IN, quantity=line.quantity,
            reason=f"Achat #{po.pk} · {po.supplier or 'fournisseur non renseigné'}",
            created_by=getattr(request, "user", None) if getattr(request.user, "is_authenticated", False) else None,
        )
    po.status = PurchaseOrder.Status.RECEIVED
    po.received_at = timezone.now()
    po.save(update_fields=["status", "received_at"])
    record(request, "purchase.receive", po, after={"total": po.total}, hotel=po.hotel)
    return po


def low_stock_items(hotel):
    return [i for i in StockItem.objects.filter(hotel=hotel, is_active=True) if i.is_low]
