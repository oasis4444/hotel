from django.utils import timezone
from django.utils.dateparse import parse_datetime


def parse_local(value):
    """'2026-09-19T14:30' (saisie datetime-local) -> datetime aware, ou None."""
    if not value:
        return None
    dt = parse_datetime(value)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def to_int(value, default=0):
    try:
        return int(str(value).replace(" ", "").replace("\u202f", "") or default)
    except (TypeError, ValueError):
        return default


def darken_hex(hex_color, factor=0.82):
    """'#0B7A86' -> version l\u00e9g\u00e8rement plus sombre (survol/accent), m\u00eame format."""
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return hex_color
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return hex_color
    r, g, b = (max(0, int(c * factor)) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"
