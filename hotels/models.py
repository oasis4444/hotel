from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Hotel(models.Model):
    name = models.CharField("Nom", max_length=120)
    slug = models.SlugField("Identifiant web", unique=True)
    tagline = models.CharField("Slogan", max_length=160, blank=True)
    address = models.CharField("Adresse", max_length=200, blank=True)
    phone = models.CharField("Téléphone", max_length=40, blank=True)
    email = models.EmailField("Email", blank=True)
    logo = models.ImageField("Logo", upload_to="logos/", null=True, blank=True)
    theme_color = models.CharField("Couleur principale", max_length=7, blank=True, help_text="Code hexadécimal, ex. #0B7A86")

    turnaround_minutes = models.PositiveSmallIntegerField("Remise en état entre deux séjours (min)", default=10)
    cleaning_alert_minutes = models.PositiveSmallIntegerField("Alerte si nettoyage dépasse (min)", default=15)
    cleaning_fraud_minutes = models.PositiveSmallIntegerField("Alerte anti-fraude si prête en moins de (min)", default=5)
    warn_before_end_minutes = models.PositiveSmallIntegerField("Préavis de fin de créneau (min)", default=10)
    overrun_tolerance_minutes = models.PositiveSmallIntegerField("Tolérance de dépassement (min)", default=10)
    unreachable_release_minutes = models.PositiveSmallIntegerField("Libération d'un client injoignable (min après l'arrivée)", default=30)
    online_reconfirm_minutes = models.PositiveSmallIntegerField("Délai de reconfirmation en ligne (min)", default=15)
    shift_start_hour = models.PositiveSmallIntegerField("Heure de début du quart", default=8)
    cash_variance_alert = models.PositiveIntegerField("Seuil d'alerte écart de caisse (Ar)", default=5000)
    expense_manager_threshold = models.PositiveIntegerField("Dépense nécessitant le gérant au-delà de (Ar)", default=100000)
    online_max_pending_per_phone = models.PositiveSmallIntegerField("Réservations en attente max par numéro", default=2)
    is_active = models.BooleanField("Actif", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Hôtel"

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Propriétaire"
        MANAGER = "manager", "Gérant"
        RECEPTIONIST = "receptionist", "Réceptionniste"
        COOK = "cook", "Cuisinier"
        HOUSEKEEPER = "housekeeper", "Femme de chambre"
        MAINTENANCE = "maintenance", "Maintenance"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField("Rôle", max_length=20, choices=Role.choices)
    is_active = models.BooleanField("Actif", default=True)

    class Meta:
        unique_together = ("user", "hotel")
        verbose_name = "Affectation"

    def __str__(self):
        return f"{self.user} · {self.hotel} · {self.get_role_display()}"


class RoomType(models.Model):
    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE, related_name="room_types")
    name = models.CharField("Nom", max_length=60)
    capacity = models.PositiveSmallIntegerField("Personnes", default=2)
    description = models.CharField("Description", max_length=200, blank=True)

    class Meta:
        verbose_name = "Type de chambre"
        verbose_name_plural = "Types de chambre"

    def __str__(self):
        return f"{self.name} ({self.hotel})"


class Room(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Disponible"
        OCCUPIED = "occupied", "Occupée"
        DIRTY = "dirty", "À nettoyer"
        CLEANING = "cleaning", "En nettoyage"
        MAINTENANCE = "maintenance", "Maintenance"
        OUT_OF_SERVICE = "out_of_service", "Hors service"

    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE, related_name="rooms")
    number = models.CharField("Numéro", max_length=10)
    room_type = models.ForeignKey(RoomType, on_delete=models.PROTECT, related_name="rooms")
    floor = models.CharField("Étage", max_length=20, blank=True)
    status = models.CharField("Statut", max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    status_changed_at = models.DateTimeField(default=timezone.now)
    photo = models.ImageField("Photo", upload_to="rooms/", blank=True, null=True)
    is_active = models.BooleanField("Active", default=True)

    class Meta:
        unique_together = ("hotel", "number")
        ordering = ["number"]
        verbose_name = "Chambre"

    def __str__(self):
        return f"Ch. {self.number}"

    def set_status(self, status):
        self.status = status
        self.status_changed_at = timezone.now()
        self.save(update_fields=["status", "status_changed_at"])


class Formula(models.Model):
    """Formule tarifaire : 1H, 2H, Journée, Nuitée, 24H... (paramétrable sans code)."""

    HOURLY = "hourly"
    WINDOW = "window"
    KINDS = [(HOURLY, "Durée fixe"), (WINDOW, "Plage horaire")]

    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE, related_name="formulas")
    code = models.SlugField("Code", max_length=20)
    name = models.CharField("Nom affiché", max_length=40)
    kind = models.CharField("Type", max_length=10, choices=KINDS, default=HOURLY)
    duration_minutes = models.PositiveIntegerField("Durée (min) [durée fixe]", default=60)
    start_hour = models.PositiveSmallIntegerField("Début de plage (h) [plage]", default=8)
    end_hour = models.PositiveSmallIntegerField("Fin de plage (h) [plage]", default=17)
    end_next_day = models.BooleanField("Fin le lendemain", default=False)
    min_minutes = models.PositiveIntegerField("Durée minimale (min) [plage]", default=0)
    price = models.PositiveIntegerField("Tarif (Ar)")
    sort_order = models.PositiveSmallIntegerField("Ordre", default=0)
    is_active = models.BooleanField("Active", default=True)

    class Meta:
        unique_together = ("hotel", "code")
        ordering = ["sort_order", "price"]
        verbose_name = "Formule tarifaire"

    def __str__(self):
        return f"{self.name} · {self.price} Ar"

    @property
    def plage(self):
        if self.kind == self.HOURLY:
            h, m = divmod(self.duration_minutes, 60)
            return f"{h}h{m:02d}" if m else f"{h}h"
        suffix = " (lendemain)" if self.end_next_day else ""
        extra = f", min {self.min_minutes // 60}h" if self.min_minutes else ""
        return f"{self.start_hour}h → {self.end_hour}h{suffix}{extra}"

    def compute_slot(self, start):
        """Retourne (début, fin) ou lève ValidationError."""
        local = timezone.localtime(start).replace(second=0, microsecond=0)
        if self.kind == self.HOURLY:
            return local, local + timedelta(minutes=self.duration_minutes)
        window_start = local.replace(hour=self.start_hour, minute=0)
        if local < window_start:
            raise ValidationError(f"La formule « {self.name} » commence à {self.start_hour}H.")
        end = local.replace(hour=self.end_hour, minute=0)
        if self.end_next_day:
            end += timedelta(days=1)
        if end - local < timedelta(minutes=max(self.min_minutes, 1)):
            raise ValidationError(f"Trop tard pour la formule « {self.name} » : durée minimale non atteinte.")
        return local, end
