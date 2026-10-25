"""Print-on-demand supplier integration (Qikink).

The supplier is **not** connected out of the box. Nothing in this module
pretends an order was sent to a printer. When credentials are missing the
admin area says so plainly and the site refuses to submit supplier orders.

API shape (from Qikink's Custom API documentation):
    POST {base}/api/token       form-urlencoded: ClientId, client_secret
                                -> {"Accesstoken": "...", "expires_in": 3600}
    POST {base}/api/order/create
                                headers: ClientId, Accesstoken
                                JSON body with order_number, line_items, shipping_address

Qikink has two environments that do NOT share a product catalogue:
    sandbox: https://sandbox.qikink.com
    live:    https://api.qikink.com
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests
from flask import current_app

#: Cached access token: {"token": str, "expires_at": float}
_token_cache: Dict[str, Any] = {}


class SupplierNotConfigured(Exception):
    """Raised when the supplier is asked to do something without credentials."""


class SupplierError(Exception):
    """Raised when the supplier rejects a request."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def base_url() -> str:
    return current_app.config.get("QIKINK_API_BASE", "https://api.qikink.com").rstrip("/")


def sandbox_mode() -> bool:
    return bool(current_app.config.get("QIKINK_SANDBOX_MODE"))


def client_id() -> str:
    return current_app.config.get("QIKINK_CLIENT_ID", "")


def client_secret() -> str:
    if sandbox_mode():
        return current_app.config.get("QIKINK_SANDBOX_SECRET", "")
    return current_app.config.get("QIKINK_CLIENT_SECRET", "")


def supplier_configured() -> bool:
    """True when the minimum credentials for order submission are present."""
    return bool(client_id() and client_secret())


def missing_supplier_settings() -> List[str]:
    missing = []
    if not client_id():
        missing.append("QIKINK_CLIENT_ID")
    if not client_secret():
        missing.append(
            "QIKINK_SANDBOX_SECRET" if sandbox_mode() else "QIKINK_CLIENT_SECRET"
        )
    return missing


def supplier_status() -> Dict[str, Any]:
    """A status report the admin area renders. Never contains secrets."""
    note = (
        "Supplier connected. Orders can be pushed for printing."
        if supplier_configured()
        else "Supplier not connected. Add the credentials in .env, then restart "
        "the server. Orders can still be placed on the website and fulfilled "
        "manually."
    )
    return {
        "configured": supplier_configured(),
        "sandbox": sandbox_mode(),
        "base_url": base_url(),
        "missing": missing_supplier_settings(),
        "can_submit_orders": supplier_configured(),
        "note": note,
        "message": note,
    }


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def get_access_token(force_refresh: bool = False) -> str:
    """Fetch (and cache) a Qikink access token."""
    if not supplier_configured():
        raise SupplierNotConfigured(
            "Supplier credentials are missing: " + ", ".join(missing_supplier_settings())
        )

    now = time.time()
    if (
        not force_refresh
        and _token_cache.get("token")
        and _token_cache.get("expires_at", 0) > now + 60
    ):
        return _token_cache["token"]

    try:
        response = requests.post(
            f"{base_url()}/api/token",
            data={"ClientId": client_id(), "client_secret": client_secret()},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
    except requests.RequestException as exc:
        raise SupplierError(f"Could not reach the supplier: {exc}") from exc

    if response.status_code != 200:
        raise SupplierError(
            f"Supplier auth failed (HTTP {response.status_code}). Check your Client ID "
            "and secret, and that you are using the matching environment."
        )

    data = response.json()
    token = data.get("Accesstoken")
    if not token:
        raise SupplierError("Supplier did not return an access token.")

    _token_cache["token"] = token
    _token_cache["expires_at"] = now + int(data.get("expires_in", 3600))
    return token


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


def build_supplier_payload(order: Dict[str, Any], lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Turn a stored order into a Qikink order payload.

    The order is the flat row the database returns, so the fields are read
    straight from it. A nested ``customer`` dict is also accepted.
    """
    customer = order.get("customer") or order

    items = []
    for line in lines:
        items.append(
            {
                # 1 = look the product up in the supplier's own catalogue by SKU.
                "search_from_my_products": 1,
                "sku": line.get("supplier_sku") or line.get("sku") or "",
                "quantity": str(line.get("quantity", 1)),
                "price": f"{line.get('unit_price_cents', 0) / 100:.2f}",
            }
        )

    name_parts = (customer.get("customer_name") or "Customer").strip().split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    return {
        # Qikink rejects order numbers longer than 15 characters.
        "order_number": order["order_number"].replace("-", "")[:15],
        "qikink_shipping": "1",
        "gateway": "Prepaid",
        "total_order_value": f"{order['total_cents'] / 100:.2f}",
        "line_items": items,
        # Qikink names the address fields address1/address2 and requires the
        # phone and email of the recipient. Sending "address" gets the whole
        # order rejected with "Unexpected fields in shipping_address".
        "shipping_address": {
            "first_name": first_name,
            "last_name": last_name,
            "address1": customer.get("address_line1", ""),
            "address2": customer.get("address_line2", ""),
            "phone": customer.get("phone", ""),
            "email": customer.get("email", ""),
            "city": customer.get("city", ""),
            "province": customer.get("state", ""),
            "zip": customer.get("pincode", ""),
            "country_code": "IN",
        },
    }


def submit_order(order: Dict[str, Any], lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Push an order to the supplier for printing.

    Raises SupplierNotConfigured when credentials are missing, so the admin
    area can tell the truth instead of pretending the order was printed.
    """
    if not supplier_configured():
        raise SupplierNotConfigured(
            "Supplier credentials are missing: " + ", ".join(missing_supplier_settings())
        )

    token = get_access_token()
    payload = build_supplier_payload(order, lines)

    try:
        response = requests.post(
            f"{base_url()}/api/order/create",
            json=payload,
            headers={"ClientId": client_id(), "Accesstoken": token},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise SupplierError(f"Could not reach the supplier: {exc}") from exc

    if response.status_code not in (200, 201):
        raise SupplierError(
            f"Supplier rejected the order (HTTP {response.status_code}): {response.text[:400]}"
        )

    return response.json()


def check_connection() -> Dict[str, Any]:
    """Try to authenticate, so the admin area can show a live status."""
    if not supplier_configured():
        return {
            "ok": False,
            "message": "Not configured. Missing: " + ", ".join(missing_supplier_settings()),
        }
    try:
        get_access_token(force_refresh=True)
    except (SupplierError, SupplierNotConfigured) as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": f"Authenticated against {base_url()}."}
