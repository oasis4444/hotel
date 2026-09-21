from django.conf import settings

from .permissions import PermProxy


def hotel_context(request):
    hotel = getattr(request, "hotel", None)
    membership = getattr(request, "membership", None)
    user = getattr(request, "user", None)
    open_shift = None
    if hotel and user is not None and user.is_authenticated:
        from frontdesk.models import Shift

        open_shift = Shift.objects.filter(hotel=hotel, user=user, closed_at__isnull=True).first()
    return {
        "current_hotel": hotel,
        "membership": membership,
        "memberships": getattr(request, "memberships", []),
        "hperm": PermProxy(membership),
        "APP_NAME": settings.APP_NAME,
        "open_shift": open_shift,
    }
