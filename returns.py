"""Return / refund requests."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from db import execute, query_all, query_one, utcnow

RETURN_STATUSES = ("requested", "approved", "rejected", "received", "refunded")


def create_return(order_id: int, reason: str, details: str = "") -> int:
    now = utcnow()
    cursor = execute(
        "INSERT INTO returns (order_id, reason, details, status, created_at, updated_at) "
        "VALUES (?,?,?,'requested',?,?)",
        (order_id, reason.strip(), details.strip(), now, now),
    )
    return cursor.lastrowid


def return_for_order(order_id: int) -> Optional[Dict[str, Any]]:
    row = query_one(
        "SELECT * FROM returns WHERE order_id = ? ORDER BY id DESC LIMIT 1", (order_id,)
    )
    return dict(row) if row else None


def list_returns(status: str = "") -> List[Dict[str, Any]]:
    rows = query_all(
        "SELECT r.*, o.order_number, o.customer_name, o.phone, o.total_cents "
        "FROM returns r JOIN orders o ON o.id = r.order_id "
        + ("WHERE r.status = ? " if status else "")
        + "ORDER BY r.created_at DESC, r.id DESC",
        (status,) if status else (),
    )
    return [dict(row) for row in rows]


def set_status(return_id: int, status: str) -> None:
    if status not in RETURN_STATUSES:
        return
    execute("UPDATE returns SET status = ?, updated_at = ? WHERE id = ?", (status, utcnow(), return_id))
