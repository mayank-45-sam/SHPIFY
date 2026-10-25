"""Image storage helpers.

Local disk is used by default. On hosts without a persistent disk (for example
Render's free plan), set ``CLOUDINARY_URL`` and uploaded images are stored in
Cloudinary instead. As a zero-account fallback, set ``STORE_UPLOADS_IN_DB=1``
and uploads are kept as compressed rows in the app's database.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, BinaryIO

from flask import current_app
from PIL import Image, UnidentifiedImageError

REMOTE_PREFIXES = ("http://", "https://")
DB_PREFIX = "db:"
MAX_CUSTOM_EDGE = 2000
CUSTOM_JPEG_QUALITY = 90


def is_remote(value: Any) -> bool:
    return bool(value) and str(value).startswith(REMOTE_PREFIXES)


def is_db_ref(value: Any) -> bool:
    return bool(value) and str(value).startswith(DB_PREFIX)


def media_url(value: Any, kind: str = "products") -> str:
    """Return a browser URL for a stored image reference."""
    if not value:
        return ""
    value = str(value)
    if is_remote(value):
        return value
    if is_db_ref(value):
        return f"/media/{value[len(DB_PREFIX):]}"
    folder = "custom" if kind == "custom" else "products"
    return f"/static/img/{folder}/{value}"


def download_url(value: Any) -> str:
    """Ask Cloudinary to serve the image as an attachment, if applicable."""
    if not value:
        return ""
    value = str(value)
    if (
        "res.cloudinary.com" in value
        and "/upload/" in value
        and "/fl_attachment/" not in value
    ):
        return value.replace("/upload/", "/upload/fl_attachment/", 1)
    return value


def cloudinary_enabled() -> bool:
    return bool((current_app.config.get("CLOUDINARY_URL") or "").strip())


def db_storage_enabled() -> bool:
    return bool(current_app.config.get("STORE_UPLOADS_IN_DB"))


def _store_in_db(data: bytes, filename: str, content_type: str = "image/jpeg") -> str:
    import db

    db.execute(
        "INSERT INTO uploaded_images (filename, content, content_type, created_at) "
        "VALUES (?, ?, ?, ?)",
        (filename, data, content_type, db.utcnow()),
    )
    return f"{DB_PREFIX}{filename}"


def load_db_image(filename: str):
    """Return ``(bytes, content_type)`` for a database-backed upload."""
    import db

    row = db.query_one(
        "SELECT content, content_type FROM uploaded_images WHERE filename = ?",
        (filename,),
    )
    if row is None:
        return None
    content = row["content"]
    if isinstance(content, memoryview):
        content = content.tobytes()
    return bytes(content), row["content_type"] or "image/jpeg"


def _compress_custom_image(data: bytes) -> bytes:
    """Resize a customer photo so database storage stays reasonably small."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.mode in ("RGBA", "P", "LA"):
                background = Image.new("RGB", image.size, (255, 255, 255))
                converted = image.convert("RGBA")
                background.paste(converted, mask=converted.split()[-1])
                image = background
            else:
                image = image.convert("RGB")

            if max(image.size) > MAX_CUSTOM_EDGE:
                image.thumbnail((MAX_CUSTOM_EDGE, MAX_CUSTOM_EDGE), Image.LANCZOS)

            buffer = io.BytesIO()
            image.save(
                buffer,
                "JPEG",
                quality=CUSTOM_JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("That file could not be read as an image.") from exc


def _upload(file_obj: BinaryIO, kind: str, public_id: str) -> str:
    try:
        import cloudinary
        import cloudinary.uploader

        cloudinary.config(
            cloudinary_url=current_app.config["CLOUDINARY_URL"].strip(),
            secure=True,
        )
        folder = (current_app.config.get("CLOUDINARY_FOLDER") or "shpify").strip("/")
        if folder:
            folder = f"{folder}/{kind}"
        else:
            folder = kind
        result = cloudinary.uploader.upload(
            file_obj,
            folder=folder,
            public_id=public_id,
            resource_type="image",
            overwrite=False,
        )
        return result["secure_url"]
    except Exception as exc:  # pragma: no cover - network/service failure
        raise ValueError(
            "The image could not be uploaded to Cloudinary. "
            "Check CLOUDINARY_URL and try again."
        ) from exc


def store_custom_image(file_storage, filename: str) -> str:
    """Store a customer's front/back photo and return its reference."""
    if cloudinary_enabled():
        file_storage.stream.seek(0)
        return _upload(file_storage.stream, "custom", os.path.splitext(filename)[0])

    if db_storage_enabled():
        data = file_storage.read()
        return _store_in_db(_compress_custom_image(data), filename)

    upload_dir = Path(current_app.root_path) / "static" / "img" / "custom"
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_storage.save(upload_dir / filename)
    return filename


def store_product_image(data: bytes, filename: str) -> str:
    """Store a processed product image and return its reference."""
    if cloudinary_enabled():
        return _upload(io.BytesIO(data), "products", os.path.splitext(filename)[0])

    if db_storage_enabled():
        return _store_in_db(data, filename)

    target = Path(current_app.config["UPLOAD_DIR"]) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return filename
