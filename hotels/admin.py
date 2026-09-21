from django.contrib import admin

from .models import Formula, Hotel, Membership, Room, RoomType


class FormulaInline(admin.TabularInline):
    model = Formula
    extra = 0


class RoomTypeInline(admin.TabularInline):
    model = RoomType
    extra = 0


@admin.register(Hotel)
class HotelAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "phone", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [RoomTypeInline, FormulaInline]
    fieldsets = (
        ("Identité", {"fields": ("name", "slug", "tagline", "address", "phone", "email", "is_active")}),
        ("Temps et alertes", {"fields": (
            "turnaround_minutes", "cleaning_alert_minutes", "cleaning_fraud_minutes",
            "warn_before_end_minutes", "overrun_tolerance_minutes",
            "unreachable_release_minutes", "online_reconfirm_minutes", "shift_start_hour")}),
        ("Caisse et dépenses", {"fields": ("cash_variance_alert", "expense_manager_threshold")}),
        ("Réservation en ligne", {"fields": ("online_max_pending_per_phone",)}),
    )


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "hotel", "role", "is_active")
    list_filter = ("hotel", "role", "is_active")


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("number", "hotel", "room_type", "floor", "status", "is_active")
    list_filter = ("hotel", "status", "room_type")


@admin.register(Formula)
class FormulaAdmin(admin.ModelAdmin):
    list_display = ("name", "hotel", "kind", "price", "is_active", "sort_order")
    list_filter = ("hotel", "is_active")
