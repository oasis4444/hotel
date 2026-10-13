from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.text import slugify

from audit.service import record

from .models import Hotel, Membership
from .permissions import perm_required


@perm_required("hotel.manage")
def hotel_list(request):
    return render(request, "hotels/list.html", {
        "memberships": request.memberships, "page": "hotels",
    })


@perm_required("hotel.manage")
def hotel_new(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        try:
            if len(name) < 2:
                raise ValidationError("Le nom de l'hôtel est obligatoire.")
            base_slug = slugify(name) or "hotel"
            slug = base_slug
            n = 1
            while Hotel.objects.filter(slug=slug).exists():
                n += 1
                slug = f"{base_slug}-{n}"
            hotel = Hotel.objects.create(
                name=name, slug=slug,
                tagline=request.POST.get("tagline", "").strip(),
                address=request.POST.get("address", "").strip(),
                phone=request.POST.get("phone", "").strip(),
                email=request.POST.get("email", "").strip(),
            )
            Membership.objects.create(user=request.user, hotel=hotel, role=Membership.Role.OWNER)
            record(request, "hotel.create", hotel, after={"name": hotel.name, "slug": hotel.slug}, hotel=hotel)
            request.session["hotel_id"] = hotel.id
            messages.success(
                request,
                f"« {hotel.name} » créé. Ajoutez maintenant ses chambres, types de chambre et formules "
                "tarifaires depuis l'administration (/admin/), puis affectez vos employés à cet hôtel.",
            )
            return redirect("board")
        except ValidationError as e:
            for m in e.messages:
                messages.error(request, m)
    return render(request, "hotels/new.html", {"page": "hotels"})
