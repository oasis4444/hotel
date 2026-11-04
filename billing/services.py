from django.core.exceptions import ValidationError
from django.db import transaction

from audit.service import record
from frontdesk.models import Shift
from reservations.models import Reservation, Sequence

from .models import Invoice, InvoiceLine, Payment


def current_shift(request):
    return Shift.objects.filter(hotel=request.hotel, user=request.user, closed_at__isnull=True).first()


def _room_label(res):
    label = f"Chambre {res.room.number} · {res.formula.name}"
    return f"{label} × {res.nights} nuits" if res.nights > 1 else label


@transaction.atomic
def ensure_invoice(request, res):
    try:
        return res.invoice
    except Invoice.DoesNotExist:
        pass
    number = f"F{Sequence.next(res.hotel, 'invoice'):06d}"
    user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    inv = Invoice.objects.create(hotel=res.hotel, number=number, reservation=res, customer_name=res.guest_name, created_by=user)
    add_line(request, inv, "room", _room_label(res), res.nights, res.formula.price)
    record(request, "invoice.create", inv, hotel=res.hotel)
    return inv


@transaction.atomic
def ensure_order_invoice(request, order):
    """Addition de table : facture autonome (restaurant/bar) non liée à un séjour."""
    try:
        return order.order_invoice
    except Invoice.DoesNotExist:
        pass
    number = f"F{Sequence.next(order.hotel, 'invoice'):06d}"
    user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    label = f"Table {order.table.name}" if order.table else (order.guest_name or f"Commande #{order.pk}")
    inv = Invoice.objects.create(hotel=order.hotel, number=number, order=order, customer_name=label, created_by=user)
    for line in order.lines.all():
        add_line(request, inv, "meal", f"Restauration · {line.dish_name}", line.quantity, line.unit_price)
    record(request, "invoice.create", inv, hotel=order.hotel)
    return inv


def sync_room_line(request, res):
    line = res.invoice.lines.filter(kind="room", is_cancelled=False).first()
    if line:
        cancel_line(request, line, "Changement de formule")
    add_line(request, res.invoice, "room", _room_label(res), res.nights, res.formula.price)


def add_line(request, invoice, kind, description, qty, unit_price):
    if not description.strip():
        raise ValidationError("La description est obligatoire.")
    if qty < 1 or unit_price < 0:
        raise ValidationError("Quantité ou prix invalide.")
    user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    line = InvoiceLine.objects.create(invoice=invoice, kind=kind, description=description.strip(), quantity=qty, unit_price=unit_price, created_by=user)
    record(request, "invoice.line.add", invoice, after={"line": line.description, "amount": line.amount}, hotel=invoice.hotel)
    return line


def cancel_line(request, line, reason):
    if len((reason or "").strip()) < 5:
        raise ValidationError("Motif obligatoire (5 caractères minimum). Une ligne ne se supprime jamais, elle s'annule.")
    if line.is_cancelled:
        raise ValidationError("Ligne déjà annulée.")
    line.is_cancelled = True
    line.cancel_reason = reason.strip()
    line.save(update_fields=["is_cancelled", "cancel_reason"])
    record(request, "invoice.line.cancel", line.invoice, before={"line": line.description, "amount": line.amount}, reason=reason, hotel=line.invoice.hotel)


def line_allocations(invoice):
    """Répartit le net encaissé sur les lignes dans l'ordre : statut par ligne, calculé en temps réel."""
    remaining = max(invoice.net_paid, 0) + invoice.discount
    rows = []
    for line in invoice.lines.all():
        if line.is_cancelled:
            rows.append({"line": line, "status": "cancelled", "paid": 0})
            continue
        paid = min(remaining, line.amount)
        remaining -= paid
        status = "paid" if paid >= line.amount else ("partial" if paid > 0 else "unpaid")
        rows.append({"line": line, "status": status, "paid": paid})
    return rows


@transaction.atomic
def add_payment(request, invoice, method, amount, reference="", payer_phone=""):
    if method not in dict(Payment.Method.choices):
        raise ValidationError("Mode de paiement non accepté (espèces, MVola, Orange Money, Airtel Money).")
    if amount <= 0:
        raise ValidationError("Le montant doit être supérieur à zéro.")
    if invoice.is_cancelled:
        raise ValidationError("Cette facture est annulée.")
    if method != Payment.Method.CASH and not reference.strip():
        raise ValidationError("La référence de transaction est obligatoire pour le mobile money.")
    if amount > invoice.balance:
        raise ValidationError(f"Le montant dépasse le solde restant ({invoice.balance} Ar).")
    shift = current_shift(request)
    if shift is None:
        raise ValidationError("Ouvrez votre quart avant d'encaisser : chaque paiement est rattaché à un quart.")
    res = invoice.reservation
    kind = Payment.Kind.DEPOSIT if res and res.status != Reservation.Status.CHECKED_IN else Payment.Kind.PAYMENT
    number = f"P{Sequence.next(invoice.hotel, 'receipt'):06d}"
    pay = Payment.objects.create(
        invoice=invoice, receipt_number=number, method=method, kind=kind, amount=amount,
        reference=reference.strip(), payer_phone=payer_phone.strip(), shift=shift, user=request.user,
    )
    record(request, "payment.add", invoice, after={"receipt": number, "method": method, "amount": amount}, hotel=invoice.hotel)
    return pay


@transaction.atomic
def refund(request, invoice, method, amount, reason):
    if method not in dict(Payment.Method.choices):
        raise ValidationError("Mode de remboursement invalide.")
    if len((reason or "").strip()) < 5:
        raise ValidationError("Motif obligatoire pour un remboursement.")
    if amount <= 0 or amount > invoice.net_paid:
        raise ValidationError(f"Montant invalide (maximum remboursable : {invoice.net_paid} Ar).")
    shift = current_shift(request)
    if shift is None:
        raise ValidationError("Ouvrez un quart pour enregistrer un remboursement.")
    number = f"P{Sequence.next(invoice.hotel, 'receipt'):06d}"
    pay = Payment.objects.create(
        invoice=invoice, receipt_number=number, method=method, kind=Payment.Kind.REFUND, amount=amount,
        reason=reason.strip(), shift=shift, user=request.user, approved_by=request.user,
    )
    record(request, "payment.refund", invoice, after={"receipt": number, "method": method, "amount": amount}, reason=reason, hotel=invoice.hotel)
    return pay


@transaction.atomic
def apply_discount(request, invoice, value, unit, reason, auth_username="", auth_password=""):
    """Remise sur facture : motif obligatoire + autorisation d'un gérant/propriétaire."""
    from django.contrib.auth import authenticate

    from hotels.models import Membership
    from hotels.permissions import has_perm

    if invoice.is_cancelled:
        raise ValidationError("Cette facture est annulée.")
    if len((reason or "").strip()) < 5:
        raise ValidationError("Le motif de la remise est obligatoire (5 caractères minimum).")
    if value <= 0:
        raise ValidationError("Indiquez le montant ou le pourcentage de la remise.")
    subtotal = invoice.subtotal
    amount = round(subtotal * value / 100) if unit == "percent" else value
    if unit == "percent" and value > 100:
        raise ValidationError("Une remise ne peut pas dépasser 100 %.")
    if amount > subtotal:
        raise ValidationError("La remise dépasse le total de la facture.")
    if subtotal - amount < invoice.net_paid:
        raise ValidationError("Cette remise ferait passer le total sous le montant déjà encaissé : remboursez d'abord la différence.")
    approver = None
    if has_perm(getattr(request, "membership", None), "billing.discount"):
        approver = request.user
    else:
        cand = authenticate(request, username=(auth_username or "").strip(), password=auth_password or "")
        m = Membership.objects.filter(user=cand, hotel=invoice.hotel, is_active=True).first() if cand else None
        if not m or not has_perm(m, "billing.discount"):
            raise ValidationError("Autorisation refusée : un gérant ou le propriétaire doit saisir son identifiant et son mot de passe.")
        approver = cand
    before = {"discount": invoice.discount}
    invoice.discount, invoice.discount_reason, invoice.discount_by = amount, reason.strip(), approver
    invoice.save(update_fields=["discount", "discount_reason", "discount_by"])
    record(request, "invoice.discount", invoice, before=before, after={"discount": amount, "authorized_by": str(approver)},
           reason=reason, hotel=invoice.hotel)
    return amount
