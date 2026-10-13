from datetime import datetime, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import AuditLog
from hotels.permissions import has_perm, perm_required
from hotels.utils import compress_image, to_int
from reservations import services as rs
from reservations.models import Reservation

from . import services as fs
from .alerts import build_alerts
from .models import CleaningTask, Expense, Remittance, Shift


def _fail(request, exc):
    for m in getattr(exc, "messages", [str(exc)]):
        messages.error(request, m)


def live_context(hotel):
    now = timezone.now()
    rs.tick(hotel)
    tiles = rs.rooms_with_state(hotel, now)
    counts = {}
    for t in tiles:
        counts[t["state"]["code"]] = counts.get(t["state"]["code"], 0) + 1
    total = len(tiles) or 1
    busy = counts.get("occupied", 0)
    alerts = build_alerts(hotel, now)
    return {
        "tiles": tiles, "counts": counts, "alerts": alerts, "occupancy": int(busy / total * 100),
        "danger_count": sum(1 for a in alerts if a["level"] == "danger"),
        "sound_keys": ",".join(a["key"] for a in alerts if a.get("sound")),
        "now": now,
    }


@perm_required("board.view")
def board(request):
    ctx = live_context(request.hotel)
    ctx["page"] = "board"
    return render(request, "frontdesk/board.html", ctx)


@perm_required("board.view")
def live(request):
    return render(request, "frontdesk/_live.html", live_context(request.hotel))


@perm_required("planning.view")
def planning(request):
    hotel = request.hotel
    try:
        day = datetime.strptime(request.GET.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        day = timezone.localdate()
    start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
    end = start + timedelta(days=1)
    total = (end - start).total_seconds()
    res = Reservation.objects.filter(hotel=hotel, start_at__lt=end, end_at__gt=start).exclude(
        status__in=[Reservation.Status.CANCELLED, Reservation.Status.NO_SHOW]).select_related("formula")
    by_room = {}
    for r in res:
        real_end = r.actual_checkout if r.status == Reservation.Status.CHECKED_OUT and r.actual_checkout else r.end_at
        s, e = max(r.start_at, start), min(real_end, end)
        if e <= s:
            continue
        by_room.setdefault(r.room_id, []).append({
            "res": r, "left": round((s - start).total_seconds() / total * 100, 2),
            "width": max(round((e - s).total_seconds() / total * 100, 2), 1.2),
        })
    from hotels.models import Room

    rows = [{"room": rm, "bars": by_room.get(rm.id, [])} for rm in Room.objects.filter(hotel=hotel, is_active=True).select_related("room_type")]
    now = timezone.now()
    now_left = round((now - start).total_seconds() / total * 100, 2) if start <= now < end else None
    return render(request, "frontdesk/planning.html", {
        "rows": rows, "day": day, "prev": day - timedelta(days=1), "next": day + timedelta(days=1),
        "hours": list(range(0, 24, 2)), "now_left": now_left, "page": "planning",
    })


# ------------------------------------------------------------- quarts
@perm_required("shift.manage")
def shift_home(request):
    hotel = request.hotel
    shift = Shift.objects.filter(hotel=hotel, user=request.user, closed_at__isnull=True).first()
    pending = Shift.objects.filter(hotel=hotel, closed_at__isnull=False, incoming_signed_at__isnull=True).order_by("-closed_at").first()
    recent = Shift.objects.filter(hotel=hotel).select_related("user")[:12]
    ctx = {"shift": shift, "pending": pending, "recent": recent, "page": "shift"}
    if shift:
        ctx["totals"] = shift.totals_by_method()
        ctx["total_all"] = sum(ctx["totals"].values())
        ctx["pay_count"] = shift.payments.count()
    return render(request, "frontdesk/shift.html", ctx)


@perm_required("shift.manage")
@require_POST
def shift_open(request):
    try:
        fs.open_shift(request, to_int(request.POST.get("opening_float")))
        messages.success(request, "Quart ouvert. Bon courage !")
    except ValidationError as e:
        _fail(request, e)
    return redirect("shift")


@perm_required("shift.manage")
def shift_close(request):
    shift = Shift.objects.filter(hotel=request.hotel, user=request.user, closed_at__isnull=True).first()
    if not shift:
        messages.error(request, "Aucun quart ouvert.")
        return redirect("shift")
    ctx = {"shift": shift, "page": "shift", "stage": "count"}
    if request.method == "POST":
        declared = to_int(request.POST.get("declared_cash"), -1)
        note = request.POST.get("variance_note", "")
        handover = request.POST.get("handover_note", "")
        if declared < 0:
            messages.error(request, "Comptez la caisse et saisissez le montant.")
        else:
            expected = shift.expected_cash()
            variance = declared - expected
            if variance != 0 and len(note.strip()) < 5:
                # Étape 2 : l'écart n'est révélé qu'après la déclaration (comptage à l'aveugle)
                ctx.update({"stage": "explain", "declared": declared, "expected": expected, "variance": variance, "handover": handover})
                return render(request, "frontdesk/shift_close.html", ctx)
            try:
                fs.close_shift(request, shift, declared, note, handover)
                messages.success(request, "Quart clôturé. Passez maintenant au versement.")
                return redirect("remittances")
            except ValidationError as e:
                _fail(request, e)
    return render(request, "frontdesk/shift_close.html", ctx)


@perm_required("remit.make")
def remittances(request):
    hotel = request.hotel
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "create":
                shift = get_object_or_404(Shift, pk=request.POST.get("shift"), hotel=hotel, user=request.user)
                fs.create_remittance(request, shift, to_int(request.POST.get("kept_float")))
                messages.success(request, "Versement enregistré. Il reste « en attente » jusqu'à la confirmation du gérant.")
            elif action == "confirm":
                if not has_perm(request.membership, "remit.confirm"):
                    raise ValidationError("Seul le gérant confirme un versement.")
                rem = get_object_or_404(Remittance, pk=request.POST.get("rem"), hotel=hotel)
                fs.confirm_remittance(request, rem, to_int(request.POST.get("received_cash")), request.POST.get("mobile_note", ""))
                messages.success(request, "Versement confirmé.")
        except ValidationError as e:
            _fail(request, e)
        return redirect("remittances")
    to_remit = Shift.objects.filter(hotel=hotel, closed_at__isnull=False, remittance__isnull=True)
    if not has_perm(request.membership, "remit.confirm"):
        to_remit = to_remit.filter(user=request.user)
    items = Remittance.objects.filter(hotel=hotel).select_related("shift", "user", "confirmed_by")[:40]
    return render(request, "frontdesk/remittances.html", {"to_remit": to_remit, "items": items, "page": "remittances"})


# ------------------------------------------------------------- dépenses
@perm_required("expense.request")
def expenses(request):
    hotel = request.hotel
    if request.method == "POST":
        try:
            fs.expense_request(request, request.POST.get("description", ""), request.POST.get("category", "other"), to_int(request.POST.get("estimated")))
            messages.success(request, "Demande envoyée à la réception.")
        except ValidationError as e:
            _fail(request, e)
        return redirect("expenses")
    qs = Expense.objects.filter(hotel=hotel).select_related("requester", "decided_by")
    if not has_perm(request.membership, "expense.approve"):
        qs = qs.filter(requester=request.user)
    unjustified = Expense.objects.filter(hotel=hotel, status=Expense.Status.ADVANCED).count()
    return render(request, "frontdesk/expenses.html", {
        "items": qs[:60], "categories": Expense.Category.choices, "unjustified": unjustified, "page": "expenses",
    })


@perm_required("expense.request")
@require_POST
def expense_action(request, pk, action):
    exp = get_object_or_404(Expense, pk=pk, hotel=request.hotel)
    m = request.membership
    try:
        if action in ("accept", "reject"):
            if not has_perm(m, "expense.approve"):
                raise ValidationError("Action réservée à la réception.")
            fs.expense_decide(request, exp, action == "accept", request.POST.get("reason", ""))
        elif action == "advance":
            if not has_perm(m, "expense.pay"):
                raise ValidationError("Action réservée à la réception.")
            fs.expense_advance(request, exp, to_int(request.POST.get("amount"), exp.estimated_amount))
        elif action == "settle":
            if not has_perm(m, "expense.pay"):
                raise ValidationError("Action réservée à la réception.")
            photo = compress_image(request.FILES.get("photo"), max_side=1600, quality=78)
            fs.expense_settle(request, exp, to_int(request.POST.get("actual")), photo)
        messages.success(request, "Dépense mise à jour.")
    except ValidationError as e:
        _fail(request, e)
    return redirect("expenses")


# ------------------------------------------------------------- ménage
@perm_required("cleaning.view")
def cleaning(request):
    hotel = request.hotel
    open_tasks = CleaningTask.objects.filter(hotel=hotel).exclude(status=CleaningTask.Status.VERIFIED).select_related("room")
    done = CleaningTask.objects.filter(hotel=hotel, status=CleaningTask.Status.VERIFIED).select_related("room", "worked_by")[:15]
    return render(request, "frontdesk/cleaning.html", {"open_tasks": open_tasks, "done": done, "page": "cleaning"})


@perm_required("cleaning.view")
@require_POST
def cleaning_action(request, pk, action):
    task = get_object_or_404(CleaningTask, pk=pk, hotel=request.hotel)
    try:
        if action in ("start", "done"):
            if not has_perm(request.membership, "cleaning.work"):
                raise ValidationError("Réservé au personnel de ménage.")
            (fs.cleaning_start if action == "start" else fs.cleaning_done)(request, task)
        elif action == "verify":
            if not has_perm(request.membership, "cleaning.verify"):
                raise ValidationError("Le contrôle est fait par la réception.")
            fs.cleaning_verify(request, task)
            messages.success(request, f"Chambre {task.room.number} prête et de nouveau disponible.")
    except ValidationError as e:
        _fail(request, e)
    return redirect("cleaning")


# ------------------------------------------------------------- audit
@perm_required("audit.view")
def audit_list(request):
    qs = AuditLog.objects.filter(hotel=request.hotel).select_related("user")
    kind = request.GET.get("k", "")
    q = request.GET.get("q", "").strip()
    if kind == "anomaly":
        qs = qs.filter(action__startswith="anomaly.")
    if q:
        qs = qs.filter(Q(action__icontains=q) | Q(object_repr__icontains=q) | Q(reason__icontains=q) | Q(user__username__icontains=q))
    return render(request, "frontdesk/audit.html", {"items": qs[:200], "kind": kind, "q": q, "page": "audit"})


def switch_hotel(request, pk):
    if any(m.hotel_id == pk for m in getattr(request, "memberships", [])):
        request.session["hotel_id"] = pk
    nxt = request.GET.get("next") or request.POST.get("next")
    if nxt == "dashboard":
        return redirect("dashboard")
    return redirect("board")
