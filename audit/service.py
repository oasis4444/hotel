from .models import AuditLog


def _meta(request):
    if request is None:
        return None, ""
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = fwd.split(",")[0].strip() or request.META.get("REMOTE_ADDR") or None
    return ip, request.META.get("HTTP_USER_AGENT", "")[:255]


def record(request, action, obj=None, before=None, after=None, reason="", hotel=None):
    """Enregistre une action faite par un utilisateur connecté."""
    ip, device = _meta(request)
    user = getattr(request, "user", None)
    return AuditLog.objects.create(
        hotel=hotel or getattr(request, "hotel", None),
        user=user if user is not None and getattr(user, 'is_authenticated', False) else None,
        action=action,
        object_type=obj.__class__.__name__.lower() if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        object_repr=str(obj)[:200] if obj is not None else "",
        before=before,
        after=after,
        reason=(reason or "")[:300],
        ip=ip,
        device=device,
    )


def record_system(hotel, action, obj=None, before=None, after=None, reason=""):
    """Enregistre une action automatique (sans utilisateur)."""
    return AuditLog.objects.create(
        hotel=hotel,
        action=action,
        object_type=obj.__class__.__name__.lower() if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        object_repr=str(obj)[:200] if obj is not None else "",
        before=before,
        after=after,
        reason=(reason or "")[:300],
    )
