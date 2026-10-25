"""Shopping cart service and JSON API.

The cart lives in the database and is keyed by a token stored in the signed
session cookie. The browser also keeps a copy in localStorage so the drawer
survives a refresh instantly, and the local copy is synced to the server on
every page load. The server copy is always the source of truth: prices come
from the database and stock is re-checked before anything is ordered.
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request, session

import media
from catalog import get_product, size_availability
from db import execute, query_all
from pricing import build_totals
from security import validate_csrf

bp = Blueprint("cart", __name__, url_prefix="/api/cart")

MAX_QUANTITY_PER_LINE = 10
MAX_QUANTITY_TOTAL = 25


# ---------------------------------------------------------------------------
# Cart helpers
# ---------------------------------------------------------------------------


def cart_token() -> str:
    """Return (and create) the cart token for this browser session."""
    token = session.get("cart_token")
    if not token:
        token = secrets.token_urlsafe(24)
        session["cart_token"] = token
    return token


def cart_lines() -> List[Dict[str, Any]]:
    """Return the cart lines with fresh product data and live totals."""
    rows = query_all(
        """SELECT ci.id AS cart_id, ci.quantity AS quantity,
                  ci.custom_front AS custom_front, ci.custom_back AS custom_back,
                  v.id AS variant_id, v.size AS size, v.stock AS stock, v.sku AS sku,
                  v.price_cents AS variant_price_cents,
                  p.id AS product_id, p.name AS name, p.slug AS slug,
                  p.price_cents AS price_cents, p.status AS status,
                  p.stock_mode AS stock_mode, p.category AS category
           FROM cart_items ci
           JOIN product_variants v ON v.id = ci.variant_id
           JOIN products p ON p.id = ci.product_id
           WHERE ci.cart_token = ?
           ORDER BY ci.id""",
        (cart_token(),),
    )

    lines: List[Dict[str, Any]] = []
    for row in rows:
        product = get_product(row["product_id"])
        if product is None:
            continue
        image = product["primary_image"]
        custom_front = (row["custom_front"] or "").strip()
        custom_back = (row["custom_back"] or "").strip()
        custom_front_url = media.media_url(custom_front, "custom") or None
        custom_back_url = media.media_url(custom_back, "custom") or None
        available = row["stock"] > 0 if product["stock_mode"] == "tracked" else True
        unit_price = int(row["variant_price_cents"] or 0) or int(product["price_cents"])
        quantity = int(row["quantity"])
        lines.append(
            {
                "cart_id": row["cart_id"],
                "variant_id": row["variant_id"],
                "product_id": row["product_id"],
                "name": product["name"],
                "slug": product["slug"],
                "category": product["category"],
                "image": image,
                "image_url": custom_front_url
                or (media.media_url(image, "products") if image else None),
                "custom_front": custom_front,
                "custom_back": custom_back,
                "custom_front_url": custom_front_url,
                "custom_back_url": custom_back_url,
                "is_custom": bool(custom_front or custom_back),
                "size": row["size"],
                "sku": row["sku"],
                "unit_price_cents": unit_price,
                "unit_price_display": _rupees(unit_price),
                "quantity": quantity,
                "line_total_cents": unit_price * quantity,
                "line_total_display": _rupees(unit_price * quantity),
                "available": available and product["status"] == "published",
                "issue": _issue(product, row, available),
            }
        )
    return lines


def _issue(product: Dict[str, Any], row: Any, available: bool) -> str:
    """A short warning shown next to a cart line."""
    if product["status"] != "published":
        return "This product is no longer published."
    if product["stock_mode"] == "tracked" and not available:
        return "Size is out of stock."
    if product["stock_mode"] == "tracked" and row["stock"] < row["quantity"]:
        return f"Only {row['stock']} left in this size."
    return ""


def _rupees(cents: int) -> str:
    amount = cents / 100
    if amount == int(amount):
        return f"{int(amount):,}"
    return f"{amount:,.2f}"


def cart_payload() -> Dict[str, Any]:
    """The full cart object sent to the browser."""
    lines = cart_lines()
    totals = build_totals(lines)
    return {
        "lines": lines,
        "totals": totals,
        "item_count": totals["item_count"],
        "currency": "₹",
    }


def clear_cart() -> None:
    execute("DELETE FROM cart_items WHERE cart_token = ?", (cart_token(),))


def _parse_quantity(raw: Any) -> int:
    try:
        quantity = int(raw)
    except (TypeError, ValueError):
        raise ValueError("Quantity must be a whole number.")
    if quantity < 1:
        raise ValueError("Quantity must be at least 1.")
    if quantity > MAX_QUANTITY_PER_LINE:
        raise ValueError(f"Maximum {MAX_QUANTITY_PER_LINE} per product.")
    return quantity


def _total_quantity() -> int:
    row = query_all(
        "SELECT COALESCE(SUM(quantity), 0) AS n FROM cart_items WHERE cart_token = ?",
        (cart_token(),),
    )
    return int(row[0]["n"]) if row else 0


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@bp.get("")
def get_cart():
    return jsonify(cart_payload())


@bp.post("/add")
def add_to_cart():
    """Add a product to the cart. A size is always required."""
    validate_csrf()
    data = request.get_json(silent=True) or {}
    product_id = data.get("product_id")
    size = str(data.get("size") or "").strip()

    if not product_id:
        return jsonify({"ok": False, "error": "Missing product."}), 400
    if not size:
        return jsonify({"ok": False, "error": "Please choose a size first."}), 400

    product = get_product(int(product_id))
    if product is None or product["status"] != "published":
        return jsonify({"ok": False, "error": "This product is not available."}), 404

    variant = next(
        (v for v in product["variants"] if v["size"].casefold() == size.casefold()), None
    )
    if variant is None:
        return jsonify({"ok": False, "error": "Please choose a valid size."}), 400
    size = variant["size"]

    availability = size_availability(product)
    entry = availability.get(size)
    if entry is None or entry["missing"]:
        return jsonify({"ok": False, "error": "Please choose a valid size."}), 400
    if not entry["available"]:
        return jsonify({"ok": False, "error": f"Size {size} is not available."}), 409

    try:
        quantity = _parse_quantity(data.get("quantity", 1))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    existing = query_all(
        "SELECT id, quantity FROM cart_items WHERE cart_token = ? AND variant_id = ?",
        (cart_token(), variant["id"]),
    )
    already = int(existing[0]["quantity"]) if existing else 0
    new_quantity = already + quantity
    if new_quantity > MAX_QUANTITY_PER_LINE:
        return jsonify(
            {"ok": False, "error": f"Maximum {MAX_QUANTITY_PER_LINE} per product."}
        ), 400
    if _total_quantity() + quantity > MAX_QUANTITY_TOTAL:
        return jsonify(
            {"ok": False, "error": f"Maximum {MAX_QUANTITY_TOTAL} items per order."}
        ), 400

    if product["stock_mode"] == "tracked" and new_quantity > int(variant["stock"]):
        return jsonify(
            {"ok": False, "error": f"Only {variant['stock']} left in size {size}."}
        ), 409

    if existing:
        execute(
            "UPDATE cart_items SET quantity = ? WHERE id = ?",
            (new_quantity, existing[0]["id"]),
        )
    else:
        from db import utcnow

        execute(
            "INSERT INTO cart_items (cart_token, product_id, variant_id, quantity, created_at) "
            "VALUES (?,?,?,?,?)",
            (cart_token(), product["id"], variant["id"], new_quantity, utcnow()),
        )

    payload = cart_payload()
    payload["ok"] = True
    payload["message"] = f"{product['name']} (size {size}) added to your cart."
    payload["added_size"] = size
    payload["added_name"] = product["name"]
    payload["added_image"] = (
        media.media_url(product["primary_image"], "products")
        if product["primary_image"]
        else None
    )
    payload["added_unit_price_display"] = entry["price_display"]
    return jsonify(payload)


@bp.post("/update")
def update_cart():
    """Change the quantity of a cart line."""
    validate_csrf()
    data = request.get_json(silent=True) or {}
    cart_id = data.get("cart_id")
    raw_quantity = data.get("quantity")

    if not cart_id:
        return jsonify({"ok": False, "error": "Missing cart item."}), 400

    row = query_all(
        "SELECT ci.id, ci.quantity, v.stock, p.stock_mode, p.name "
        "FROM cart_items ci JOIN product_variants v ON v.id = ci.variant_id "
        "JOIN products p ON p.id = ci.product_id "
        "WHERE ci.id = ? AND ci.cart_token = ?",
        (cart_id, cart_token()),
    )
    if not row:
        return jsonify({"ok": False, "error": "That item is not in your cart."}), 404

    item = row[0]
    try:
        quantity = int(raw_quantity)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Quantity must be a whole number."}), 400

    if quantity <= 0:
        execute("DELETE FROM cart_items WHERE id = ?", (item["id"],))
        payload = cart_payload()
        payload["ok"] = True
        payload["message"] = "Item removed from your cart."
        return jsonify(payload)

    if quantity > MAX_QUANTITY_PER_LINE:
        return jsonify(
            {"ok": False, "error": f"Maximum {MAX_QUANTITY_PER_LINE} per product."}
        ), 400
    if item["stock_mode"] == "tracked" and quantity > int(item["stock"]):
        return jsonify(
            {"ok": False, "error": f"Only {item['stock']} left in this size."}
        ), 409

    execute("UPDATE cart_items SET quantity = ? WHERE id = ?", (quantity, item["id"]))
    payload = cart_payload()
    payload["ok"] = True
    payload["message"] = "Cart updated."
    return jsonify(payload)


@bp.post("/remove")
def remove_from_cart():
    validate_csrf()
    data = request.get_json(silent=True) or {}
    cart_id = data.get("cart_id")
    if not cart_id:
        return jsonify({"ok": False, "error": "Missing cart item."}), 400
    execute(
        "DELETE FROM cart_items WHERE id = ? AND cart_token = ?",
        (cart_id, cart_token()),
    )
    payload = cart_payload()
    payload["ok"] = True
    payload["message"] = "Item removed from your cart."
    return jsonify(payload)


@bp.post("/clear")
def clear_cart_endpoint():
    validate_csrf()
    clear_cart()
    payload = cart_payload()
    payload["ok"] = True
    payload["message"] = "Cart cleared."
    return jsonify(payload)


@bp.post("/sync")
def sync_cart():
    """Merge the browser's saved cart into the server cart.

    This is what makes the cart survive a refresh: localStorage is the fast
    copy, this endpoint reconciles it with the server copy on page load.
    """
    validate_csrf()
    data = request.get_json(silent=True) or {}
    items = data.get("items")
    if not isinstance(items, list):
        return jsonify({"ok": False, "error": "Invalid cart payload."}), 400

    for entry in items[:50]:
        try:
            product_id = int(entry.get("product_id"))
            size = str(entry.get("size") or "").strip()
            quantity = int(entry.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if quantity < 1:
            continue

        product = get_product(product_id)
        if product is None or product["status"] != "published":
            continue

        variant = next(
            (v for v in product["variants"] if v["size"].casefold() == size.casefold()), None
        )
        if variant is None:
            continue
        size = variant["size"]

        availability = size_availability(product)
        entry_info = availability.get(size)
        if entry_info is None or entry_info["missing"] or not entry_info["available"]:
            continue

        quantity = min(quantity, MAX_QUANTITY_PER_LINE)
        if product["stock_mode"] == "tracked":
            quantity = min(quantity, int(variant["stock"]))

        existing = query_all(
            "SELECT id, quantity FROM cart_items WHERE cart_token = ? AND variant_id = ?",
            (cart_token(), variant["id"]),
        )
        if existing:
            # localStorage is a mirror of the server cart, not a delta. Merging
            # by taking the larger value is idempotent, so reloading a page can
            # never inflate a single piece into two, four, eight...
            merged = min(max(int(existing[0]["quantity"]), quantity), MAX_QUANTITY_PER_LINE)
            if product["stock_mode"] == "tracked":
                merged = min(merged, int(variant["stock"]))
            execute("UPDATE cart_items SET quantity = ? WHERE id = ?", (merged, existing[0]["id"]))
        else:
            from db import utcnow

            execute(
                "INSERT INTO cart_items (cart_token, product_id, variant_id, quantity, created_at) "
                "VALUES (?,?,?,?,?)",
                (cart_token(), product["id"], variant["id"], quantity, utcnow()),
            )

    payload = cart_payload()
    payload["ok"] = True
    return jsonify(payload)
