from django.conf import settings
from django.db import models


class Invoice(models.Model):
    """Folio unique par séjour : toute prestation y figure sous forme de ligne."""

    STATUS_LABELS = {
        "unpaid": "Non payée", "partial": "Partiellement payée", "paid": "Payée",
        "refunded": "Remboursée", "refunded_partial": "Partiellement remboursée", "cancelled": "Annulée",
    }

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="invoices")
    number = models.CharField(max_length=14)
    reservation = models.OneToOneField("reservations.Reservation", null=True, blank=True, on_delete=models.PROTECT, related_name="invoice")
    order = models.OneToOneField("kitchen.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="order_invoice")
    customer_name = models.CharField(max_length=120)
    is_cancelled = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("hotel", "number")
        ordering = ["-id"]
        verbose_name = "Facture"

    def __str__(self):
        return self.number

    @property
    def total(self):
        return sum(l.amount for l in self.lines.all() if not l.is_cancelled)

    @property
    def paid(self):
        return sum(p.amount for p in self.payments.all() if p.kind != Payment.Kind.REFUND)

    @property
    def refunded(self):
        return sum(p.amount for p in self.payments.all() if p.kind == Payment.Kind.REFUND)

    @property
    def net_paid(self):
        return self.paid - self.refunded

    @property
    def balance(self):
        return 0 if self.is_cancelled else self.total - self.net_paid

    @property
    def status(self):
        if self.is_cancelled and self.net_paid <= 0 and self.refunded == 0:
            return "cancelled"
        if self.refunded > 0:
            return "refunded" if self.net_paid <= 0 else "refunded_partial"
        if self.total > 0 and self.net_paid >= self.total:
            return "paid"
        if self.net_paid > 0:
            return "partial"
        return "unpaid"

    @property
    def status_label(self):
        return self.STATUS_LABELS[self.status]


class InvoiceLine(models.Model):
    class Kind(models.TextChoices):
        ROOM = "room", "Chambre"
        EXTENSION = "extension", "Prolongation"
        MEAL = "meal", "Restauration"
        SERVICE = "service", "Service"
        OTHER = "other", "Autre"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.OTHER)
    description = models.CharField(max_length=200)
    quantity = models.PositiveSmallIntegerField(default=1)
    unit_price = models.PositiveIntegerField()
    is_cancelled = models.BooleanField(default=False)
    cancel_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    @property
    def amount(self):
        return self.quantity * self.unit_price


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Espèces"
        MVOLA = "mvola", "MVola"
        ORANGE = "orange", "Orange Money"
        AIRTEL = "airtel", "Airtel Money"

    class Kind(models.TextChoices):
        PAYMENT = "payment", "Paiement"
        DEPOSIT = "deposit", "Acompte"
        REFUND = "refund", "Remboursement"

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    receipt_number = models.CharField(max_length=14)
    method = models.CharField(max_length=10, choices=Method.choices)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PAYMENT)
    amount = models.PositiveIntegerField()
    reference = models.CharField("Référence de transaction", max_length=60, blank=True)
    payer_phone = models.CharField("Numéro payeur", max_length=30, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    shift = models.ForeignKey("frontdesk.Shift", null=True, on_delete=models.PROTECT, related_name="payments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Paiement"
