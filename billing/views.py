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
    items = list(qs[:200])
    if st:
        items = [i for i in items if i.status == st]
    return render(request, "billing/list.html", {
        "items": items, "q": q, "s": st, "statuses": Invoice.STATUS_LABELS.items(), "page": "invoices"})


@perm_required("billing.view")
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("reservation", "reservation__room"), pk=pk, hotel=request.hotel)
    return render(request, "billing/detail.html", {
        "inv": inv, "allocs": bs.line_allocations(inv), "payments": inv.payments.select_related("user", "shift"),
        "methods": Payment.Method.choices, "kinds": InvoiceLine.Kind.choices, "shift": bs.current_shift(request), "page": "invoices"})


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
        by_formula[r.formula.name][1] += r.formula.price
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
