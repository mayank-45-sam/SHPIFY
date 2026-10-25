"""Wishlist stored against the anonymous cart token."""

from __future__ import annotations

from typing import Dict, List

import catalog
from db import execute, query_all, query_one, utcnow


def list_wishlist(cart_token: str) -> List[Dict]:
    rows = query_all(
        "SELECT product_id FROM wishlist WHERE cart_token = ? ORDER BY id DESC",
        (cart_token,),
    )
    products = []
    for row in rows:
        product = catalog.get_product(int(row["product_id"]))
        if product and product["status"] == "published":
            products.append(product)
    return products


def is_wishlisted(cart_token: str, product_id: int) -> bool:
    return query_one(
        "SELECT id FROM wishlist WHERE cart_token = ? AND product_id = ?",
        (cart_token, product_id),
    ) is not None


def add(cart_token: str, product_id: int) -> None:
    if is_wishlisted(cart_token, product_id):
        return
    execute(
        "INSERT INTO wishlist (cart_token, product_id, created_at) VALUES (?,?,?)",
        (cart_token, product_id, utcnow()),
    )


def remove(cart_token: str, product_id: int) -> None:
    execute(
        "DELETE FROM wishlist WHERE cart_token = ? AND product_id = ?",
        (cart_token, product_id),
    )


def toggle(cart_token: str, product_id: int) -> bool:
    if is_wishlisted(cart_token, product_id):
        remove(cart_token, product_id)
        return False
    add(cart_token, product_id)
    return True


def count(cart_token: str) -> int:
    row = query_one(
        "SELECT COUNT(*) AS n FROM wishlist WHERE cart_token = ?", (cart_token,)
    )
    return int(row["n"]) if row else 0
