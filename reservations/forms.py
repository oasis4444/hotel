from django import forms

from hotels.utils import compress_image

from .models import Client


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["last_name", "first_name", "birth_date", "nationality", "phone", "address",
                  "id_type", "id_number", "id_photo", "signature", "notes"]
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "signature": forms.HiddenInput(),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "id_photo": forms.ClearableFileInput(attrs={"accept": "image/*", "capture": "environment"}),
        }

    def clean_id_photo(self):
        photo = self.cleaned_data.get("id_photo")
        if photo and hasattr(photo, "content_type"):  # nouveau fichier envoyé (pas un FieldFile existant)
            return compress_image(photo, max_side=1600, quality=80)
        return photo
