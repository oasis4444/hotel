from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.service import record
from hotels.models import Room

from .models import CleaningTask, Expense, MaintenanceTicket, Remittance, Shift


# ------------------------------------------------------------- quarts
@transaction.atomic
def open_shift(request, opening_float):
    if Shift.objects.filter(hotel=request.hotel, user=request.user, closed_at__isnull=True).exists():
        raise ValidationError("Vous avez déjà un quart ouvert.")
    prev = (
        Shift.objects.filter(hotel=request.hotel, closed_at__isnull=False, incoming_signed_at__isnull=True)
        .order_by("-closed_at").first()
    )
    shift = Shift.objects.create(hotel=request.hotel, user=request.user, opening_float=opening_float)
    if prev is not None:
        prev.incoming_user = request.user
        prev.incoming_signed_at = timezone.now()
        prev.save(update_fields=["incoming_user", "incoming_signed_at"])
        shift.previous = prev
        shift.save(update_fields=["previous"])
        left = prev.declared_cash or 0
        if abs(opening_float - left) > 0:
            record(request, "anomaly.handover_cash_mismatch", shift,
                   before={"declared_by_outgoing": left}, after={"counted_by_incoming": opening_float},
                   reason="Écart entre la caisse laissée et la caisse reprise")
    record(request, "shift.open", shift, after={"opening_float": opening_float})
    return shift


@transaction.atomic
def close_shift(request, shift, declared_cash, variance_note="", handover_note=""):
    if not shift.is_open:
        raise ValidationError("Ce quart est déjà clôturé.")
    expected = shift.expected_cash()
    variance = declared_cash - expected
    if variance != 0 and len(variance_note.strip()) < 5:
        raise ValidationError("Un écart de caisse doit être justifié.")
    shift.declared_cash = declared_cash
    shift.expected_cash_at_close = expected
    shift.variance = variance
    shift.variance_note = variance_note.strip()
    shift.handover_note = handover_note
    shift.closed_at = timezone.now()
    shift.save()
    record(request, "shift.close", shift, after={"declared": declared_cash, "expected": expected, "variance": variance}, reason=variance_note)
    if abs(variance) > shift.hotel.cash_variance_alert:
        record(request, "anomaly.cash_variance", shift, after={"variance": variance}, reason=variance_note)
    return variance


@transaction.atomic
def create_remittance(request, shift, kept_float):
    if shift.is_open:
        raise ValidationError("Clôturez le quart avant de faire le versement.")
    if hasattr(shift, "remittance"):
        raise ValidationError("Le versement de ce quart existe déjà.")
    cash = (shift.declared_cash or 0) - kept_float
    if cash < 0:
        raise ValidationError("Le fond conservé dépasse la caisse déclarée.")
    t = shift.totals_by_method()
    rem = Remittance.objects.create(
        hotel=shift.hotel, shift=shift, user=request.user, cash_amount=cash, kept_float=kept_float,
        mvola_total=t["mvola"], orange_total=t["orange"], airtel_total=t["airtel"],
    )
    record(request, "remittance.create", rem, after={"cash": cash, "kept": kept_float})
    return rem


def confirm_remittance(request, rem, received_cash, mobile_note=""):
    if rem.confirmed_at:
        raise ValidationError("Déjà confirmé.")
    rem.received_cash = received_cash
    rem.confirmed_by = request.user
    rem.confirmed_at = timezone.now()
    rem.mobile_check_note = mobile_note[:300]
    rem.save()
    record(request, "remittance.confirm", rem, after={"received": received_cash, "variance": rem.variance})
    if rem.variance:
        record(request, "anomaly.remittance_variance", rem, after={"variance": rem.variance})


# ------------------------------------------------------------- dépenses
def expense_request(request, description, category, estimated):
    if not description.strip() or estimated <= 0:
        raise ValidationError("Décrivez la dépense et indiquez un montant estimé.")
    exp = Expense.objects.create(hotel=request.hotel, requester=request.user, description=description.strip(), category=category, estimated_amount=estimated)
    record(request, "expense.request", exp, after={"estimated": estimated})
    return exp


def expense_decide(request, exp, accept, reason=""):
    from hotels.permissions import has_perm

    if exp.status != Expense.Status.REQUESTED:
        raise ValidationError("Cette demande a déjà été traitée.")
    if exp.requester_id == request.user.id:
        raise ValidationError("Vous ne pouvez pas valider votre propre dépense.")
    if accept and exp.estimated_amount > request.hotel.expense_manager_threshold and request.membership.role not in ("manager", "owner"):
        raise ValidationError("Ce montant dépasse le seuil : la validation du gérant est requise.")
    if not accept and len(reason.strip()) < 3:
        raise ValidationError("Indiquez le motif du refus.")
    exp.status = Expense.Status.ACCEPTED if accept else Expense.Status.REJECTED
    exp.decided_by = request.user
    exp.decided_at = timezone.now()
    exp.decision_reason = reason.strip()
    exp.save()
    record(request, "expense.accept" if accept else "expense.reject", exp, reason=reason)


@transaction.atomic
def expense_advance(request, exp, amount):
    if exp.status != Expense.Status.ACCEPTED:
        raise ValidationError("La dépense doit d'abord être acceptée.")
    shift = Shift.objects.filter(hotel=request.hotel, user=request.user, closed_at__isnull=True).first()
    if shift is None:
        raise ValidationError("Ouvrez votre quart pour sortir de l'argent de la caisse.")
    if amount <= 0:
        raise ValidationError("Montant invalide.")
    if Expense.objects.filter(hotel=request.hotel, requester=exp.requester, status=Expense.Status.ADVANCED).exists():
        raise ValidationError("Cette personne a une avance non justifiée : elle bloque toute nouvelle avance.")
    if amount > shift.expected_cash() and request.membership.role not in ("manager", "owner"):
        raise ValidationError("La dépense dépasse le solde de caisse (dérogation du gérant requise).")
    exp.advance_amount = amount
    exp.advance_shift = shift
    exp.advanced_at = timezone.now()
    exp.status = Expense.Status.ADVANCED
    exp.save()
    record(request, "expense.advance", exp, after={"amount": amount, "shift": shift.pk})


@transaction.atomic
def expense_settle(request, exp, actual, photo):
    if exp.status != Expense.Status.ADVANCED:
        raise ValidationError("Aucune avance à justifier.")
    if actual > exp.advance_amount:
        raise ValidationError("Le montant réel dépasse l'avance : enregistrez une nouvelle avance pour le complément.")
    shift = Shift.objects.filter(hotel=request.hotel, user=request.user, closed_at__isnull=True).first()
    if shift is None:
        raise ValidationError("Ouvrez votre quart pour récupérer la monnaie.")
    if actual >= request.hotel.expense_manager_threshold // 4 and not photo and not exp.receipt_photo:
        raise ValidationError("La photo du justificatif est obligatoire pour ce montant.")
    exp.actual_amount = actual
    exp.change_returned = exp.advance_amount - actual
    exp.settle_shift = shift
    exp.settled_at = timezone.now()
    exp.status = Expense.Status.SETTLED
    if photo:
        exp.receipt_photo = photo
    exp.save()
    record(request, "expense.settle", exp, after={"actual": actual, "change": exp.change_returned})
    if exp.estimated_amount and actual > exp.estimated_amount * 1.3:
        record(request, "anomaly.expense_above_estimate", exp, after={"estimated": exp.estimated_amount, "actual": actual})


# ------------------------------------------------------------- ménage
def cleaning_start(request, task):
    if task.status != CleaningTask.Status.TODO:
        raise ValidationError("Cette tâche est déjà commencée.")
    task.status = CleaningTask.Status.IN_PROGRESS
    task.started_at = timezone.now()
    task.worked_by = request.user
    task.save()
    task.room.set_status(Room.Status.CLEANING)
    record(request, "cleaning.start", task)


def cleaning_done(request, task):
    if task.status != CleaningTask.Status.IN_PROGRESS:
        raise ValidationError("Démarrez d'abord le nettoyage.")
    task.status = CleaningTask.Status.DONE
    task.done_at = timezone.now()
    task.save()
    record(request, "cleaning.done", task)


def cleaning_verify(request, task):
    if task.status != CleaningTask.Status.DONE:
        raise ValidationError("Le nettoyage n'est pas terminé.")
    now = timezone.now()
    task.status = CleaningTask.Status.VERIFIED
    task.verified_at = now
    task.verified_by = request.user
    task.save()
    task.room.set_status(Room.Status.AVAILABLE)  # « Prête » : redevient Disponible automatiquement
    record(request, "cleaning.verify", task, after={"minutes_to_ready": task.minutes_to_ready})
    if task.minutes_to_ready is not None and task.minutes_to_ready < task.hotel.cleaning_fraud_minutes:
        record(request, "anomaly.cleaning_too_fast", task,
               reason=f"Chambre prête {task.minutes_to_ready} min après le check-out (seuil {task.hotel.cleaning_fraud_minutes} min)")


# ------------------------------------------------------------- maintenance
@transaction.atomic
def ticket_create(request, title, description, priority, room=None, block_room=False):
    if not title.strip():
        raise ValidationError("Décrivez le problème.")
    ticket = MaintenanceTicket.objects.create(
        hotel=request.hotel, room=room, title=title.strip()[:160], description=description.strip(),
        priority=priority if priority in dict(MaintenanceTicket.Priority.choices) else MaintenanceTicket.Priority.NORMAL,
        block_room=block_room, reported_by=request.user,
    )
    if block_room and room is not None:
        room.set_status(Room.Status.OUT_OF_SERVICE)
    record(request, "maintenance.create", ticket, after={"title": ticket.title, "priority": ticket.priority})
    return ticket


def ticket_start(request, ticket):
    if ticket.status != MaintenanceTicket.Status.OPEN:
        raise ValidationError("Ce ticket est déjà pris en charge.")
    ticket.status = MaintenanceTicket.Status.IN_PROGRESS
    ticket.started_at = timezone.now()
    ticket.assigned_to = request.user
    ticket.save(update_fields=["status", "started_at", "assigned_to"])
    record(request, "maintenance.start", ticket)


@transaction.atomic
def ticket_done(request, ticket, cost=None, reopen_room=False):
    if ticket.status == MaintenanceTicket.Status.DONE:
        raise ValidationError("Ce ticket est déjà résolu.")
    ticket.status = MaintenanceTicket.Status.DONE
    ticket.done_at = timezone.now()
    ticket.cost = cost
    ticket.save(update_fields=["status", "done_at", "cost"])
    if reopen_room and ticket.room is not None and ticket.room.status == Room.Status.OUT_OF_SERVICE:
        ticket.room.set_status(Room.Status.DIRTY)  # repasse par le ménage avant d'être revendue
    record(request, "maintenance.done", ticket, after={"cost": cost})
