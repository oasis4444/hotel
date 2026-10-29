from django.conf import settings
from django.db import models


class Supplier(models.Model):
    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="suppliers")
    name = models.CharField("Nom", max_length=120)
    phone = models.CharField("Téléphone", max_length=40, blank=True)
    address = models.CharField("Adresse", max_length=200, blank=True)
    notes = models.CharField("Notes", max_length=300, blank=True)
    is_active = models.BooleanField("Actif", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Fournisseur"

    def __str__(self):
        return self.name


class StockItem(models.Model):
    class Unit(models.TextChoices):
        PIECE = "piece", "Pièce"
        KG = "kg", "Kilogramme"
        G = "g", "Gramme"
        L = "l", "Litre"
        ML = "ml", "Millilitre"
        BOTTLE = "bottle", "Bouteille"
        PACK = "pack", "Paquet"

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="stock_items")
    name = models.CharField("Article", max_length=120)
    unit = models.CharField("Unité", max_length=10, choices=Unit.choices, default=Unit.PIECE)
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL, related_name="items")
    cost_price = models.PositiveIntegerField("Dernier prix d'achat (Ar/unité)", default=0)
    quantity = models.DecimalField("Quantité en stock", max_digits=10, decimal_places=2, default=0)
    low_stock_threshold = models.DecimalField("Seuil d'alerte", max_digits=10, decimal_places=2, default=0)
    expiry_date = models.DateField("Date de péremption", null=True, blank=True)
    is_active = models.BooleanField("Actif", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Article de stock"
        verbose_name_plural = "Articles de stock"

    def __str__(self):
        return f"{self.name} ({self.get_unit_display()})"

    @property
    def is_low(self):
        return self.low_stock_threshold > 0 and self.quantity <= self.low_stock_threshold

    @property
    def expires_soon(self):
        from datetime import timedelta

        from django.utils import timezone

        return bool(self.expiry_date) and self.expiry_date <= timezone.localdate() + timedelta(days=7)


class StockMovement(models.Model):
    class Kind(models.TextChoices):
        IN = "in", "Entrée (achat)"
        OUT = "out", "Sortie (consommation)"
        LOSS = "loss", "Perte / casse"
        ADJUST = "adjust", "Ajustement d'inventaire"

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="stock_movements")
    item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="movements")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=300, blank=True)
    order = models.ForeignKey("kitchen.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="stock_movements")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Mouvement de stock"

    def __str__(self):
        return f"{self.get_kind_display()} · {self.item} · {self.quantity}"


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        RECEIVED = "received", "Réceptionnée"

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="purchases")
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL, related_name="purchases")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Commande d'achat"

    def __str__(self):
        return f"Achat #{self.pk} · {self.supplier or 'sans fournisseur'}"

    @property
    def total(self):
        return sum(l.amount for l in self.lines.all())


class PurchaseLine(models.Model):
    purchase = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.PositiveIntegerField()

    class Meta:
        ordering = ["id"]

    @property
    def amount(self):
        return float(self.quantity) * self.unit_cost
