"""Order service: creation, stock handling, tracking and status changes."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from db import execute, execute_many, query_all, query_one, utcnow

#: Order status values the admin area can set.
ORDER_STATUSES = (
    "new",
    "awaiting_payment",
    "confirmed",
    "processing",
    "packed",
    "shipped",
    "delivered",
    "cancelled",
    "refunded",
    "test_order",
)

STATUS_LABELS = {
    "new": "New",
    "awaiting_payment": "Awaiting payment",
    "confirmed": "Confirmed",
    "processing": "Processing",
    "packed": "Packed",
    "shipped": "Shipped",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
    "refunded": "Refunded",
    "test_order": "Test order",
}

PAYMENT_STATUSES = (
    "pending",
    "submitted",
    "paid",
    "failed",
    "refunded",
    "test_mode",
    "cod",
)

PAYMENT_LABELS = {
    "pending": "Not paid",
    "submitted": "Payment sent - to check",
    "paid": "Paid",
    "failed": "Not paid",
    "refunded": "Refunded",
    "test_mode": "Test order",
    "cod": "Cash on delivery",
}


def generate_order_number() -> str:
    """Human friendly, unique order number such as PT-260921-4K7QX."""
    stamp = datetime.now(timezone.utc).strftime("%y%m%d")
    while True:
        number = f"PT-{stamp}-{secrets.token_hex(3).upper()}"
        if query_one("SELECT id FROM orders WHERE order_number = ?", (number,)) is None:
            return number


def create_order(
    customer: Dict[str, str],
    lines: List[Dict[str, Any]],
    totals: Dict[str, Any],
    payment_method: str = "",
    payment_ref: str = "",
    payment_status: str = "pending",
    status: str = "new",
    notes: str = "",
    decrement_stock: bool = False,
    coupon_code: str = "",
    discount_cents: int = 0,
    cod_fee_cents: int = 0,
    cart_token: str = "",
    otp_verified: bool = False,
    delivery_estimate: str = "",
) -> Dict[str, Any]:
    """Store an order and its lines. Returns the created order row."""
    order_number = generate_order_number()
    now = utcnow()

    cursor = execute(
        """INSERT INTO orders
           (order_number, customer_name, phone, email, address_line1, address_line2,
            city, state, pincode, subtotal_cents, shipping_cents, total_cents,
            payment_status, payment_method, payment_ref, status, notes, created_at, updated_at,
            coupon_code, discount_cents, cod_fee_cents, cart_token, otp_verified,
            delivery_estimate)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            order_number,
            customer["customer_name"],
            customer["phone"],
            customer["email"],
            customer["address_line1"],
            customer.get("address_line2", ""),
            customer["city"],
            customer["state"],
            customer["pincode"],
            totals["subtotal_cents"],
            totals["shipping_cents"],
            totals["total_cents"],
            payment_status,
            payment_method,
            payment_ref,
            status,
            notes,
            now,
            now,
            coupon_code or "",
            int(discount_cents or 0),
            int(cod_fee_cents or 0),
            cart_token or "",
            1 if otp_verified else 0,
            delivery_estimate or "",
        ),
    )
    order_id = cursor.lastrowid

    rows = []
    for line in lines:
        rows.append(
            (
                order_id,
                line.get("product_id"),
                line.get("variant_id"),
                line["name"],
                line["size"],
                line.get("sku", ""),
                line.get("image") or "",
                line.get("custom_front", "") or "",
                line.get("custom_back", "") or "",
                int(line["unit_price_cents"]),
                int(line["quantity"]),
            )
        )
    execute_many(
        """INSERT INTO order_items
           (order_id, product_id, variant_id, product_name, size, sku, image_filename,
            custom_front, custom_back, unit_price_cents, quantity)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )

    add_order_event(order_id, status, "Order created.")

    if decrement_stock:
        decrement_stock_for_lines(lines)

    return get_order(order_id)


def decrement_stock_for_lines(lines: List[Dict[str, Any]]) -> None:
    """Reduce tracked stock. Made-to-order products have no stock to reduce."""
    from catalog import get_product

    for line in lines:
        product = get_product(int(line["product_id"]))
        if product is None or product.get("stock_mode") != "tracked":
            continue
        variant_id = line.get("variant_id")
        if not variant_id:
            continue
        execute(
            "UPDATE product_variants SET stock = MAX(stock - ?, 0) WHERE id = ?",
            (int(line["quantity"]), variant_id),
        )


def add_order_event(order_id: int, status: str, message: str = "") -> None:
    execute(
        "INSERT INTO order_events (order_id, status, message, created_at) VALUES (?,?,?,?)",
        (order_id, status, message, utcnow()),
    )


def get_order(order_id: int) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM orders WHERE id = ?", (order_id,))
    if row is None:
        return None
    order = dict(row)
    order["items"] = []
    for item_row in query_all(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,)
    ):
        item = dict(item_row)
        front = (item.get("custom_front") or "").strip()
        back = (item.get("custom_back") or "").strip()
        image = (item.get("image_filename") or "").strip()
        item["custom_front_url"] = f"/static/img/custom/{front}" if front else ""
        item["custom_back_url"] = f"/static/img/custom/{back}" if back else ""
        if front or back:
            item["image_url"] = item["custom_front_url"] or item["custom_back_url"]
        elif image:
            item["image_url"] = f"/static/img/products/{image}"
        else:
            item["image_url"] = ""
        order["items"].append(item)
    order["events"] = [
        dict(event)
        for event in query_all(
            "SELECT * FROM order_events WHERE order_id = ? ORDER BY id DESC", (order_id,)
        )
    ]
    order["status_label"] = STATUS_LABELS.get(order["status"], order["status"])
    order["payment_label"] = PAYMENT_LABELS.get(
        order["payment_status"], order["payment_status"]
    )
    return order


def get_order_by_number(order_number: str) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT id FROM orders WHERE order_number = ?", (order_number.strip(),))
    if row is None:
        return None
    return get_order(row["id"])


def get_order_by_razorpay_order_id(razorpay_order_id: str) -> Optional[Dict[str, Any]]:
    """Find the store order that a gateway order belongs to."""
    if not razorpay_order_id:
        return None
    row = query_one(
        "SELECT id FROM orders WHERE razorpay_order_id = ?", (razorpay_order_id.strip(),)
    )
    if row is None:
        return None
    return get_order(row["id"])


def set_razorpay_order_id(order_id: int, razorpay_order_id: str) -> None:
    """Remember the gateway order id, so one store order has one gateway order."""
    execute(
        "UPDATE orders SET razorpay_order_id = ?, updated_at = ? WHERE id = ?",
        ((razorpay_order_id or "").strip(), utcnow(), order_id),
    )


def list_orders(status: str = "", search: str = "") -> List[Dict[str, Any]]:
    where: List[str] = []
    params: List[Any] = []
    if status and status in ORDER_STATUSES:
        where.append("status = ?")
        params.append(status)
    if search:
        where.append("(order_number LIKE ? OR customer_name LIKE ? OR phone LIKE ? OR email LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like, like])
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = query_all(
        f"SELECT * FROM orders {clause} ORDER BY created_at DESC, id DESC", params
    )
    orders = [dict(row) for row in rows]
    counts = query_all("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")
    summary = {row["status"]: row["n"] for row in counts}
    for order in orders:
        order["status_label"] = STATUS_LABELS.get(order["status"], order["status"])
        order["payment_label"] = PAYMENT_LABELS.get(
            order["payment_status"], order["payment_status"]
        )
    return orders, summary


def update_order_status(order_id: int, status: str, message: str = "") -> bool:
    if status not in ORDER_STATUSES:
        return False
    execute(
        "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
        (status, utcnow(), order_id),
    )
    add_order_event(order_id, status, message or STATUS_LABELS.get(status, status))
    return True


def update_tracking(order_id: int, tracking_number: str, carrier: str = "") -> bool:
    execute(
        "UPDATE orders SET tracking_number = ?, carrier = ?, updated_at = ? WHERE id = ?",
        (tracking_number.strip(), carrier.strip(), utcnow(), order_id),
    )
    add_order_event(
        order_id,
        "shipped",
        f"Tracking updated: {tracking_number} {carrier}".strip(),
    )
    return True


def mark_paid(
    order_id: int,
    payment_ref: str,
    payment_method: str = "razorpay",
    decrement_stock: bool = True,
) -> None:
    """Mark an order paid. Only ever called after a verified signature.

    Tracked stock is reduced here, once the money is actually confirmed, so an
    abandoned checkout never eats inventory. Cash-on-delivery orders already
    hold their stock when placed, so the admin confirmation passes
    ``decrement_stock=False``.
    """
    order = get_order(order_id)
    if order is None:
        return
    already_paid = order["payment_status"] == "paid"

    execute(
        "UPDATE orders SET payment_status = 'paid', payment_ref = ?, payment_method = ?, "
        "status = CASE WHEN status = 'awaiting_payment' THEN 'confirmed' ELSE status END, "
        "updated_at = ? WHERE id = ?",
        (payment_ref, payment_method, utcnow(), order_id),
    )

    if already_paid:
        return

    add_order_event(order_id, "confirmed", f"Payment received ({payment_ref}).")

    if decrement_stock:
        decrement_stock_for_lines(
            [
                {
                    "product_id": item["product_id"],
                    "variant_id": item["variant_id"],
                    "quantity": item["quantity"],
                }
                for item in order["items"]
            ]
        )


def mark_payment_failed(order_id: int, reason: str = "") -> None:
    execute(
        "UPDATE orders SET payment_status = 'failed', updated_at = ? WHERE id = ?",
        (utcnow(), order_id),
    )
    add_order_event(order_id, "awaiting_payment", f"Payment failed: {reason}".strip())


def mark_payment_submitted(order_id: int, method: str = "upi") -> None:
    """The customer says they have paid.

    A UPI screenshot is not proof, so the order stays **unpaid**. It only
    moves to paid when the shop owner confirms the money in the admin area.
    """
    execute(
        "UPDATE orders SET payment_status = 'submitted', payment_method = ?, "
        "updated_at = ? WHERE id = ?",
        (method or "upi", utcnow(), order_id),
    )
    add_order_event(
        order_id,
        "awaiting_payment",
        "Customer says the payment was sent. Waiting for the shop to confirm it.",
    )


def restore_stock_for_order(order: Dict[str, Any]) -> None:
    """Put tracked stock back when an order is cancelled."""
    from catalog import get_product

    for item in order.get("items", []):
        product = get_product(item["product_id"]) if item["product_id"] else None
        if product is None or product.get("stock_mode") != "tracked":
            continue
        if item["variant_id"]:
            execute(
                "UPDATE product_variants SET stock = stock + ? WHERE id = ?",
                (int(item["quantity"]), item["variant_id"]),
            )
