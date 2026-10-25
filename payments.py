"""Payment layer.

The store can take money in three ways, and it always knows which ones it has:

* **UPI (a QR code).** The customer scans the QR with any UPI app and pays
  the exact order total. The order is saved as unpaid, and it is only marked
  paid once the shop owner confirms the money in the admin area. A QR code
  cannot be verified automatically, so the store never pretends otherwise.
* **Razorpay.** The browser opens Razorpay Checkout, and the payment is only
  marked as paid after the backend verifies the HMAC signature of the payment
  response (and, separately, the webhook).
* **Cash on delivery.** The customer pays the courier when the parcel arrives.
  The order is confirmed immediately and stays marked ``payment_status='cod'``
  until the shop owner collects the cash and confirms it in the admin area.

* **Test mode** - nothing is available. No money can move and no fake success
  screen is shown. The order is stored with ``payment_status = 'test_mode'``
  and every screen says so in plain words.

There is no mode where an order is marked paid without a verified signature or
a confirmation from the shop owner.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

import requests
from flask import current_app

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"

#: Payment methods the checkout knows how to offer.
PAYMENT_METHODS = ("upi", "razorpay", "cod")


class PaymentError(Exception):
    """Raised when a payment could not be created or verified."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def payments_configured() -> bool:
    """True when real Razorpay credentials are available."""
    return bool(
        current_app.config.get("RAZORPAY_KEY_ID")
        and current_app.config.get("RAZORPAY_KEY_SECRET")
    )


def cod_enabled() -> bool:
    """True when the shop accepts cash on delivery."""
    return bool(current_app.config.get("COD_ENABLED"))


# ---------------------------------------------------------------------------
# UPI (QR code)
# ---------------------------------------------------------------------------


def upi_qr_path() -> str:
    """Absolute path of the UPI QR image, or "" when the file is missing."""
    static_folder = current_app.static_folder or ""
    relative = (current_app.config.get("UPI_QR_IMAGE") or "").strip().lstrip("/")
    if not static_folder or not relative:
        return ""
    path = Path(static_folder) / relative
    return str(path) if path.is_file() else ""


def upi_qr_url() -> str:
    """URL of the UPI QR image for the browser."""
    relative = (current_app.config.get("UPI_QR_IMAGE") or "").strip().lstrip("/")
    return f"/static/{relative}" if relative else ""


def upi_configured() -> bool:
    """True when a UPI id and a readable QR code are both in place."""
    return bool(current_app.config.get("UPI_ID")) and bool(upi_qr_path())


def upi_details() -> Dict[str, str]:
    """Everything the browser needs to show the customer how to pay."""
    return {
        "id": current_app.config.get("UPI_ID") or "",
        "payee_name": current_app.config.get("UPI_PAYEE_NAME") or "",
        "qr_url": upi_qr_url(),
    }


def _amount_for_upi(amount_cents: int) -> str:
    """Rupee amount for a UPI link: 59900 -> '599', 64850 -> '648.50'.

    No thousand separators: a comma in the amount breaks some UPI apps.
    """
    amount = int(amount_cents) / 100
    if amount == int(amount):
        return str(int(amount))
    return f"{amount:.2f}"


def upi_link(amount_cents: int, note: str = "") -> str:
    """A UPI deep link that already carries the exact amount.

    The customer scans this and the UPI app opens with the amount filled in,
    so nobody has to type it (and nobody can type the wrong one).
    """
    vpa = (current_app.config.get("UPI_ID") or "").strip()
    if not vpa:
        return ""

    params = {"pa": vpa, "cu": "INR", "am": _amount_for_upi(amount_cents)}
    payee = (current_app.config.get("UPI_PAYEE_NAME") or "").strip()
    if payee:
        params["pn"] = payee
    if note:
        params["tn"] = note

    # quote_via=quote so a space becomes %20 instead of '+', which some UPI
    # apps read as a plus sign.
    return "upi://pay?" + urlencode(params, quote_via=quote)


def upi_qr_data_uri(amount_cents: int, note: str = "") -> str:
    """A PNG QR code (as a data URI) for one exact amount.

    Returns "" when the QR cannot be made, so the caller can fall back to the
    static QR image.
    """
    link = upi_link(amount_cents, note)
    if not link:
        return ""

    try:
        import segno
    except ImportError:
        return ""

    try:
        buffer = io.BytesIO()
        segno.make(link).save(buffer, kind="png", scale=6, border=2, dark="#000000")
    except Exception:
        return ""

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


# ---------------------------------------------------------------------------
# Which methods can this store offer?
# ---------------------------------------------------------------------------


def available_methods() -> List[Dict[str, str]]:
    """The payment methods this store can actually take money with."""
    methods: List[Dict[str, str]] = []
    if upi_configured():
        methods.append(
            {
                "id": "upi",
                "label": "UPI",
                "detail": "Scan the QR code with any UPI app and pay the exact amount.",
            }
        )
    if payments_configured():
        methods.append(
            {
                "id": "razorpay",
                "label": "Card, netbanking or UPI",
                "detail": "Pay securely with Razorpay. Verified automatically.",
            }
        )
    if cod_enabled():
        methods.append(
            {
                "id": "cod",
                "label": "Cash on delivery",
                "detail": "Pay in cash when your order is delivered.",
            }
        )
    return methods


def method_ids() -> List[str]:
    return [method["id"] for method in available_methods()]


def default_method() -> str:
    """The method used when the customer does not pick one."""
    methods = method_ids()
    return methods[0] if methods else ""


def live_payments_available() -> bool:
    """True when the store can take at least one real payment."""
    return bool(available_methods())


def razorpay_missing_settings() -> list:
    missing = []
    if not current_app.config.get("RAZORPAY_KEY_ID"):
        missing.append("RAZORPAY_KEY_ID")
    if not current_app.config.get("RAZORPAY_KEY_SECRET"):
        missing.append("RAZORPAY_KEY_SECRET")
    return missing


def missing_payment_settings() -> list:
    """What is still missing before the store can take a single rupee."""
    if live_payments_available():
        return []
    missing = []
    if not current_app.config.get("UPI_ID"):
        missing.append("UPI_ID")
    elif not upi_qr_path():
        missing.append("UPI_QR_IMAGE")
    missing.extend(razorpay_missing_settings())
    return missing


def mode_label() -> str:
    return "live" if live_payments_available() else "test"


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def verify_payment_signature(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
) -> bool:
    """Verify the signature Razorpay sends back after a successful payment.

    This is the only thing that may mark an order as paid.
    """
    secret = current_app.config.get("RAZORPAY_KEY_SECRET") or ""
    if not secret:
        return False
    if not (razorpay_order_id and razorpay_payment_id and razorpay_signature):
        return False

    body = f"{razorpay_order_id}|{razorpay_payment_id}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, razorpay_signature)


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """Verify an incoming Razorpay webhook signature."""
    secret = current_app.config.get("RAZORPAY_WEBHOOK_SECRET") or ""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Razorpay order creation
# ---------------------------------------------------------------------------


def create_razorpay_order(
    amount_cents: int,
    receipt: str,
    notes: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Create a Razorpay order and return its id plus the public key.

    Raises PaymentError if Razorpay is not configured or rejects the request.
    """
    if not payments_configured():
        raise PaymentError("Razorpay is not configured on this server.")

    key_id = current_app.config["RAZORPAY_KEY_ID"]
    key_secret = current_app.config["RAZORPAY_KEY_SECRET"]

    payload = {
        "amount": int(amount_cents),
        "currency": "INR",
        "receipt": receipt,
        "notes": notes or {},
    }

    try:
        response = requests.post(
            f"{RAZORPAY_API_BASE}/orders",
            data=payload,
            auth=(key_id, key_secret),
            timeout=20,
        )
    except requests.RequestException as exc:
        raise PaymentError(f"Could not reach Razorpay: {exc}") from exc

    if response.status_code not in (200, 201):
        raise PaymentError(
            f"Razorpay rejected the order (HTTP {response.status_code}): {response.text[:300]}"
        )

    data = response.json()
    return {
        "order_id": data.get("id"),
        "amount": data.get("amount"),
        "currency": data.get("currency"),
        "key_id": key_id,
    }


def fetch_razorpay_payment(payment_id: str) -> Dict[str, Any]:
    """Fetch payment details from Razorpay (used to double-check a webhook)."""
    key_id = current_app.config["RAZORPAY_KEY_ID"]
    key_secret = current_app.config["RAZORPAY_KEY_SECRET"]
    try:
        response = requests.get(
            f"{RAZORPAY_API_BASE}/payments/{payment_id}",
            auth=(key_id, key_secret),
            timeout=20,
        )
    except requests.RequestException as exc:
        raise PaymentError(f"Could not reach Razorpay: {exc}") from exc
    if response.status_code != 200:
        raise PaymentError(f"Razorpay returned HTTP {response.status_code}.")
    return response.json()


def fetch_razorpay_order(razorpay_order_id: str) -> Dict[str, Any]:
    """Fetch an existing Razorpay order so it can be paid again."""
    key_id = current_app.config["RAZORPAY_KEY_ID"]
    key_secret = current_app.config["RAZORPAY_KEY_SECRET"]
    try:
        response = requests.get(
            f"{RAZORPAY_API_BASE}/orders/{razorpay_order_id}",
            auth=(key_id, key_secret),
            timeout=20,
        )
    except requests.RequestException as exc:
        raise PaymentError(f"Could not reach Razorpay: {exc}") from exc
    if response.status_code != 200:
        raise PaymentError(f"Razorpay returned HTTP {response.status_code}.")
    return response.json()


def extract_webhook_payment(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the details we need out of a Razorpay webhook body.

    Razorpay nests the payment under ``payload.payment.entity``. Older
    integrations used the flatter ``payload.payment`` shape, so both are
    accepted. Returns an empty ``order_number`` when nothing can be found.
    """
    payload = payload or {}
    payment = (payload.get("payload") or {}).get("payment") or {}
    entity = payment.get("entity") if isinstance(payment.get("entity"), dict) else payment
    if not isinstance(entity, dict):
        entity = {}

    notes = entity.get("notes")
    if not isinstance(notes, dict):
        notes = {}

    amount = entity.get("amount")
    try:
        amount = int(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount = None

    return {
        "order_number": str(notes.get("order_number") or "").strip(),
        "payment_id": str(entity.get("id") or "").strip(),
        "razorpay_order_id": str(entity.get("order_id") or "").strip(),
        "amount": amount,
        "currency": str(entity.get("currency") or "").strip(),
        "status": str(entity.get("status") or "").strip(),
    }


# ---------------------------------------------------------------------------
# Client-side checkout payload
# ---------------------------------------------------------------------------


def checkout_payload(
    order_number: str,
    total_cents: int,
    razorpay_order_id: str = "",
) -> Dict[str, Any]:
    """The data the browser needs to open the payment dialog.

    One store order keeps one gateway order: if the customer reloads the pay
    page or comes back later to pay, the same Razorpay order is reused instead
    of a new one being created each time.

    In test mode this returns an empty payload and the browser is told to
    show the test-mode notice instead of a payment form.
    """
    if not payments_configured():
        return {"mode": "test", "key_id": None, "order_number": order_number}

    razorpay_order = None
    if razorpay_order_id:
        try:
            existing = fetch_razorpay_order(razorpay_order_id)
        except PaymentError:
            existing = None
        if existing and existing.get("status") in ("created", "attempted"):
            if int(existing.get("amount") or 0) == int(total_cents):
                razorpay_order = existing

    if razorpay_order is None:
        razorpay_order = create_razorpay_order(
            total_cents,
            receipt=order_number,
            notes={"order_number": order_number},
        )

    return {
        "mode": "live",
        "key_id": current_app.config["RAZORPAY_KEY_ID"],
        "order_id": razorpay_order.get("id"),
        "amount": razorpay_order.get("amount") or int(total_cents),
        "currency": razorpay_order.get("currency") or "INR",
        "order_number": order_number,
        "callback_url": "/checkout/verify",
    }


def new_reference(prefix: str = "pay") -> str:
    return f"{prefix}_{secrets.token_hex(10)}"
