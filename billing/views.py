import csv
from collections import defaultdict
from datetime import datetime, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import AuditLog
from hotels.permissions import has_perm, perm_required
from hotels.utils import to_int
from reservations.models import Extension, Reservation

from . import services as bs
from .models import Invoice, InvoiceLine, Payment


def _fail(request, exc):
    for m in getattr(exc, "messages", [str(exc)]):
        messages.error(request, m)


@perm_required("billing.view")
def invoice_list(request):
    qs = Invoice.objects.filter(hotel=request.hotel).prefetch_related("lines", "payments")
    q = request.GET.get("q", "").strip()
    st = request.GET.get("s", "")
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(customer_name__icontains=q))
    items = list(qs[:400])
    due = request.GET.get("due") == "1"
    if due:
        items = [i for i in items if not i.is_cancelled and i.balance > 0]
        items.sort(key=lambda i: -i.balance)
    elif st:
        items = [i for i in items if i.status == st]
    return render(request, "billing/list.html", {
        "items": items, "due": due, "due_total": sum(i.balance for i in items) if due else 0, "q": q, "s": st, "statuses": Invoice.STATUS_LABELS.items(), "page": "invoices"})


@perm_required("billing.view")
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("reservation", "reservation__room"), pk=pk, hotel=request.hotel)
    return render(request, "billing/detail.html", {
        "inv": inv, "allocs": bs.line_allocations(inv), "payments": inv.payments.select_related("user", "shift"),
        "methods": Payment.Method.choices, "can_discount": has_perm(request.membership, "billing.discount"), "kinds": InvoiceLine.Kind.choices, "shift": bs.current_shift(request), "page": "invoices"})


@perm_required("billing.view")
@require_POST
def invoice_action(request, pk, action):
    inv = get_object_or_404(Invoice, pk=pk, hotel=request.hotel)
    p = request.POST
    m = request.membership
    try:
        if action == "pay":
            if not has_perm(m, "billing.pay"):
                raise ValidationError("Encaissement réservé à la réception.")
            pay = bs.add_payment(request, inv, p.get("method", ""), to_int(p.get("amount")), p.get("reference", ""), p.get("payer_phone", ""))
            messages.success(request, f"Paiement enregistré, reçu {pay.receipt_number}.")
        elif action == "line":
            if not has_perm(m, "billing.pay"):
                raise ValidationError("Ajout de ligne réservé à la réception.")
            bs.add_line(request, inv, p.get("kind", "other"), p.get("description", ""), max(to_int(p.get("quantity"), 1), 1), to_int(p.get("unit_price")))
            messages.success(request, "Ligne ajoutée au folio (non payée).")
        elif action == "cancel_line":
            if not has_perm(m, "billing.pay"):
                raise ValidationError("Annulation réservée à la réception.")
            line = get_object_or_404(InvoiceLine, pk=p.get("line"), invoice=inv)
            bs.cancel_line(request, line, p.get("reason", ""))
            messages.success(request, "Ligne annulée (elle reste visible et tracée).")
        elif action == "discount":
            if not has_perm(m, "billing.pay"):
                raise ValidationError("Remise réservée à la réception, avec autorisation du gérant.")
            amt = bs.apply_discount(request, inv, to_int(p.get("value")), p.get("unit", "ar"), p.get("reason", ""),
                                    p.get("auth_username", ""), p.get("auth_password", ""))
            messages.success(request, f"Remise de {amt} Ar appliquée.")
        elif action == "refund":
            if not has_perm(m, "billing.refund"):
                raise ValidationError("Le remboursement doit être validé par le gérant.")
            bs.refund(request, inv, p.get("method", ""), to_int(p.get("amount")), p.get("reason", ""))
            messages.success(request, "Remboursement enregistré.")
    except ValidationError as e:
        _fail(request, e)
    return redirect("invoice_detail", pk=inv.pk)


@perm_required("billing.view")
def invoice_print(request, pk):
    inv = get_object_or_404(Invoice, pk=pk, hotel=request.hotel)
    return render(request, "billing/print.html", {"inv": inv, "allocs": bs.line_allocations(inv), "payments": inv.payments.all()})


@perm_required("reports.view")
def reports(request):
    hotel = request.hotel
    today = timezone.localdate()
    try:
        d1 = datetime.strptime(request.GET.get("from", ""), "%Y-%m-%d").date()
    except ValueError:
        d1 = today - timedelta(days=29)
    try:
        d2 = datetime.strptime(request.GET.get("to", ""), "%Y-%m-%d").date()
    except ValueError:
        d2 = today
    start = timezone.make_aware(datetime.combine(d1, datetime.min.time()))
    end = timezone.make_aware(datetime.combine(d2, datetime.min.time())) + timedelta(days=1)

    pays = list(Payment.objects.filter(invoice__hotel=hotel, created_at__gte=start, created_at__lt=end).select_related("user", "invoice"))
    if request.GET.get("export") == "csv":
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="paiements_{d1}_{d2}.csv"'
        w = csv.writer(resp, delimiter=";")
        w.writerow(["Reçu", "Date", "Facture", "Mode", "Type", "Montant", "Référence", "Par"])
        for p in pays:
            w.writerow([p.receipt_number, timezone.localtime(p.created_at).strftime("%Y-%m-%d %H:%M"), p.invoice.number,
                        p.get_method_display(), p.get_kind_display(), p.amount, p.reference, p.user])
        return resp

    by_day, by_method, by_user = defaultdict(int), defaultdict(int), defaultdict(int)
    for p in pays:
        v = -p.amount if p.kind == Payment.Kind.REFUND else p.amount
        by_day[timezone.localtime(p.created_at).date()] += v
        by_method[p.get_method_display()] += v
        by_user[str(p.user)] += v
    revenue = sum(by_day.values())

    by_formula = defaultdict(lambda: [0, 0])
    stays = Reservation.objects.filter(hotel=hotel, start_at__gte=start, start_at__lt=end).exclude(
        status__in=[Reservation.Status.CANCELLED, Reservation.Status.NO_SHOW]).select_related("formula")
    for r in stays:
        by_formula[r.formula.name][0] += 1
        by_formula[r.formula.name][1] += r.formula.price * r.nights
    cancelled = Reservation.objects.filter(hotel=hotel, status=Reservation.Status.CANCELLED, updated_at__gte=start, updated_at__lt=end).count()
    no_show = Reservation.objects.filter(hotel=hotel, status=Reservation.Status.NO_SHOW, updated_at__gte=start, updated_at__lt=end).count()
    ext = Extension.objects.filter(reservation__hotel=hotel, created_at__gte=start, created_at__lt=end).count()
    cancel_by_user = defaultdict(int)
    for a in AuditLog.objects.filter(hotel=hotel, action="reservation.cancel", created_at__gte=start, created_at__lt=end).select_related("user"):
        cancel_by_user[str(a.user or "Système")] += 1
    anomalies = AuditLog.objects.filter(hotel=hotel, action__startswith="anomaly.", created_at__gte=start, created_at__lt=end).select_related("user")[:100]

    day_rows = sorted(by_day.items())
    max_day = max([v for _, v in day_rows], default=1) or 1
    return render(request, "billing/reports.html", {
        "d1": d1, "d2": d2, "revenue": revenue,
        "day_rows": [(d, v, int(max(v, 0) / max_day * 100)) for d, v in day_rows],
        "by_method": sorted(by_method.items(), key=lambda x: -x[1]),
        "by_user": sorted(by_user.items(), key=lambda x: -x[1]),
        "by_formula": sorted(by_formula.items(), key=lambda x: -x[1][1]),
        "stay_count": stays.count(), "cancelled": cancelled, "no_show": no_show, "extensions": ext,
        "cancel_by_user": sorted(cancel_by_user.items(), key=lambda x: -x[1]),
        "anomalies": anomalies, "page": "reports",
    })


@perm_required("reports.view")
def bilan(request):
    """Dépenses et bénéfice, impayés, occupation sur la période, prix moyen par chambre."""
    from frontdesk.models import Expense
    from hotels.models import Room

    hotel = request.hotel
    today = timezone.localdate()
    try:
        d1 = datetime.strptime(request.GET.get("from", ""), "%Y-%m-%d").date()
    except ValueError:
        d1 = today.replace(day=1)
    try:
        d2 = datetime.strptime(request.GET.get("to", ""), "%Y-%m-%d").date()
    except ValueError:
        d2 = today
    start = timezone.make_aware(datetime.combine(d1, datetime.min.time()))
    end = timezone.make_aware(datetime.combine(d2, datetime.min.time())) + timedelta(days=1)

    pays = Payment.objects.filter(invoice__hotel=hotel, created_at__gte=start, created_at__lt=end)
    revenue = sum((-p.amount if p.kind == Payment.Kind.REFUND else p.amount) for p in pays)
    by_method = defaultdict(int)
    for p in pays:
        by_method[p.get_method_display()] += -p.amount if p.kind == Payment.Kind.REFUND else p.amount

    exp_by_cat = defaultdict(int)
    cat_labels = dict(Expense.Category.choices)
    exps = Expense.objects.filter(hotel=hotel, status__in=[Expense.Status.ADVANCED, Expense.Status.SETTLED],
                                  advanced_at__gte=start, advanced_at__lt=end)
    for e in exps:
        exp_by_cat[cat_labels.get(e.category, e.category)] += e.actual_amount if e.actual_amount is not None else e.advance_amount
    expenses_total = sum(exp_by_cat.values())
    profit = revenue - expenses_total

    due_invoices = [i for i in Invoice.objects.filter(hotel=hotel, is_cancelled=False).prefetch_related("lines", "payments")[:600] if i.balance > 0]
    due_invoices.sort(key=lambda i: -i.balance)

    period_hours = (end - start).total_seconds() / 3600
    rooms = list(Room.objects.filter(hotel=hotel, is_active=True).order_by("number"))
    stays = Reservation.objects.filter(hotel=hotel, start_at__lt=end, end_at__gt=start).exclude(
        status__in=[Reservation.Status.CANCELLED, Reservation.Status.NO_SHOW, Reservation.Status.PENDING]).select_related("invoice")
    per_room = {r.id: {"room": r, "stays": 0, "hours": 0.0, "revenue": 0} for r in rooms}
    for s in stays:
        row = per_room.get(s.room_id)
        if not row:
            continue
        real_end = s.actual_checkout if s.status == Reservation.Status.CHECKED_OUT and s.actual_checkout else s.end_at
        a, b = max(s.start_at, start), min(real_end, end)
        row["hours"] += max((b - a).total_seconds() / 3600, 0)
        row["stays"] += 1
        inv = getattr(s, "invoice", None)
        if inv:
            row["revenue"] += sum(l.amount for l in inv.lines.all() if not l.is_cancelled and l.kind in ("room", "extension")) - inv.discount
    rows = []
    for row in per_room.values():
        row["rate"] = int(min(row["hours"] / period_hours, 1) * 100) if period_hours else 0
        row["avg"] = int(row["revenue"] / row["stays"]) if row["stays"] else 0
        rows.append(row)
    total_hours = sum(r["hours"] for r in rows)
    occupancy = int(min(total_hours / (period_hours * len(rooms)), 1) * 100) if rooms and period_hours else 0
    total_stays = sum(r["stays"] for r in rows)
    avg_price = int(sum(r["revenue"] for r in rows) / total_stays) if total_stays else 0

    if request.GET.get("export") == "csv":
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="bilan_{d1}_{d2}.csv"'
        w = csv.writer(resp, delimiter=";")
        w.writerow(["Rubrique", "Montant (Ar)"])
        w.writerow(["Encaissé net", revenue])
        for k, v in exp_by_cat.items():
            w.writerow([f"Dépense · {k}", v])
        w.writerow(["Dépenses totales", expenses_total])
        w.writerow(["Bénéfice", profit])
        w.writerow([])
        w.writerow(["Chambre", "Séjours", "Taux d'occupation %", "Revenu chambre", "Prix moyen"])
        for r in rows:
            w.writerow([r["room"].number, r["stays"], r["rate"], r["revenue"], r["avg"]])
        return resp

    return render(request, "billing/bilan.html", {
        "d1": d1, "d2": d2, "revenue": revenue, "by_method": sorted(by_method.items(), key=lambda x: -x[1]),
        "exp_by_cat": sorted(exp_by_cat.items(), key=lambda x: -x[1]), "expenses_total": expenses_total, "profit": profit,
        "due_invoices": due_invoices[:50], "due_total": sum(i.balance for i in due_invoices), "due_count": len(due_invoices),
        "rows": rows, "occupancy": occupancy, "avg_price": avg_price, "total_stays": total_stays, "page": "bilan",
    })
