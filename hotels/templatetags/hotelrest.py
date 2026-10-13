from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def ar(value):
    try:
        v = int(value or 0)
    except (TypeError, ValueError):
        return value
    s = f"{abs(v):,}".replace(",", "\u202f")
    return f"{'−' if v < 0 else ''}{s}\u00a0Ar"


@register.filter
def hm(value):
    return timezone.localtime(value).strftime("%Hh%M") if value else "—"


@register.filter
def dm(value):
    return timezone.localtime(value).strftime("%d/%m %Hh%M") if value else "—"


@register.filter
def dinput(value):
    if isinstance(value, str):
        return value
    return timezone.localtime(value).strftime("%Y-%m-%dT%H:%M") if value else ""


@register.filter
def dur(minutes):
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return "—"
    h, mm = divmod(abs(m), 60)
    return f"{h}h{mm:02d}" if h else f"{mm} min"


@register.simple_tag
def epoch(value):
    return int(value.timestamp() * 1000) if value else 0


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None
