"""Application configuration.

All secrets come from environment variables (loaded from a local .env file).
Nothing sensitive is ever hardcoded or exposed to the browser.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
UPLOAD_DIR = BASE_DIR / "static" / "img" / "products"

load_dotenv(BASE_DIR / ".env")


def env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value.strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env_str(name, "").lower()
    if raw == "":
        return default
    return raw in ("1", "true", "yes", "on")


def _stable_dev_secret() -> str:
    """Keep sessions alive across restarts in development.

    In production SECRET_KEY must be supplied via the environment, otherwise a
    fresh random key is generated each start (which logs everyone out).
    """
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    key_file = INSTANCE_DIR / "secret_key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    key_file.write_text(key, encoding="utf-8")
    return key


class Config:
    """Base configuration shared by every environment."""

    BASE_DIR = BASE_DIR
    INSTANCE_DIR = INSTANCE_DIR
    UPLOAD_DIR = UPLOAD_DIR

    SECRET_KEY = env_str("SECRET_KEY") or _stable_dev_secret()

    # --- Database -----------------------------------------------------------
    DATABASE = INSTANCE_DIR / "store.db"
    DATABASE_PATH = str(DATABASE)

    # A PostgreSQL connection string switches the whole app over to
    # PostgreSQL. Leave it empty to use the SQLite file above.
    #   DATABASE_URL=postgresql://user:password@localhost:5432/shopify
    DATABASE_URL = env_str("DATABASE_URL", "")
    # 0 = one connection per request. Set it (for example 10) to keep a pool
    # of connections open, which is what a real server should do.
    DATABASE_POOL_SIZE = env_int("DATABASE_POOL_SIZE", 0)

    # --- Storefront ---------------------------------------------------------
    STORE_NAME = env_str("STORE_NAME", "Playbook Tees")
    STORE_TAGLINE = env_str(
        "STORE_TAGLINE", "Graphic tees for people who never sit out"
    )
    STORE_EMAIL = env_str("STORE_EMAIL", "hello@example.com")
    STORE_PHONE = env_str("STORE_PHONE", "+91 90000 00000")
    STORE_ADDRESS = env_str("STORE_ADDRESS", "India")
    STORE_CURRENCY = env_str("STORE_CURRENCY", "INR")

    DEFAULT_TEE_PRICE = env_int("DEFAULT_TEE_PRICE", 599)
    SHIPPING_FEE = env_int("SHIPPING_FEE", 49)
    FREE_SHIPPING_THRESHOLD = env_int("FREE_SHIPPING_THRESHOLD", 999)

    # --- Admin --------------------------------------------------------------
    ADMIN_USERNAME = env_str("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD_HASH = env_str("ADMIN_PASSWORD_HASH", "")

    # --- Payments (Razorpay) ------------------------------------------------
    RAZORPAY_KEY_ID = env_str("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET = env_str("RAZORPAY_KEY_SECRET", "")
    RAZORPAY_WEBHOOK_SECRET = env_str("RAZORPAY_WEBHOOK_SECRET", "")

    # --- Payments (UPI) -----------------------------------------------------
    UPI_ID = env_str("UPI_ID", "")
    UPI_PAYEE_NAME = env_str("UPI_PAYEE_NAME", "")
    UPI_QR_IMAGE = env_str("UPI_QR_IMAGE", "img/payment/upi_qr.jpg")

    # --- Payments (Cash on delivery) ----------------------------------------
    COD_ENABLED = env_bool("COD_ENABLED", False)
    # A small handling fee added to COD orders, in rupees.
    COD_FEE = env_int("COD_FEE", 0)
    # Optional advance the customer pays online, as a percentage of the total.
    COD_ADVANCE_PERCENT = env_int("COD_ADVANCE_PERCENT", 0)

    # --- Checkout protection (OTP) ------------------------------------------
    OTP_REQUIRED = env_bool("OTP_REQUIRED", False)
    OTP_TTL_SECONDS = env_int("OTP_TTL_SECONDS", 600)
    # Optional SMS gateway. Receives {"phone": ..., "message": ...} as JSON.
    SMS_WEBHOOK_URL = env_str("SMS_WEBHOOK_URL", "")

    # --- Delivery -----------------------------------------------------------
    # Comma separated 6-digit pincodes or 3-digit prefixes. Empty = all India.
    SERVICEABLE_PINCODES = env_str("SERVICEABLE_PINCODES", "")
    DELIVERY_MIN_DAYS = env_int("DELIVERY_MIN_DAYS", 4)
    DELIVERY_MAX_DAYS = env_int("DELIVERY_MAX_DAYS", 7)

    # --- Invoices -----------------------------------------------------------
    STORE_LEGAL_NAME = env_str("STORE_LEGAL_NAME", "")
    STORE_GSTIN = env_str("STORE_GSTIN", "")
    GST_PERCENT = env_int("GST_PERCENT", 5)
    INVOICE_PREFIX = env_str("INVOICE_PREFIX", "INV")

    # --- Notifications ------------------------------------------------------
    SMTP_HOST = env_str("SMTP_HOST", "")
    SMTP_PORT = env_int("SMTP_PORT", 587)
    SMTP_USER = env_str("SMTP_USER", "")
    SMTP_PASSWORD = env_str("SMTP_PASSWORD", "")
    SMTP_FROM = env_str("SMTP_FROM", "")
    SMTP_USE_TLS = env_bool("SMTP_USE_TLS", True)
    # Fallback inbox for new-order alerts when no customer email can be used.
    ORDER_ALERT_EMAIL = env_str("ORDER_ALERT_EMAIL", "")
    WHATSAPP_NUMBER = env_str("WHATSAPP_NUMBER", "")

    # --- Cart / marketing ---------------------------------------------------
    ABANDONED_CART_HOURS = env_int("ABANDONED_CART_HOURS", 2)
    REVIEWS_AUTO_APPROVE = env_bool("REVIEWS_AUTO_APPROVE", False)

    # --- Supplier (Qikink) ---------------------------------------------------
    QIKINK_CLIENT_ID = env_str("QIKINK_CLIENT_ID", "")
    QIKINK_CLIENT_SECRET = env_str("QIKINK_CLIENT_SECRET", "")
    QIKINK_SANDBOX_SECRET = env_str("QIKINK_SANDBOX_SECRET", "")
    QIKINK_SANDBOX_MODE = env_bool("QIKINK_SANDBOX_MODE", False)
    QIKINK_API_BASE = env_str(
        "QIKINK_API_BASE",
        "https://sandbox.qikink.com"
        if env_bool("QIKINK_SANDBOX_MODE", False)
        else "https://api.qikink.com",
    )
    QIKINK_WEBHOOK_SECRET = env_str("QIKINK_WEBHOOK_SECRET", "")

    # --- Uploads ------------------------------------------------------------
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB per request
    ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    # Cloudinary keeps uploads when the host has no persistent disk.
    # Format: cloudinary://<api_key>:<api_secret>@<cloud_name>
    CLOUDINARY_URL = env_str("CLOUDINARY_URL", "")
    CLOUDINARY_FOLDER = env_str("CLOUDINARY_FOLDER", "shpify")

    # --- Behaviour ----------------------------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12

    @staticmethod
    def init_app(app):
        INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
