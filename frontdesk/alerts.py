"""Alertes calculées en temps réel (aucune facturation automatique de dépassement)."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from reservations.models import Reservation

from .models import CleaningTask

S = Reservation.Status
ORDER = {"danger": 0, "warn": 1, "info": 2}


def build_alerts(hotel, now=None):
    now = now or timezone.now()
    out = []
    warn = timedelta(minutes=hotel.warn_before_end_minutes)
    tol = timedelta(minutes=hotel.overrun_tolerance_minutes)

    stays = Reservation.objects.filter(hotel=hotel, status=S.CHECKED_IN).select_related("room")
    for r in stays:
        url = reverse("reservation_detail", args=[r.pk])
        left = r.end_at - now
        nxt = (
            Reservation.objects.filter(room=r.room, status__in=[S.ACCEPTED, S.CONFIRMED, S.PENDING], start_at__gte=r.start_at)
            .exclude(pk=r.pk).order_by("start_at").first()
        )
        imminent = nxt is not None and nxt.start_at - now < timedelta(minutes=60)
        if left > timedelta(0) and left <= warn:
            out.append({"level": "warn", "key": f"end-{r.pk}", "title": f"Chambre {r.room.number} : fin dans {int(left.total_seconds() // 60) + 1} min",
                        "text": "Prévenez le client ou proposez une prolongation.", "url": url})
        elif left <= timedelta(0):
            over = -left
            if over > tol:
                out.append({"level": "danger", "key": f"esc-{r.pk}", "sound": True, "title": f"Chambre {r.room.number} : dépassement de {int(over.total_seconds() // 60)} min",
                            "text": "Tolérance dépassée : escalade au gérant si aucune action.", "url": url})
            else:
                out.append({"level": "danger", "key": f"over-{r.pk}", "sound": True, "title": f"Chambre {r.room.number} : créneau terminé",
                            "text": "Faire quitter le client ou encaisser un nouveau créneau.", "url": url})
            if imminent:
                out.append({"level": "danger", "key": f"clash-{r.pk}", "sound": True, "title": f"Chambre {r.room.number} : réservation imminente derrière",
                            "text": f"{nxt.guest_name} arrive à {timezone.localtime(nxt.start_at):%Hh%M}. Déplacez le client suivant ou faites libérer la chambre.", "url": url})

    online = Reservation.objects.filter(hotel=hotel, status=S.PENDING, source=Reservation.Source.ONLINE)
    for r in online:
        waited = int((now - r.created_at).total_seconds() // 60)
        late = waited >= hotel.online_reconfirm_minutes
        out.append({"level": "danger" if late else "warn", "key": f"online-{r.pk}", "sound": late,
                    "title": f"Demande en ligne à reconfirmer : {r.guest_name}",
                    "text": f"En attente depuis {waited} min" + (" : escalade au gérant" if late else "") + f" · {r.phone}", "url": reverse("reservation_detail", args=[r.pk])})

    for r in Reservation.objects.filter(hotel=hotel, status=S.ACCEPTED, start_at__lte=now + timedelta(hours=3), start_at__gte=now - timedelta(hours=1)).select_related("room"):
        to_call_by = r.start_at - timedelta(hours=1)
        overdue = now > to_call_by
        out.append({"level": "danger" if overdue else "info", "key": f"call-{r.pk}", "sound": False,
                    "title": f"Appeler {r.guest_name} (ch. {r.room.number})",
                    "text": ("Appel de confirmation en retard" if overdue else f"À appeler avant {timezone.localtime(to_call_by):%Hh%M}") + (f" · {r.phone}" if r.phone else ""),
                    "url": reverse("reservation_detail", args=[r.pk])})

    for r in Reservation.objects.filter(hotel=hotel, status__in=[S.ACCEPTED, S.CONFIRMED], start_at__lt=now - timedelta(minutes=15), start_at__gte=now - timedelta(hours=6)).select_related("room"):
        out.append({"level": "warn", "key": f"late-{r.pk}", "title": f"{r.guest_name} n'est pas arrivé (ch. {r.room.number})",
                    "text": f"Attendu à {timezone.localtime(r.start_at):%Hh%M}. Appelez ou libérez la chambre.", "url": reverse("reservation_detail", args=[r.pk])})

    limit = timedelta(minutes=hotel.cleaning_alert_minutes)
    for t in CleaningTask.objects.filter(hotel=hotel).exclude(status=CleaningTask.Status.VERIFIED).select_related("room"):
        if t.status == CleaningTask.Status.DONE:
            out.append({"level": "warn", "key": f"verify-{t.pk}", "title": f"Chambre {t.room.number} à contrôler",
                        "text": "Nettoyage terminé : vérifiez pour la rendre vendable.", "url": reverse("cleaning")})
        elif now - t.checkout_at > limit:
            out.append({"level": "warn", "key": f"clean-{t.pk}", "title": f"Nettoyage long : chambre {t.room.number}",
                        "text": f"{int((now - t.checkout_at).total_seconds() // 60)} min depuis le départ.", "url": reverse("cleaning")})

    out.sort(key=lambda a: ORDER[a["level"]])
    return out
