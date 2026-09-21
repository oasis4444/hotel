from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from frontdesk.models import Shift
from hotels.models import Formula, Hotel, Membership, Room, RoomType
from reservations.models import Client, Reservation, Sequence

PASSWORD = "trano2026"


class Command(BaseCommand):
    help = "Crée un hôtel de démonstration avec comptes, chambres, formules et quelques séjours."

    def handle(self, *args, **opts):
        U = get_user_model()
        hotel, created = Hotel.objects.get_or_create(
            slug="vanille",
            defaults={"name": "Hôtel Vanille", "tagline": "Votre chambre, à l'heure qui vous convient.",
                      "address": "Antananarivo", "phone": "034 00 000 00"},
        )
        if not created:
            self.stdout.write("La démo existe déjà.")
            return
        accounts = [("proprio", "Owner", "owner", "Propriétaire"), ("gerant", "Gérant", "manager", "Hery"),
                    ("rece1", "Réceptionniste", "receptionist", "Miora"), ("rece2", "Réceptionniste", "receptionist", "Tsiry"),
                    ("menage1", "Femme de chambre", "housekeeper", "Voahangy")]
        users = {}
        for username, _label, role, first in accounts:
            u, _ = U.objects.get_or_create(username=username, defaults={"first_name": first})
            u.set_password(PASSWORD)
            if username == "proprio":
                u.is_staff = True
                u.is_superuser = True
            u.save()
            Membership.objects.get_or_create(user=u, hotel=hotel, defaults={"role": role})
            users[username] = u
        std = RoomType.objects.create(hotel=hotel, name="Standard", capacity=2)
        conf = RoomType.objects.create(hotel=hotel, name="Confort", capacity=2)
        suite = RoomType.objects.create(hotel=hotel, name="Suite", capacity=4)
        rooms = []
        for i in range(1, 13):
            rt = std if i <= 6 else conf if i <= 10 else suite
            rooms.append(Room.objects.create(hotel=hotel, number=str(100 + i), room_type=rt, floor="1" if i <= 6 else "2"))
        Formula.objects.create(hotel=hotel, code="1h", name="1H", kind="hourly", duration_minutes=60, price=15000, sort_order=1)
        Formula.objects.create(hotel=hotel, code="2h", name="2H", kind="hourly", duration_minutes=120, price=25000, sort_order=2)
        Formula.objects.create(hotel=hotel, code="jour", name="Journée", kind="window", start_hour=8, end_hour=17, min_minutes=180, price=35000, sort_order=3)
        Formula.objects.create(hotel=hotel, code="nuit", name="Nuitée", kind="window", start_hour=18, end_hour=10, end_next_day=True, price=35000, sort_order=4)
        Formula.objects.create(hotel=hotel, code="24h", name="24H", kind="window", start_hour=12, end_hour=10, end_next_day=True, price=70000, sort_order=5)

        # Quelques séjours pour animer le tableau de bord
        from billing.models import Invoice, InvoiceLine
        now = timezone.now()
        shift = Shift.objects.create(hotel=hotel, user=users["rece1"], opening_float=50000)
        f1, f2 = Formula.objects.get(hotel=hotel, code="1h"), Formula.objects.get(hotel=hotel, code="2h")

        def stay(room, formula, name, started_min_ago, length_min):
            start = now - timedelta(minutes=started_min_ago)
            code = f"R{Sequence.next(hotel, 'reservation'):05d}"
            client = Client.objects.create(hotel=hotel, first_name=name.split()[0], last_name=name.split()[-1], phone="0340000000")
            r = Reservation.objects.create(hotel=hotel, code=code, room=room, formula=formula, client=client, guest_name=name,
                                           phone="0340000000", start_at=start, end_at=start + timedelta(minutes=length_min),
                                           status="checked_in", source="onsite", actual_checkin=start, created_by=users["rece1"])
            room.set_status("occupied")
            inv = Invoice.objects.create(hotel=hotel, number=f"F{Sequence.next(hotel, 'invoice'):06d}", reservation=r, customer_name=name)
            InvoiceLine.objects.create(invoice=inv, kind="room", description=f"Chambre {room.number} · {formula.name}", unit_price=formula.price)

        stay(rooms[0], f1, "Rakoto Jean", 25, 60)
        stay(rooms[1], f2, "Rasoa Lalao", 112, 120)
        stay(rooms[2], f1, "Andry Faly", 70, 60)
        from frontdesk.models import CleaningTask
        rooms[5].set_status("dirty")
        CleaningTask.objects.create(hotel=hotel, room=rooms[5], urgent=False)
        rooms[9].set_status("maintenance")
        self.stdout.write(self.style.SUCCESS(
            "Démo créée. Comptes : proprio, gerant, rece1, rece2, menage1 — mot de passe : " + PASSWORD))
        self.stdout.write(f"Portail public : /h/{hotel.slug}/")
