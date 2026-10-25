"""Pincode serviceability and delivery estimates."""

from __future__ import annotations

import re
from typing import Tuple

from flask import current_app


def normalize(pincode: str) -> str:
    return re.sub(r"\D", "", pincode or "")


def serviceable(pincode: str) -> Tuple[bool, str, str]:
    """Return (serviceable, message, estimate)."""
    pin = normalize(pincode)
    if len(pin) != 6:
        return False, "Enter a valid 6-digit PIN code.", ""

    raw = (current_app.config.get("SERVICEABLE_PINCODES") or "").strip()
    if raw:
        allowed = [item.strip() for item in raw.split(",") if item.strip()]
        matches = any(pin == item or pin.startswith(item) for item in allowed)
        if not matches:
            return False, f"We do not deliver to {pin} yet.", ""

    low = int(current_app.config.get("DELIVERY_MIN_DAYS") or 4)
    high = int(current_app.config.get("DELIVERY_MAX_DAYS") or 7)
    estimate = f"{low}-{high} days" if low != high else f"{low} days"
    return True, f"Delivers to {pin} in {estimate}.", estimate
