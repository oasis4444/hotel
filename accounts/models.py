from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    phone = models.CharField("Téléphone", max_length=30, blank=True)
    language = models.CharField("Langue", max_length=5, default="fr")

    @property
    def display_name(self):
        return self.get_full_name() or self.username
