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
