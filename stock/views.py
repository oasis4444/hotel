from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from hotels.permissions import perm_required

from . import services as ss
from .models import PurchaseOrder, StockItem, StockMovement, Supplier


def _fail(request, exc):
    for m in getattr(exc, "messages", [str(exc)]):
        messages.error(request, m)


@perm_required("stock.view")
def items(request):
    hotel = request.hotel
    if request.method == "POST":
        try:
            if not request.POST.get("name", "").strip():
                raise ValidationError("Nom de l'article obligatoire.")
            StockItem.objects.create(
                hotel=hotel, name=request.POST["name"].strip(), unit=request.POST.get("unit", StockItem.Unit.PIECE),
                supplier_id=request.POST.get("supplier") or None,
                cost_price=int(request.POST.get("cost_price") or 0),
                low_stock_threshold=request.POST.get("low_stock_threshold") or 0,
            )
            messages.success(request, "Article ajouté au stock.")
        except (ValidationError, ValueError) as e:
            _fail(request, e if isinstance(e, ValidationError) else ValidationError("Valeurs invalides."))
        return redirect("stock_items")
    qs = StockItem.objects.filter(hotel=hotel).select_related("supplier")
    return render(request, "stock/items.html", {
        "items": qs, "low": [i for i in qs if i.is_active and i.is_low],
        "units": StockItem.Unit.choices, "kinds": StockMovement.Kind.choices,
        "suppliers": Supplier.objects.filter(hotel=hotel, is_active=True), "page": "stock",
    })


@perm_required("stock.manage")
@require_POST
def item_toggle(request, pk):
    item = get_object_or_404(StockItem, pk=pk, hotel=request.hotel)
    item.is_active = not item.is_active
    item.save(update_fields=["is_active"])
    return redirect("stock_items")


@perm_required("stock.manage")
@require_POST
def movement_create(request):
    item = get_object_or_404(StockItem, pk=request.POST.get("item"), hotel=request.hotel)
    try:
        ss.adjust_stock(request, item, request.POST.get("kind", ""), request.POST.get("quantity"), request.POST.get("reason", ""))
        messages.success(request, "Mouvement de stock enregistré.")
    except ValidationError as e:
        _fail(request, e)
    return redirect("stock_items")


@perm_required("stock.view")
def suppliers(request):
    hotel = request.hotel
    if request.method == "POST":
        if not request.POST.get("name", "").strip():
            messages.error(request, "Nom du fournisseur obligatoire.")
        else:
            Supplier.objects.create(
                hotel=hotel, name=request.POST["name"].strip(), phone=request.POST.get("phone", "").strip(),
                address=request.POST.get("address", "").strip(), notes=request.POST.get("notes", "").strip(),
            )
            messages.success(request, "Fournisseur ajouté.")
        return redirect("stock_suppliers")
    return render(request, "stock/suppliers.html", {"suppliers": Supplier.objects.filter(hotel=hotel), "page": "stock"})


@perm_required("stock.manage")
@require_POST
def supplier_toggle(request, pk):
    sup = get_object_or_404(Supplier, pk=pk, hotel=request.hotel)
    sup.is_active = not sup.is_active
    sup.save(update_fields=["is_active"])
    return redirect("stock_suppliers")


@perm_required("stock.view")
def purchases(request):
    hotel = request.hotel
    if request.method == "POST":
        po = PurchaseOrder.objects.create(
            hotel=hotel, supplier_id=request.POST.get("supplier") or None,
            created_by=request.user if request.user.is_authenticated else None,
        )
        return redirect("stock_purchase_detail", pk=po.pk)
    return render(request, "stock/purchases.html", {
        "purchases": PurchaseOrder.objects.filter(hotel=hotel).select_related("supplier").prefetch_related("lines"),
        "suppliers": Supplier.objects.filter(hotel=hotel, is_active=True), "page": "stock",
    })


@perm_required("stock.view")
def purchase_detail(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk, hotel=request.hotel)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "add_line":
                item = get_object_or_404(StockItem, pk=request.POST.get("item"), hotel=request.hotel)
                ss.add_purchase_line(request, po, item, request.POST.get("quantity"), request.POST.get("unit_cost"))
            elif action == "receive":
                ss.receive_purchase(request, po)
                messages.success(request, "Commande réceptionnée : le stock est mis à jour.")
        except ValidationError as e:
            _fail(request, e)
        return redirect("stock_purchase_detail", pk=po.pk)
    return render(request, "stock/purchase_detail.html", {
        "po": po, "items": StockItem.objects.filter(hotel=request.hotel, is_active=True), "page": "stock",
    })
