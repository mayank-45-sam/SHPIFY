"""Pricing and shipping rules.

Every amount in the system is stored as integer paise, so totals are exact.
Prices are always read from the database - never from a form field - so a
customer cannot change what they pay by editing a request.
"""

from __future__ import annotations

from typing import Any, Dict, List

from flask import current_app


def shipping_for(subtotal_cents: int) -> int:
    """Shipping charge in paise for a given subtotal."""
    fee = current_app.config["SHIPPING_FEE"]
    threshold = current_app.config["FREE_SHIPPING_THRESHOLD"]
    if subtotal_cents <= 0:
        return 0
    if threshold and subtotal_cents >= threshold * 100:
        return 0
    return fee * 100


def free_shipping_threshold_cents() -> int:
    return current_app.config["FREE_SHIPPING_THRESHOLD"] * 100


def shipping_message(subtotal_cents: int) -> str:
    """A short line telling the customer where they stand on free shipping."""
    threshold = current_app.config["FREE_SHIPPING_THRESHOLD"]
    if not threshold:
        return ""
    if subtotal_cents >= threshold * 100:
        return "You have unlocked free shipping."
    remaining = threshold * 100 - subtotal_cents
    return f"Add ₹{remaining // 100 if remaining % 100 == 0 else remaining / 100:.2f} more for free shipping."


def build_totals(lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Work out subtotal, shipping and total for a list of cart lines."""
    subtotal = sum(int(line["unit_price_cents"]) * int(line["quantity"]) for line in lines)
    shipping = shipping_for(subtotal)
    return {
        "subtotal_cents": subtotal,
        "shipping_cents": shipping,
        "total_cents": subtotal + shipping,
        "item_count": sum(int(line["quantity"]) for line in lines),
        "free_shipping_threshold_cents": free_shipping_threshold_cents(),
        "shipping_message": shipping_message(subtotal),
    }
