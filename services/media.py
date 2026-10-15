"""Image storage for UPI scanners (stored in Firebase as base64) and QR generation."""
import base64
import hashlib
import io
from urllib.parse import quote

from PIL import Image

from core import db, utils
from core.errors import ApiError

Image.MAX_IMAGE_PIXELS = 25_000_000
MAX_UPLOAD = 2 * 1024 * 1024


def save_scanner(raw, uploaded_by=""):
    """Validate + re-encode an uploaded QR image (strips metadata, blocks non-images). Returns media id."""
    if not raw or len(raw) > MAX_UPLOAD:
        raise ApiError("Scanner image is missing or larger than 2 MB.")
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        raise ApiError("That file is not a valid image.")
    img.thumbnail((1000, 1000))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    mid = utils.new_id("med")
    db.set(f"/media/{mid}", {
        "id": mid, "kind": "upi_scanner", "mime": "image/png", "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "data": base64.b64encode(data).decode(),
        "created_at": utils.now(), "by": uploaded_by,
    })
    return mid


def get_media(media_id):
    rec = db.get(f"/media/{media_id}")
    if not rec or "data" not in rec:
        return None, None
    return base64.b64decode(rec["data"]), rec.get("mime", "image/png")


def make_upi_qr(upi_id, name, amount, txn_id):
    import qrcode

    uri = (f"upi://pay?pa={quote(upi_id)}&pn={quote(name or 'Merchant')}"
           f"&am={float(amount):.2f}&cu=INR&tn={quote(txn_id)}")
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
