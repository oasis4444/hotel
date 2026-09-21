from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


class Sequence(models.Model):
    """Numérotation séquentielle sans trou (réservations, factures, reçus)."""

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE)
    name = models.CharField(max_length=30)
    value = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("hotel", "name")

    @classmethod
    def next(cls, hotel, name):
        with transaction.atomic():
            cls.objects.get_or_create(hotel=hotel, name=name)
            seq = cls.objects.select_for_update().get(hotel=hotel, name=name)
            seq.value += 1
            seq.save(update_fields=["value"])
            return seq.value


class Client(models.Model):
    ID_TYPES = [("cin", "CIN"), ("passport", "Passeport"), ("other", "Autre")]

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="clients")
    last_name = models.CharField("Nom", max_length=80, blank=True)
    first_name = models.CharField("Prénom", max_length=80, blank=True)
    birth_date = models.DateField("Date de naissance", null=True, blank=True)
    nationality = models.CharField("Nationalité", max_length=60, blank=True)
    phone = models.CharField("Téléphone", max_length=30, blank=True)
    address = models.CharField("Adresse", max_length=200, blank=True)
    id_type = models.CharField("Type de pièce", max_length=10, choices=ID_TYPES, blank=True)
    id_number = models.CharField("Numéro de pièce", max_length=60, blank=True, db_index=True)
    id_photo = models.ImageField("Photo de la pièce", upload_to="ids/", null=True, blank=True)
    signature = models.TextField("Signature", blank=True)
    is_vip = models.BooleanField("VIP", default=False)
    is_blacklisted = models.BooleanField("Liste noire", default=False)
    notes = models.TextField("Notes", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name = "Client"

    def __str__(self):
        return self.full_name or f"Client #{self.pk}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def missing_identity(self):
        checks = [
            ("nom", self.last_name), ("prénom", self.first_name), ("date de naissance", self.birth_date),
            ("nationalité", self.nationality), ("téléphone", self.phone), ("adresse", self.address),
            ("type de pièce", self.id_type), ("numéro de pièce", self.id_number),
            ("photo de la pièce", self.id_photo), ("signature", self.signature),
        ]
        return [label for label, value in checks if not value]


class Reservation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        ACCEPTED = "accepted", "Acceptée"
        CONFIRMED = "confirmed", "Confirmée"
        CHECKED_IN = "checked_in", "En séjour"
        CHECKED_OUT = "checked_out", "Terminée"
        CANCELLED = "cancelled", "Annulée"
        NO_SHOW = "no_show", "No-show"

    class Source(models.TextChoices):
        ONSITE = "onsite", "Sur place"
        PHONE = "phone", "Téléphone"
        ONLINE = "online", "En ligne"

    class Call(models.TextChoices):
        NONE = "", "Pas encore appelé"
        CONFIRMED = "confirmed", "Confirmé"
        UNREACHABLE = "unreachable", "Injoignable"
        CANCELLED = "cancelled", "Annulé par le client"
        RESCHEDULED = "rescheduled", "Décalé"

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="reservations")
    code = models.CharField(max_length=12)
    room = models.ForeignKey("hotels.Room", on_delete=models.PROTECT, related_name="reservations")
    formula = models.ForeignKey("hotels.Formula", on_delete=models.PROTECT, related_name="+")
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.SET_NULL, related_name="reservations")
    guest_name = models.CharField("Nom du client", max_length=120)
    phone = models.CharField("Téléphone", max_length=30, blank=True)
    start_at = models.DateTimeField("Début")
    end_at = models.DateTimeField("Fin")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.ONSITE)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    call_result = models.CharField(max_length=12, choices=Call.choices, blank=True, default="")
    call_attempts = models.PositiveSmallIntegerField(default=0)
    call_done_at = models.DateTimeField(null=True, blank=True)
    actual_checkin = models.DateTimeField(null=True, blank=True)
    actual_checkout = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    overstay_reason = models.CharField(max_length=300, blank=True)
    notes = models.TextField("Notes", blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("hotel", "code")
        ordering = ["start_at"]
        indexes = [models.Index(fields=["hotel", "room", "start_at"])]
        verbose_name = "Réservation"

    def __str__(self):
        return f"{self.code} · {self.guest_name}"

    @property
    def is_active_pre(self):
        return self.status in (self.Status.PENDING, self.Status.ACCEPTED, self.Status.CONFIRMED)

    @property
    def minutes_left(self):
        return int((self.end_at - timezone.now()).total_seconds() // 60)


class Extension(models.Model):
    """Prolongation = nouveau créneau enregistré, facturé et payé (jamais automatique)."""

    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name="extensions")
    formula = models.ForeignKey("hotels.Formula", on_delete=models.PROTECT, related_name="+")
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
