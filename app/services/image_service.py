from io import BytesIO

import magic
import pillow_heif
from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.core.config import settings

pillow_heif.register_heif_opener()

FULL_MAX_WIDTH = 1200
FULL_QUALITY = 85
THUMB_MAX_WIDTH = 400
THUMB_QUALITY = 60


async def validate_and_process_image(file: UploadFile) -> BytesIO:
    await file.seek(0)
    content: bytes = await file.read()
    file_size: int = len(content)

    if file_size > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max size is {settings.MAX_FILE_SIZE_MB}MB",
        )

    mime = magic.Magic(mime=True)
    real_content_type: str = mime.from_buffer(content[:2048])

    if real_content_type not in settings.ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type: {real_content_type}. Required: {settings.ALLOWED_IMAGE_TYPES}",
        )

    try:
        image = Image.open(BytesIO(content))

        if (
            image.width > settings.MAX_IMAGE_WIDTH
            or image.height > settings.MAX_IMAGE_HEIGHT
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Image resolution too high. Max {settings.MAX_IMAGE_WIDTH}x{settings.MAX_IMAGE_HEIGHT}",
            )

        return BytesIO(content)

    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="Invalid image file") from None


BROWSER_SAFE_FORMATS = {"JPEG", "PNG", "WEBP"}


def ensure_browser_compatible(content: bytes) -> tuple[BytesIO, str, str]:
    """Convert image to JPEG if its format is not natively supported by browsers.

    Returns (file_bytes, content_type, extension).
    """
    img = Image.open(BytesIO(content))
    fmt = (img.format or "").upper()

    if fmt in BROWSER_SAFE_FORMATS:
        ext = fmt.lower()
        if ext == "jpeg":
            ext = "jpg"
        ct = f"image/{fmt.lower()}"
        return BytesIO(content), ct, ext

    # Convert non-browser formats (HEIC, HEIF, TIFF, …) to JPEG
    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return buf, "image/jpeg", "jpg"


def _resize_to_webp(content: bytes, max_width: int, quality: int) -> BytesIO:
    """Resize image to max_width (preserving aspect ratio) and encode as WebP."""
    img = Image.open(BytesIO(content))
    img = img.convert("RGB")

    if img.width > max_width:
        ratio = max_width / img.width
        new_size = (max_width, int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    buf = BytesIO()
    img.save(buf, format="WEBP", quality=quality)
    buf.seek(0)
    return buf


def generate_compressed_versions(content: bytes) -> dict[str, BytesIO]:
    """
    Generate two compressed WebP versions from the original image bytes:
    - full: 1200px wide, quality 85
    - thumb: 400px wide, quality 60
    """
    return {
        "full": _resize_to_webp(content, FULL_MAX_WIDTH, FULL_QUALITY),
        "thumb": _resize_to_webp(content, THUMB_MAX_WIDTH, THUMB_QUALITY),
    }
