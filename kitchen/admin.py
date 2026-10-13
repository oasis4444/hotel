from django.contrib import admin

from .models import Dish, Order, OrderLine


@admin.register(Dish)
class DishAdmin(admin.ModelAdmin):
    list_display = ("name", "hotel", "category", "price", "is_available")
    list_filter = ("hotel", "category", "is_available")
    search_fields = ("name",)


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "hotel", "room", "status", "created_at")
    list_filter = ("hotel", "status")
    inlines = [OrderLineInline]
