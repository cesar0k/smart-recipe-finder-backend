from io import BytesIO

import magic
from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.core.config import settings


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
