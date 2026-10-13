from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class HotelUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Profil", {"fields": ("phone", "language")}),)
    list_display = ("username", "first_name", "last_name", "phone", "is_active")
