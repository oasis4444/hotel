"""Linge, planning semaine/mois et rapport journalier (main courante)."""
from collections import defaultdict
from datetime import datetime, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.service import record
from hotels.models import Room
from hotels.permissions import perm_required
from hotels.utils import to_int
from reservations.models import Reservation

from .models import Expense, LinenItem, LinenMovement


def _fail(request, exc):
    for m in getattr(exc, "messages", [str(exc)]):
        messages.error(request, m)


def _day(value, default=None):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default or timezone.localdate()


def _midnight(d):
    return timezone.make_aware(datetime.combine(d, datetime.min.time()))


# ------------------------------------------------------------------ linge
@perm_required("cleaning.view")
def linen(request):
    hotel = request.hotel
    can = request.membership.role in ("owner", "manager", "receptionist", "housekeeper")
    if request.method == "POST":
        try:
            if not can:
                raise ValidationError("Action non autorisée.")
            name = request.POST.get("name", "").strip()
            if len(name) < 2:
                raise ValidationError("Nom du linge obligatoire.")
            item, created = LinenItem.objects.get_or_create(hotel=hotel, name=name)
            qty = to_int(request.POST.get("total"), 0)
            if qty > 0:
                item.total += qty
                item.save(update_fields=["total"])
            record(request, "linen.item", item, after={"name": name, "total": item.total}, hotel=hotel)
            messages.success(request, f"« {name} » enregistré.")
        except ValidationError as e:
            _fail(request, e)
        return redirect("linen")
    items = list(LinenItem.objects.filter(hotel=hotel, is_active=True).prefetch_related("movements"))
    moves = LinenMovement.objects.filter(item__hotel=hotel).select_related("item", "room", "user")[:40]
    return render(request, "frontdesk/linen.html", {
        "items": items, "moves": moves, "kinds": LinenMovement.Kind.choices, "can": can,
        "rooms": Room.objects.filter(hotel=hotel, is_active=True).order_by("number"), "page": "linen",
        "totals": {"total": sum(i.total for i in items), "laundry": sum(i.in_laundry for i in items),
                   "lost": sum(i.lost for i in items), "ok": sum(i.available for i in items)},
    })


@perm_required("cleaning.view")
@require_POST
def linen_move(request):
    hotel = request.hotel
    p = request.POST
    try:
        if request.membership.role not in ("owner", "manager", "receptionist", "housekeeper"):
            raise ValidationError("Action non autorisée.")
        item = get_object_or_404(LinenItem, pk=to_int(p.get("item")), hotel=hotel)
        kind = p.get("kind", "")
        if kind not in dict(LinenMovement.Kind.choices):
            raise ValidationError("Type de mouvement invalide.")
        qty = to_int(p.get("quantity"), 0)
        if qty < 1:
            raise ValidationError("Indiquez une quantité.")
        if kind == "laundry_out" and qty > item.available:
            raise ValidationError(f"Seulement {item.available} « {item.name} » disponible(s).")
        if kind == "laundry_in" and qty > item.in_laundry:
            raise ValidationError(f"Seulement {item.in_laundry} « {item.name} » à la blanchisserie.")
        if kind in ("lost", "damaged") and qty > item.available:
            raise ValidationError(f"Seulement {item.available} « {item.name} » disponible(s).")
        room = Room.objects.filter(pk=to_int(p.get("room")), hotel=hotel).first()
        LinenMovement.objects.create(item=item, kind=kind, quantity=qty, room=room, note=p.get("note", "")[:200], user=request.user)
        if kind == "added":
            item.total += qty
            item.save(update_fields=["total"])
        record(request, "linen.move", item, after={"kind": kind, "qty": qty}, hotel=hotel)
        messages.success(request, "Mouvement de linge enregistré.")
    except ValidationError as e:
        _fail(request, e)
    return redirect("linen")


# ------------------------------------------------------------------ planning semaine / mois
def planning_grid(request, mode):
    hotel = request.hotel
    day = _day(request.GET.get("date"))
    if mode == "week":
        first = day - timedelta(days=day.weekday())
        n = 7
        prev, nxt = first - timedelta(days=7), first + timedelta(days=7)
    else:
        first = day.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        prev = (first - timedelta(days=1)).replace(day=1)
        n = (nxt - first).days
    days = [first + timedelta(days=i) for i in range(n)]
    start, end = _midnight(first), _midnight(first + timedelta(days=n))
    res = Reservation.objects.filter(hotel=hotel, start_at__lt=end, end_at__gt=start).exclude(
        status__in=[Reservation.Status.CANCELLED, Reservation.Status.NO_SHOW]).select_related("formula")
    cells = defaultdict(list)
    for r in res:
        real_end = r.actual_checkout if r.status == Reservation.Status.CHECKED_OUT and r.actual_checkout else r.end_at
        for i, d in enumerate(days):
            ds, de = _midnight(d), _midnight(d + timedelta(days=1))
            if r.start_at < de and real_end > ds:
                cells[(r.room_id, i)].append(r)
    rows = []
    for rm in Room.objects.filter(hotel=hotel, is_active=True).select_related("room_type"):
        rows.append({"room": rm, "cells": [cells.get((rm.id, i), []) for i in range(n)]})
    today = timezone.localdate()
    occupied = sum(1 for row in rows for c in row["cells"] if c)
    return render(request, "frontdesk/planning_grid.html", {
        "rows": rows, "days": days, "mode": mode, "first": first, "prev": prev, "next": nxt, "today": today,
        "rate": int(occupied / (len(rows) * n) * 100) if rows else 0, "page": "planning",
    })


# ------------------------------------------------------------------ rapport journalier
@perm_required("report.daily")
def daily_report(request):
    from billing.models import Payment

    hotel = request.hotel
    day = _day(request.GET.get("date"))
    start, end = _midnight(day), _midnight(day + timedelta(days=1))
    R = Reservation.Status
    arrivals = Reservation.objects.filter(hotel=hotel, actual_checkin__gte=start, actual_checkin__lt=end).select_related("room", "formula")
    departures = Reservation.objects.filter(hotel=hotel, actual_checkout__gte=start, actual_checkout__lt=end).select_related("room", "formula")
    in_house = Reservation.objects.filter(hotel=hotel, status=R.CHECKED_IN, start_at__lt=end).select_related("room", "formula")
    expected = Reservation.objects.filter(
        hotel=hotel, start_at__gte=start, start_at__lt=end, status__in=[R.ACCEPTED, R.CONFIRMED, R.PENDING]).select_related("room", "formula")
    cancelled = Reservation.objects.filter(hotel=hotel, status__in=[R.CANCELLED, R.NO_SHOW], updated_at__gte=start, updated_at__lt=end)
    pays = list(Payment.objects.filter(invoice__hotel=hotel, created_at__gte=start, created_at__lt=end).select_related("invoice", "user"))
    by_method = defaultdict(int)
    for p in pays:
        by_method[p.get_method_display()] += -p.amount if p.kind == Payment.Kind.REFUND else p.amount
    total_in = sum(by_method.values())
    exp_list = list(Expense.objects.filter(hotel=hotel, status__in=[Expense.Status.ADVANCED, Expense.Status.SETTLED],
                                           advanced_at__gte=start, advanced_at__lt=end).select_related("requester"))
    exp_total = sum((e.actual_amount if e.actual_amount is not None else e.advance_amount) for e in exp_list)
    rooms_total = Room.objects.filter(hotel=hotel, is_active=True).count()
    return render(request, "frontdesk/daily_report.html", {
        "day": day, "prev": day - timedelta(days=1), "next": day + timedelta(days=1), "hotel": hotel,
        "arrivals": arrivals, "departures": departures, "in_house": in_house, "expected": expected,
        "cancelled": cancelled, "pays": pays, "by_method": sorted(by_method.items(), key=lambda x: -x[1]),
        "total_in": total_in, "exp_list": exp_list, "exp_total": exp_total, "net": total_in - exp_total,
        "occupied": in_house.count(), "rooms_total": rooms_total, "page": "daily",
        "printed_by": request.user, "now": timezone.now(),
    })
