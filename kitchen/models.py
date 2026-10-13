from django.conf import settings
from django.db import models
from django.utils import timezone


class Dish(models.Model):
    """Carte du restaurant : plats, boissons, etc. — gérée par le cuisinier/gérant."""

    class Category(models.TextChoices):
        STARTER = "starter", "Entrée"
        MAIN = "main", "Plat"
        DESSERT = "dessert", "Dessert"
        DRINK = "drink", "Boisson"
        BREAKFAST = "breakfast", "Petit-déjeuner"
        OTHER = "other", "Autre"

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="dishes")
    name = models.CharField("Nom", max_length=120)
    category = models.CharField("Catégorie", max_length=10, choices=Category.choices, default=Category.MAIN)
    price = models.PositiveIntegerField("Prix (Ar)")
    description = models.CharField("Description", max_length=200, blank=True)
    is_available = models.BooleanField("Disponible aujourd'hui", default=True)
    sort_order = models.PositiveSmallIntegerField("Ordre", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category", "sort_order", "name"]
        verbose_name = "Plat"
        verbose_name_plural = "Carte"

    def __str__(self):
        return f"{self.name} · {self.price} Ar"


class Order(models.Model):
    """Commande restauration : réception (ou client) → cuisine → livraison en chambre."""

    class Status(models.TextChoices):
        NEW = "new", "Nouvelle"
        PREPARING = "preparing", "En préparation"
        READY = "ready", "Prête"
        DELIVERED = "delivered", "Livrée"
        CANCELLED = "cancelled", "Annulée"

    hotel = models.ForeignKey("hotels.Hotel", on_delete=models.CASCADE, related_name="orders")
    reservation = models.ForeignKey(
        "reservations.Reservation", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders"
    )
    room = models.ForeignKey("hotels.Room", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    guest_name = models.CharField("Client", max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW, db_index=True)
    notes = models.CharField("Remarque", max_length=300, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Commande"

    def __str__(self):
        return f"Commande #{self.pk} · {self.room or self.guest_name}"

    @property
    def total(self):
        return sum(l.amount for l in self.lines.all())

    @property
    def minutes_open(self):
        end = self.delivered_at or timezone.now()
        return int((end - self.created_at).total_seconds() // 60)


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    dish = models.ForeignKey(Dish, on_delete=models.PROTECT, related_name="+")
    dish_name = models.CharField(max_length=120)
    quantity = models.PositiveSmallIntegerField(default=1)
    unit_price = models.PositiveIntegerField()

    class Meta:
        ordering = ["id"]

    @property
    def amount(self):
        return self.quantity * self.unit_price
