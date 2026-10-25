"""One-time codes for checkout phone verification."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Tuple

from flask import current_app

from db import execute, query_one, utcnow
from notifications import send_sms


def _hash(phone: str, code: str) -> str:
    secret = str(current_app.config.get("SECRET_KEY") or "").encode()
    body = f"{phone}:{code}".encode()
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def send_code(phone: str, purpose: str = "checkout") -> Tuple[bool, str, str]:
    """Create and send a code. Returns (ok, message, dev_code)."""
    phone = (phone or "").strip()
    if not phone.isdigit() or len(phone) != 10:
        return False, "Enter a valid 10-digit mobile number.", ""

    code = f"{secrets.randbelow(1000000):06d}"
    ttl = int(current_app.config.get("OTP_TTL_SECONDS") or 600)
    expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()

    execute("DELETE FROM otp_codes WHERE phone = ? AND purpose = ?", (phone, purpose))
    execute(
        """INSERT INTO otp_codes (phone, code_hash, purpose, attempts, expires_at, created_at)
           VALUES (?,?,?,0,?,?)""",
        (phone, _hash(phone, code), purpose, expires, utcnow()),
    )

    message = f"Your {current_app.config['STORE_NAME']} verification code is {code}. It expires in {ttl // 60} minutes."
    sent = send_sms(phone, message)
    dev_code = "" if sent and current_app.config.get("SMS_WEBHOOK_URL") else code
    if sent:
        return True, f"We sent a code to {phone}.", ""
    return True, f"Code generated for {phone} (no SMS gateway configured).", dev_code


def verify_code(phone: str, code: str, purpose: str = "checkout") -> Tuple[bool, str]:
    phone = (phone or "").strip()
    code = (code or "").strip()
    if not phone or not code:
        return False, "Enter the code we sent you."

    row = query_one(
        "SELECT * FROM otp_codes WHERE phone = ? AND purpose = ? ORDER BY id DESC LIMIT 1",
        (phone, purpose),
    )
    if row is None:
        return False, "Request a new code."
    record = dict(row)
    try:
        expires = datetime.fromisoformat(record["expires_at"])
    except ValueError:
        return False, "Request a new code."
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return False, "That code has expired. Request a new one."
    if int(record["attempts"]) >= 5:
        return False, "Too many attempts. Request a new code."
    if not hmac.compare_digest(record["code_hash"], _hash(phone, code)):
        execute("UPDATE otp_codes SET attempts = attempts + 1 WHERE id = ?", (record["id"],))
        return False, "That code is not correct."

    execute("DELETE FROM otp_codes WHERE phone = ? AND purpose = ?", (phone, purpose))
    return True, "Phone verified."
