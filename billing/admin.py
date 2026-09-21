from django.contrib import admin

from .models import Invoice, InvoiceLine, Payment


class LineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    can_delete = False
    readonly_fields = ("kind", "description", "quantity", "unit_price", "is_cancelled", "cancel_reason")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "hotel", "customer_name", "is_cancelled", "created_at")
    list_filter = ("hotel",)
    inlines = [LineInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("receipt_number", "invoice", "method", "kind", "amount", "created_at")
    list_filter = ("method", "kind")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
