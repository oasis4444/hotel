from .models import Membership


class HotelMiddleware:
    """Rattache chaque requête à UN hôtel actif : isolation stricte des données."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.hotel = None
        request.membership = None
        request.memberships = []
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            ms = list(
                Membership.objects.filter(user=user, is_active=True, hotel__is_active=True).select_related("hotel")
            )
            request.memberships = ms
            wanted = request.session.get("hotel_id")
            chosen = next((m for m in ms if m.hotel_id == wanted), None) or (ms[0] if ms else None)
            if chosen:
                request.membership = chosen
                request.hotel = chosen.hotel
                request.session["hotel_id"] = chosen.hotel_id
        return self.get_response(request)
