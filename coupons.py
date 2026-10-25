"""Discount coupons."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from db import execute, query_all, query_one, utcnow

KINDS = ("percent", "flat")


def normalize_code(code: str) -> str:
    return (code or "").strip().upper()


def list_coupons() -> List[Dict[str, Any]]:
    rows = query_all("SELECT * FROM coupons ORDER BY created_at DESC, id DESC")
    return [dict(row) for row in rows]


def get_coupon(coupon_id: int) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM coupons WHERE id = ?", (coupon_id,))
    return dict(row) if row else None


def create_coupon(
    code: str,
    kind: str,
    value: int,
    min_order_cents: int = 0,
    max_uses: int = 0,
    expires_at: str = "",
    active: bool = True,
) -> int:
    code = normalize_code(code)
    if kind not in KINDS:
        kind = "percent"
    cursor = execute(
        """INSERT INTO coupons
           (code, kind, value, min_order_cents, max_uses, used_count, expires_at, active, created_at)
           VALUES (?,?,?,?,?,0,?,?,?)""",
        (code, kind, int(value), int(min_order_cents), int(max_uses),
         expires_at.strip(), 1 if active else 0, utcnow()),
    )
    return cursor.lastrowid


def update_coupon(
    coupon_id: int,
    code: str,
    kind: str,
    value: int,
    min_order_cents: int,
    max_uses: int,
    expires_at: str,
    active: bool,
) -> None:
    execute(
        """UPDATE coupons SET code=?, kind=?, value=?, min_order_cents=?, max_uses=?,
           expires_at=?, active=? WHERE id=?""",
        (normalize_code(code), kind if kind in KINDS else "percent", int(value),
         int(min_order_cents), int(max_uses), expires_at.strip(),
         1 if active else 0, coupon_id),
    )


def delete_coupon(coupon_id: int) -> None:
    execute("DELETE FROM coupons WHERE id = ?", (coupon_id,))


def _expired(coupon: Dict[str, Any]) -> bool:
    raw = (coupon.get("expires_at") or "").strip()
    if not raw:
        return False
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when < datetime.now(timezone.utc)


def discount_for(coupon: Dict[str, Any], subtotal_cents: int) -> int:
    if coupon["kind"] == "percent":
        amount = subtotal_cents * int(coupon["value"]) // 100
    else:
        amount = int(coupon["value"])
    return max(0, min(amount, subtotal_cents))


def validate_coupon(code: str, subtotal_cents: int) -> Tuple[bool, int, str, Optional[Dict[str, Any]]]:
    """Return (ok, discount_cents, message, coupon)."""
    code = normalize_code(code)
    if not code:
        return False, 0, "", None
    row = query_one("SELECT * FROM coupons WHERE code = ?", (code,))
    if row is None:
        return False, 0, "That coupon code does not exist.", None
    coupon = dict(row)
    if not coupon["active"]:
        return False, 0, "That coupon is no longer active.", None
    if _expired(coupon):
        return False, 0, "That coupon has expired.", None
    if int(coupon["max_uses"]) and int(coupon["used_count"]) >= int(coupon["max_uses"]):
        return False, 0, "That coupon has been used up.", None
    if subtotal_cents < int(coupon["min_order_cents"]):
        minimum = int(coupon["min_order_cents"]) / 100
        return False, 0, f"Spend at least Rs {minimum:.0f} to use this coupon.", None
    discount = discount_for(coupon, subtotal_cents)
    if discount <= 0:
        return False, 0, "That coupon does not apply to this order.", None
    return True, discount, "", coupon


def redeem_coupon(coupon_id: int, order_id: int) -> None:
    execute(
        "INSERT INTO coupon_redemptions (coupon_id, order_id, created_at) VALUES (?,?,?)",
        (coupon_id, order_id, utcnow()),
    )
    execute("UPDATE coupons SET used_count = used_count + 1 WHERE id = ?", (coupon_id,))
