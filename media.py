"""Image storage helpers.

Local disk is used by default. On hosts without a persistent disk (for example
Render's free plan), set ``CLOUDINARY_URL`` and uploaded images are stored in
Cloudinary instead.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, BinaryIO

from flask import current_app

REMOTE_PREFIXES = ("http://", "https://")


def is_remote(value: Any) -> bool:
    return bool(value) and str(value).startswith(REMOTE_PREFIXES)


def media_url(value: Any, kind: str = "products") -> str:
    """Return a browser URL for a stored image reference."""
    if not value:
        return ""
    value = str(value)
    if is_remote(value):
        return value
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

    upload_dir = Path(current_app.root_path) / "static" / "img" / "custom"
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_storage.save(upload_dir / filename)
    return filename


def store_product_image(data: bytes, filename: str) -> str:
    """Store a processed product image and return its reference."""
    if cloudinary_enabled():
        return _upload(io.BytesIO(data), "products", os.path.splitext(filename)[0])

    target = Path(current_app.config["UPLOAD_DIR"]) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return filename
