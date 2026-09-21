"""Moteur de disponibilité et cycle de vie des séjours."""
import math
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.service import record, record_system
from hotels.models import Formula, Room

from .models import Client, Extension, Reservation, Sequence

S = Reservation.Status
BLOCKING = [S.PENDING, S.ACCEPTED, S.CONFIRMED, S.CHECKED_IN, S.CHECKED_OUT]


def fmt(dt):
    return timezone.localtime(dt).strftime("%d/%m %Hh%M")


def turnaround(hotel):
    return timedelta(minutes=hotel.turnaround_minutes)


def busy_end(res, now):
    """Heure réelle à laquelle la chambre est libérée par ce séjour."""
    if res.status == S.CHECKED_OUT:
        return res.actual_checkout or res.end_at
    if res.status == S.CHECKED_IN:
        return max(res.end_at, now)  # un dépassement bloque la chambre
    return res.end_at


def find_conflicts(room, start, end, exclude=None, now=None):
    now = now or timezone.now()
    gap = turnaround(room.hotel)
    qs = Reservation.objects.filter(
        room=room, status__in=BLOCKING, start_at__lt=end + gap, start_at__gte=start - timedelta(days=3)
    ).select_related("room")
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return [r for r in qs if start < busy_end(r, now) + gap]


def validate_slot(room, formula, start, exclude=None, now=None, allow_past=False):
    now = now or timezone.now()
    if room.status in (Room.Status.MAINTENANCE, Room.Status.OUT_OF_SERVICE) or not room.is_active:
        raise ValidationError(f"La chambre {room.number} est retirée de la vente.")
    if start < now - timedelta(minutes=15) and not allow_past:
        raise ValidationError("L'heure de début est déjà passée.")
    s, e = formula.compute_slot(start)
    conflicts = find_conflicts(room, s, e, exclude=exclude, now=now)
    if conflicts:
        c = conflicts[0]
        free_at = busy_end(c, now) + turnaround(room.hotel)
        raise ValidationError(f"Chambre {room.number} indisponible sur ce créneau (libre à partir de {fmt(free_at)}).")
    return s, e


def available_rooms(hotel, formula, start, now=None, exclude=None):
    """Chambres vendables pour (formule, début). Renvoie [(chambre, début, fin)]."""
    now = now or timezone.now()
    out = []
    for room in Room.objects.filter(hotel=hotel, is_active=True).select_related("room_type", "hotel"):
        try:
            s, e = validate_slot(room, formula, start, now=now, exclude=exclude)
            out.append((room, s, e))
        except ValidationError:
            continue
    return out


def snap(res):
    return {
        "code": res.code, "status": res.status, "room": res.room.number, "formula": res.formula.name,
        "start": res.start_at, "end": res.end_at,
    }


# ---------------------------------------------------------------- création
@transaction.atomic
def create_reservation(request, hotel, room_id, formula_id, start, source, guest_name, phone="", notes="", client=None):
    if not guest_name.strip():
        raise ValidationError("Le nom du client est obligatoire.")
    room = Room.objects.select_for_update().select_related("hotel").get(pk=room_id, hotel=hotel)
    formula = Formula.objects.get(pk=formula_id, hotel=hotel, is_active=True)
    now = timezone.now()
    s, e = validate_slot(room, formula, start, now=now)
    code = f"R{Sequence.next(hotel, 'reservation'):05d}"
    if source == Reservation.Source.ONLINE:
        status = S.PENDING
    elif s - now < timedelta(hours=1):
        status = S.CONFIRMED  # dernière minute : confirmation immédiate
    else:
        status = S.ACCEPTED
    user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    res = Reservation.objects.create(
        hotel=hotel, code=code, room=room, formula=formula, client=client, guest_name=guest_name.strip(),
        phone=phone.strip(), start_at=s, end_at=e, source=source, status=status, notes=notes,
        created_by=user, accepted_by=user if status != S.PENDING else None,
        call_result=Reservation.Call.CONFIRMED if status == S.CONFIRMED else "",
    )
    from billing.services import ensure_invoice

    ensure_invoice(request, res)
    record(request, "reservation.create", res, after=snap(res), hotel=hotel)
    return res


@transaction.atomic
def modify_reservation(request, res, room_id, formula_id, start, guest_name, phone, notes):
    if not res.is_active_pre:
        raise ValidationError("Cette réservation ne peut plus être modifiée.")
    before = snap(res)
    room = Room.objects.select_for_update().select_related("hotel").get(pk=room_id, hotel=res.hotel)
    formula = Formula.objects.get(pk=formula_id, hotel=res.hotel, is_active=True)
    s, e = validate_slot(room, formula, start, exclude=res)
    price_changed = formula.pk != res.formula_id
    res.room, res.formula, res.start_at, res.end_at = room, formula, s, e
    res.guest_name, res.phone, res.notes = guest_name.strip() or res.guest_name, phone.strip(), notes
    res.save()
    if price_changed and hasattr(res, "invoice"):
        from billing.services import sync_room_line

        sync_room_line(request, res)
    record(request, "reservation.modify", res, before=before, after=snap(res))
    return res


# ---------------------------------------------------------------- validation
def accept(request, res):
    if res.status != S.PENDING:
        raise ValidationError("Seule une demande en attente peut être acceptée.")
    before = snap(res)
    res.status = S.ACCEPTED
    res.accepted_by = request.user
    if res.start_at - timezone.now() < timedelta(hours=1):
        res.status = S.CONFIRMED
        res.call_result = Reservation.Call.CONFIRMED
    res.save()
    record(request, "reservation.accept", res, before=before, after=snap(res))


def record_call(request, res, result, note=""):
    if not res.is_active_pre:
        raise ValidationError("Cette réservation n'attend plus d'appel.")
    if result not in dict(Reservation.Call.choices) or not result:
        raise ValidationError("Résultat d'appel invalide.")
    before = snap(res)
    res.call_attempts += 1
    res.call_result = result
    res.call_done_at = timezone.now()
    if note:
        res.notes = (res.notes + "\n" if res.notes else "") + f"[Appel] {note}"
    if result == Reservation.Call.CONFIRMED:
        res.status = S.CONFIRMED
    res.save()
    record(request, f"reservation.call.{result}", res, before=before, after=snap(res), reason=note)
    if result == Reservation.Call.CANCELLED:
        cancel(request, res, "Annulé par le client lors de l'appel de confirmation")


def cancel(request, res, reason):
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Une justification (5 caractères minimum) est obligatoire pour annuler.")
    if not res.is_active_pre:
        raise ValidationError("Seule une réservation non commencée peut être annulée.")
    before = snap(res)
    res.status = S.CANCELLED
    res.cancel_reason = reason
    res.save()
    if hasattr(res, "invoice"):
        res.invoice.is_cancelled = True
        res.invoice.save(update_fields=["is_cancelled"])
    record(request, "reservation.cancel", res, before=before, after=snap(res), reason=reason)


def mark_no_show(request, res, reason="Client non présenté"):
    if not res.is_active_pre:
        raise ValidationError("Statut incompatible.")
    before = snap(res)
    res.status = S.NO_SHOW
    res.save()
    if hasattr(res, "invoice"):
        res.invoice.is_cancelled = True
        res.invoice.save(update_fields=["is_cancelled"])
    record(request, "reservation.no_show", res, before=before, after=snap(res), reason=reason)


# ---------------------------------------------------------------- séjour
@transaction.atomic
def check_in(request, res, client, override_reason=""):
    res = Reservation.objects.select_for_update().select_related("room", "hotel").get(pk=res.pk)
    if res.status == S.PENDING:
        raise ValidationError("Acceptez d'abord cette demande avant le check-in.")
    if res.status not in (S.ACCEPTED, S.CONFIRMED):
        raise ValidationError("Cette réservation n'est pas à l'arrivée.")
    now = timezone.now()
    if now < res.start_at - timedelta(hours=1):
        raise ValidationError(f"Trop tôt : l'arrivée est prévue à {fmt(res.start_at)}.")
    if res.room.status != Room.Status.AVAILABLE:
        raise ValidationError(f"La chambre {res.room.number} n'est pas disponible ({res.room.get_status_display()}).")
    missing = client.missing_identity()
    if missing and not override_reason:
        raise ValidationError("Identité incomplète : " + ", ".join(missing) + ".")
    before = snap(res)
    res.client = client
    res.guest_name = client.full_name or res.guest_name
    res.actual_checkin = now
    res.status = S.CHECKED_IN
    res.save()
    res.room.set_status(Room.Status.OCCUPIED)
    record(request, "stay.checkin", res, before=before, after=snap(res), reason=override_reason)
    if missing:
        record(request, "anomaly.identity_override", res, reason=f"{override_reason} (manque : {', '.join(missing)})")


def checkout_requirements(res, now=None):
    """Indique si un motif est exigé au check-out (dépassement ou solde impayé)."""
    now = now or timezone.now()
    needs = []
    tol = timedelta(minutes=res.hotel.overrun_tolerance_minutes)
    if now > res.end_at + tol:
        needs.append("dépassement au-delà de la tolérance sans nouveau créneau payé")
    inv = getattr(res, "invoice", None)
    if inv is not None and inv.balance > 0:
        needs.append(f"solde non réglé ({inv.balance} Ar)")
    return needs


@transaction.atomic
def check_out(request, res, reason=""):
    res = Reservation.objects.select_for_update().select_related("room", "hotel").get(pk=res.pk)
    if res.status != S.CHECKED_IN:
        raise ValidationError("Ce séjour n'est pas en cours.")
    now = timezone.now()
    needs = checkout_requirements(res, now)
    if needs and len((reason or "").strip()) < 5:
        raise ValidationError("Motif obligatoire : " + " ; ".join(needs) + ".")
    before = snap(res)
    res.actual_checkout = now
    res.status = S.CHECKED_OUT
    if needs:
        res.overstay_reason = reason.strip()[:300]
    res.save()
    room = res.room
    room.set_status(Room.Status.DIRTY)
    from frontdesk.models import CleaningTask

    nxt = (
        Reservation.objects.filter(room=room, status__in=[S.ACCEPTED, S.CONFIRMED, S.PENDING], start_at__gte=now)
        .order_by("start_at").first()
    )
    urgent = bool(nxt and nxt.start_at - now < timedelta(hours=1))
    CleaningTask.objects.create(hotel=res.hotel, room=room, reservation=res, urgent=urgent, checkout_at=now)
    record(request, "stay.checkout", res, before=before, after=snap(res), reason=reason)
    if now < res.end_at - timedelta(minutes=5):
        record(request, "stay.early_departure", res, reason="Départ anticipé : aucun remboursement, chambre revendable après remise en état")
    if needs:
        record(request, "anomaly.checkout_reason", res, reason="; ".join(needs) + " — " + reason)


def extension_limit(res):
    """Heure limite de prolongation (début de la réservation suivante moins remise en état) ou None."""
    nxt = (
        Reservation.objects.filter(room=res.room, status__in=BLOCKING, start_at__gte=res.end_at)
        .exclude(pk=res.pk).order_by("start_at").first()
    )
    if not nxt:
        return None
    return nxt.start_at - turnaround(res.hotel)


@transaction.atomic
def extend(request, res, formula_id):
    res = Reservation.objects.select_for_update().select_related("room", "hotel", "formula").get(pk=res.pk)
    if res.status != S.CHECKED_IN:
        raise ValidationError("Seul un séjour en cours peut être prolongé.")
    formula = Formula.objects.get(pk=formula_id, hotel=res.hotel, is_active=True)
    s, e = validate_slot(res.room, formula, res.end_at, exclude=res, allow_past=True)
    limit = extension_limit(res)
    before = snap(res)
    Extension.objects.create(reservation=res, formula=formula, start_at=s, end_at=e, created_by=request.user)
    res.end_at = e
    res.save()
    from billing.services import add_line

    add_line(request, res.invoice, "extension", f"Prolongation {formula.name} · ch. {res.room.number}", 1, formula.price)
    record(request, "stay.extend", res, before=before, after=snap(res), reason=formula.name)
    return limit


# ---------------------------------------------------------------- état d'une chambre
def room_state(room, res_list, now):
    """Etat affiché d'une chambre : code, libellé, détail, échéances."""
    if room.status == Room.Status.MAINTENANCE:
        return {"code": "maintenance", "label": "Maintenance", "sub": "Retirée de la vente", "res": None}
    if room.status == Room.Status.OUT_OF_SERVICE:
        return {"code": "oos", "label": "Hors service", "sub": "Retirée de la vente", "res": None}
    current = next((r for r in res_list if r.status == S.CHECKED_IN), None)
    if room.status == Room.Status.OCCUPIED and current:
        total = (current.end_at - (current.actual_checkin or current.start_at)).total_seconds() or 1
        done = (now - (current.actual_checkin or current.start_at)).total_seconds()
        progress = max(0, min(100, int(done / total * 100)))
        nxt = next((r for r in res_list if r.status in (S.ACCEPTED, S.CONFIRMED, S.PENDING) and r.start_at >= current.start_at), None)
        return {"code": "occupied", "label": "Occupée", "sub": f"jusqu'à {timezone.localtime(current.end_at):%Hh%M}",
                "res": current, "until": current.end_at, "progress": progress, "next": nxt}
    if room.status in (Room.Status.DIRTY, Room.Status.CLEANING):
        free_at = room.status_changed_at + turnaround(room.hotel)
        mins = max(0, math.ceil((free_at - now).total_seconds() / 60))
        label = "En nettoyage" if room.status == Room.Status.CLEANING else "À nettoyer"
        return {"code": "cleaning", "label": label, "sub": f"Disponible dans ~{mins} min" if mins else "Bientôt disponible",
                "res": None, "free_at": free_at}
    upcoming = [r for r in res_list if r.status in (S.ACCEPTED, S.CONFIRMED, S.PENDING)]
    upcoming.sort(key=lambda r: r.start_at)
    if upcoming:
        nxt = upcoming[0]
        gap = (nxt.start_at - now).total_seconds() / 60
        if gap <= 60:
            late = nxt.start_at < now
            sub = f"attendue depuis {timezone.localtime(nxt.start_at):%Hh%M}" if late else f"arrivée {timezone.localtime(nxt.start_at):%Hh%M}"
            return {"code": "reserved", "label": "Réservée", "sub": sub, "res": nxt}
        return {"code": "free", "label": "Libre", "sub": f"jusqu'à {timezone.localtime(nxt.start_at - turnaround(room.hotel)):%Hh%M}", "res": None, "next": nxt}
    return {"code": "free", "label": "Libre", "sub": "aucune réservation à venir", "res": None}


def rooms_with_state(hotel, now=None):
    now = now or timezone.now()
    rooms = list(Room.objects.filter(hotel=hotel, is_active=True).select_related("room_type", "hotel").order_by("number"))
    horizon = now + timedelta(days=2)
    res_qs = Reservation.objects.filter(hotel=hotel).filter(
        status__in=[S.PENDING, S.ACCEPTED, S.CONFIRMED, S.CHECKED_IN]
    ).exclude(status__in=[S.PENDING, S.ACCEPTED, S.CONFIRMED], start_at__gt=horizon).select_related("formula", "client")
    by_room = {}
    for r in res_qs:
        by_room.setdefault(r.room_id, []).append(r)
    return [{"room": room, "state": room_state(room, by_room.get(room.id, []), now)} for room in rooms]


# ---------------------------------------------------------------- tâche périodique paresseuse
def tick(hotel):
    """Libère les clients injoignables (exécuté à chaque rafraîchissement du tableau de bord)."""
    now = timezone.now()
    cutoff = now - timedelta(minutes=hotel.unreachable_release_minutes)
    qs = Reservation.objects.filter(
        hotel=hotel, status__in=[S.PENDING, S.ACCEPTED, S.CONFIRMED],
        call_result=Reservation.Call.UNREACHABLE, start_at__lt=cutoff,
    ).select_related("room", "formula")
    for r in qs:
        before = snap(r)
        r.status = S.NO_SHOW
        r.save(update_fields=["status", "updated_at"])
        if hasattr(r, "invoice"):
            r.invoice.is_cancelled = True
            r.invoice.save(update_fields=["is_cancelled"])
        record_system(hotel, "reservation.no_show_auto", r, before=before, after=snap(r),
                      reason="Client injoignable : chambre libérée automatiquement")
