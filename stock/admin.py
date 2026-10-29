from django.contrib import admin

from .models import PurchaseLine, PurchaseOrder, StockItem, StockMovement, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "hotel", "phone", "is_active")
    list_filter = ("hotel", "is_active")


@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ("name", "hotel", "unit", "quantity", "low_stock_threshold", "cost_price", "is_active")
    list_filter = ("hotel", "unit", "is_active")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("item", "kind", "quantity", "created_at")
    list_filter = ("hotel", "kind")


class PurchaseLineInline(admin.TabularInline):
    model = PurchaseLine
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "hotel", "supplier", "status", "created_at")
    list_filter = ("hotel", "status")
    inlines = [PurchaseLineInline]
