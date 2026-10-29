from django.contrib import admin

from .models import CleaningTask, Expense, MaintenanceTicket, Remittance, Shift


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("user", "hotel", "opened_at", "closed_at", "variance")
    list_filter = ("hotel",)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Remittance)
class RemittanceAdmin(admin.ModelAdmin):
    list_display = ("shift", "cash_amount", "received_cash", "confirmed_at")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("description", "requester", "estimated_amount", "actual_amount", "status")
    list_filter = ("hotel", "status")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CleaningTask)
class CleaningTaskAdmin(admin.ModelAdmin):
    list_display = ("room", "status", "urgent", "created_at", "verified_at")
    list_filter = ("hotel", "status")


@admin.register(MaintenanceTicket)
class MaintenanceTicketAdmin(admin.ModelAdmin):
    list_display = ("title", "hotel", "room", "priority", "status", "created_at")
    list_filter = ("hotel", "status", "priority")
