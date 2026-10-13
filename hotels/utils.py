import io

from django.core.files.uploadedfile import InMemoryUploadedFile
from django.utils import timezone
from django.utils.dateparse import parse_datetime


def parse_local(value):
    """'2026-09-19T14:30' (saisie datetime-local) -> datetime aware, ou None."""
    if not value:
        return None
    dt = parse_datetime(value)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def to_int(value, default=0):
    try:
        return int(str(value).replace(" ", "").replace("\u202f", "") or default)
    except (TypeError, ValueError):
        return default


def darken_hex(hex_color, factor=0.82):
    """'#0B7A86' -> version l\u00e9g\u00e8rement plus sombre (survol/accent), m\u00eame format."""
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return hex_color
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return hex_color
    r, g, b = (max(0, int(c * factor)) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def compress_image(uploaded, max_side=1600, quality=78):
    """Redimensionne et compresse une image envoyée par formulaire (logo, pièce d'identité,
    justificatif...) avant enregistrement. Retourne le fichier inchangé si ce n'est pas une
    image exploitable (Pillow absent, format inconnu) plutôt que de bloquer l'envoi."""
    if not uploaded:
        return uploaded
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return uploaded
    try:
        uploaded.seek(0)
        img = Image.open(uploaded)
        img = ImageOps.exif_transpose(img)
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        fmt = "PNG" if has_alpha else "JPEG"
        if not has_alpha and img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        if fmt == "JPEG":
            img.save(buf, format="JPEG", quality=quality, optimize=True)
        else:
            img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        name = uploaded.name.rsplit(".", 1)[0] + (".jpg" if fmt == "JPEG" else ".png")
        content_type = "image/jpeg" if fmt == "JPEG" else "image/png"
        return InMemoryUploadedFile(buf, None, name, content_type, buf.getbuffer().nbytes, None)
    except Exception:
        try:
            uploaded.seek(0)
        except Exception:
            pass
        return uploaded
