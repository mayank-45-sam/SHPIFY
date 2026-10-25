"""Customer product reviews."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask import current_app

from db import execute, query_all, query_one, utcnow


def list_reviews(product_id: int, status: str = "approved") -> List[Dict[str, Any]]:
    rows = query_all(
        "SELECT * FROM reviews WHERE product_id = ? AND status = ? ORDER BY created_at DESC, id DESC",
        (product_id, status),
    )
    return [dict(row) for row in rows]


def rating_summary(product_id: int) -> Dict[str, Any]:
    row = query_one(
        "SELECT COUNT(*) AS n, COALESCE(AVG(rating), 0) AS avg "
        "FROM reviews WHERE product_id = ? AND status = 'approved'",
        (product_id,),
    )
    count = int(row["n"]) if row else 0
    average = float(row["avg"]) if row else 0.0
    return {"count": count, "average": round(average, 1)}


def create_review(
    product_id: int,
    name: str,
    rating: int,
    title: str = "",
    body: str = "",
    order_number: str = "",
    phone: str = "",
) -> int:
    rating = max(1, min(5, int(rating or 5)))
    order_id = None
    if order_number and phone:
        from orders import get_order_by_number

        order = get_order_by_number(order_number.strip())
        if order and order["phone"].strip() == phone.strip():
            if any(int(item["product_id"] or 0) == int(product_id) for item in order["items"]):
                order_id = order["id"]

    status = "approved" if current_app.config.get("REVIEWS_AUTO_APPROVE") else "pending"
    cursor = execute(
        """INSERT INTO reviews (product_id, order_id, name, rating, title, body, status, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (product_id, order_id, name.strip(), rating, title.strip(), body.strip(), status, utcnow()),
    )
    return cursor.lastrowid


def all_reviews(status: str = "") -> List[Dict[str, Any]]:
    rows = query_all(
        "SELECT r.*, p.name AS product_name, p.slug AS product_slug "
        "FROM reviews r LEFT JOIN products p ON p.id = r.product_id "
        + ("WHERE r.status = ? " if status else "")
        + "ORDER BY r.created_at DESC, r.id DESC",
        (status,) if status else (),
    )
    return [dict(row) for row in rows]


def count_pending() -> int:
    row = query_one("SELECT COUNT(*) AS n FROM reviews WHERE status = 'pending'")
    return int(row["n"]) if row else 0


def set_status(review_id: int, status: str) -> None:
    if status not in ("pending", "approved", "rejected"):
        return
    execute("UPDATE reviews SET status = ? WHERE id = ?", (status, review_id))


def delete_review(review_id: int) -> None:
    execute("DELETE FROM reviews WHERE id = ?", (review_id,))
