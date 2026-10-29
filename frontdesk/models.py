from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class Shift(models.Model):
    """Quart d'un réceptionniste : toutes ses opérations y sont rattachées."""

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="shifts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="shifts")
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    opening_float = models.PositiveIntegerField("Fond de caisse à l'ouverture", default=0)
    previous = models.OneToOneField("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="next_shift")
    declared_cash = models.PositiveIntegerField(null=True, blank=True)
    expected_cash_at_close = models.IntegerField(null=True, blank=True)
    variance = models.IntegerField(null=True, blank=True)
    variance_note = models.CharField(max_length=300, blank=True)
    handover_note = models.TextField("Note de passation", blank=True)
    incoming_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    incoming_signed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        verbose_name = "Quart"

    def __str__(self):
        return f"Quart {self.user} · {timezone.localtime(self.opened_at):%d/%m %Hh%M}"

    @property
    def is_open(self):
        return self.closed_at is None

    def totals_by_method(self):
        from billing.models import Payment

        out = {m: 0 for m, _ in Payment.Method.choices}
        for p in self.payments.all():
            out[p.method] += -p.amount if p.kind == Payment.Kind.REFUND else p.amount
        return out

    def advances_out(self):
        return self.expense_advances.aggregate(t=Sum("advance_amount"))["t"] or 0

    def change_in(self):
        return self.expense_settlements.aggregate(t=Sum("change_returned"))["t"] or 0

    def expected_cash(self):
        return self.opening_float + self.totals_by_method()["cash"] + self.change_in() - self.advances_out()


class Remittance(models.Model):
    """Versement au gérant : espèces remises + contrôle séparé du mobile money."""

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="remittances")
    shift = models.OneToOneField(Shift, on_delete=models.PROTECT, related_name="remittance")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    cash_amount = models.PositiveIntegerField("Espèces remises")
    kept_float = models.PositiveIntegerField("Fond conservé pour le quart suivant", default=0)
    mvola_total = models.IntegerField(default=0)
    orange_total = models.IntegerField(default=0)
    airtel_total = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    received_cash = models.PositiveIntegerField(null=True, blank=True)
    mobile_check_note = models.CharField("Contrôle des soldes mobile money", max_length=300, blank=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def variance(self):
        return None if self.received_cash is None else self.received_cash - self.cash_amount

    @property
    def mobile_total(self):
        return self.mvola_total + self.orange_total + self.airtel_total


class Expense(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Demandée"
        ACCEPTED = "accepted", "Acceptée"
        REJECTED = "rejected", "Refusée"
        ADVANCED = "advanced", "Avance remise"
        SETTLED = "settled", "Justifiée"

    class Category(models.TextChoices):
        FOOD = "food", "Nourriture"
        SUPPLIES = "supplies", "Produits et fournitures"
        REPAIR = "repair", "Réparation"
        UTILITY = "utility", "JIRAMA, eau, internet"
        OTHER = "other", "Autre"

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="expenses")
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="expense_requests")
    description = models.CharField("Quoi et pourquoi", max_length=250)
    category = models.CharField(max_length=10, choices=Category.choices, default=Category.OTHER)
    estimated_amount = models.PositiveIntegerField("Montant estimé (Ar)")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.CharField(max_length=300, blank=True)
    advance_amount = models.PositiveIntegerField(default=0)
    advance_shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="expense_advances")
    advanced_at = models.DateTimeField(null=True, blank=True)
    actual_amount = models.PositiveIntegerField(null=True, blank=True)
    change_returned = models.PositiveIntegerField(default=0)
    settle_shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="expense_settlements")
    settled_at = models.DateTimeField(null=True, blank=True)
    receipt_photo = models.ImageField(upload_to="receipts/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Dépense"


class CleaningTask(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "À nettoyer"
        IN_PROGRESS = "in_progress", "En nettoyage"
        DONE = "done", "Terminé, à contrôler"
        VERIFIED = "verified", "Prête"

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="cleaning_tasks")
    room = models.ForeignKey("hotels.Room", on_delete=models.CASCADE, related_name="cleaning_tasks")
    reservation = models.ForeignKey("reservations.Reservation", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.TODO, db_index=True)
    urgent = models.BooleanField(default=False)
    checkout_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    worked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-urgent", "created_at"]

    @property
    def minutes_to_ready(self):
        if self.verified_at:
            return int((self.verified_at - self.checkout_at).total_seconds() // 60)
        return None

    @property
    def cleaning_minutes(self):
        if self.started_at and self.done_at:
            return max(1, int((self.done_at - self.started_at).total_seconds() // 60))
        return None


class MaintenanceTicket(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Ouvert"
        IN_PROGRESS = "in_progress", "En cours"
        DONE = "done", "Résolu"

    class Priority(models.TextChoices):
        LOW = "low", "Faible"
        NORMAL = "normal", "Normale"
        URGENT = "urgent", "Urgente"

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="maintenance_tickets")
    room = models.ForeignKey("hotels.Room", null=True, blank=True, on_delete=models.SET_NULL, related_name="maintenance_tickets")
    title = models.CharField("Problème", max_length=160)
    description = models.TextField("Détails", blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    block_room = models.BooleanField("Bloquer la chambre (hors service)", default=False)
    cost = models.PositiveIntegerField("Coût de la réparation (Ar)", null=True, blank=True)
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-priority", "-created_at"]
        verbose_name = "Ticket de maintenance"

    def __str__(self):
        return f"{self.title} · {self.room or 'général'}"
