from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class AuditQuerySet(models.QuerySet):
    def update(self, *args, **kwargs):
        raise PermissionError("Le journal d'audit n'est pas modifiable.")

    def delete(self):
        raise PermissionError("Le journal d'audit n'est pas modifiable.")


class AuditLog(models.Model):
    """Journal non modifiable : on ajoute, on ne modifie ni ne supprime jamais."""

    hotel = models.ForeignKey("hotels.Hotel", null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=60, db_index=True)
    object_type = models.CharField(max_length=40, blank=True, db_index=True)
    object_id = models.CharField(max_length=40, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    before = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    after = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    reason = models.CharField(max_length=300, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    device = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Entrée du journal"
        verbose_name_plural = "Journal d'audit"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Le journal d'audit n'est pas modifiable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Le journal d'audit n'est pas modifiable.")

    @property
    def is_anomaly(self):
        return self.action.startswith("anomaly.")

    def __str__(self):
        return f"{self.created_at:%d/%m %H:%M} {self.action}"
