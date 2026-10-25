"""Product catalog service.

Every product the storefront shows comes from the database. Templates and the
front-end never hardcode a product list, so new products appear automatically
as soon as they are added in the admin area.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional

from db import execute, execute_many, query_all, query_one, utcnow

#: Sizes the store sells, in display order.
SIZES: tuple = ("XS", "S", "M", "L", "XL", "2XL")

#: Extra size labels used by non-apparel products such as posters.
POSTER_SIZES: tuple = ("18x24 in", "24x36 in")

#: Every size label a product may use.
ALL_SIZES: tuple = SIZES + POSTER_SIZES

#: Product categories. "all" is a pseudo-category used by the shop filters.
CATEGORIES: tuple = ("cricket", "superheroes", "football")

CATEGORY_LABELS: Dict[str, str] = {
    "cricket": "Cricket",
    "superheroes": "Superheroes",
    "football": "Football",
}

CATEGORY_TAGLINES: Dict[str, str] = {
    "cricket": "Sixes, centuries and the last-over jitters.",
    "superheroes": "For the ones who save the city before breakfast.",
    "football": "Champions League mornings, all day every day.",
}

#: Valid values for product stock behaviour.
STOCK_MODES: tuple = ("made_to_order", "tracked")

SORT_OPTIONS: Dict[str, str] = {
    "featured": "Featured",
    "newest": "Newest",
    "price_asc": "Price: low to high",
    "price_desc": "Price: high to low",
    "name_asc": "Name: A to Z",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """Turn a product name into a URL-safe slug."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return re.sub(r"-{2,}", "-", slug)[:120] or "product"


def unique_slug(name: str, product_id: Optional[int] = None) -> str:
    """Return a slug that is not already used by another product."""
    base = slugify(name)
    candidate = base
    suffix = 2
    while True:
        row = query_one("SELECT id FROM products WHERE slug = ?", (candidate,))
        if row is None or (product_id is not None and row["id"] == product_id):
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


def format_rupees(cents: int) -> str:
    """Format paise as a rupee string, e.g. 59900 -> '599'."""
    amount = cents / 100
    if amount == int(amount):
        return f"{int(amount):,}"
    return f"{amount:,.2f}"


def rupees_to_cents(value: str) -> int:
    """Convert a rupee string to paise. Raises ValueError on bad input."""
    cleaned = str(value).strip().replace(",", "").replace("₹", "").replace("Rs", "").strip()
    if not cleaned:
        raise ValueError("Price is required.")
    amount = float(cleaned)
    if amount < 0:
        raise ValueError("Price cannot be negative.")
    return int(round(amount * 100))


# ---------------------------------------------------------------------------
# Reading the catalog
# ---------------------------------------------------------------------------


def _attach_children(products: List[Any]) -> List[Dict[str, Any]]:
    """Attach images and variants to a list of product rows."""
    if not products:
        return []
    ids = [row["id"] for row in products]
    placeholders = ",".join("?" for _ in ids)

    images: Dict[int, List[Dict[str, Any]]] = {}
    for image in query_all(
        f"SELECT id, product_id, filename, alt_text, position FROM product_images "
        f"WHERE product_id IN ({placeholders}) ORDER BY position, id",
        ids,
    ):
        images.setdefault(image["product_id"], []).append(dict(image))

    variants: Dict[int, List[Dict[str, Any]]] = {}
    for variant in query_all(
        f"SELECT id, product_id, size, stock, sku, price_cents, position FROM product_variants "
        f"WHERE product_id IN ({placeholders}) ORDER BY position, id",
        ids,
    ):
        variants.setdefault(variant["product_id"], []).append(dict(variant))

    result: List[Dict[str, Any]] = []
    for row in products:
        product = dict(row)
        product["images"] = images.get(row["id"], [])
        product["variants"] = variants.get(row["id"], [])
        product["price_display"] = format_rupees(product["price_cents"])
        product["compare_at_display"] = format_rupees(product["compare_at_cents"])
        for variant in product["variants"]:
            variant["price_cents"] = int(variant.get("price_cents") or 0) or int(
                product["price_cents"]
            )
            variant["price_display"] = format_rupees(variant["price_cents"])
        variant_prices = {v["price_cents"] for v in product["variants"]}
        product["has_size_prices"] = len(variant_prices) > 1
        product["price_from_display"] = (
            format_rupees(min(variant_prices)) if variant_prices else product["price_display"]
        )
        product["primary_image"] = product["images"][0]["filename"] if product["images"] else None
        product["in_stock_total"] = sum(v["stock"] for v in product["variants"])
        product["is_available"] = product["status"] == "published" and bool(product["variants"])
        result.append(product)
    return result


def list_products(
    category: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "featured",
    include_unpublished: bool = False,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return catalog products filtered, searched and sorted."""
    where: List[str] = []
    params: List[Any] = []

    if not include_unpublished:
        where.append("status = 'published'")
    if category and category in CATEGORIES:
        where.append("category = ?")
        params.append(category)
    if search:
        where.append("(name LIKE ? OR description LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])

    clause = f"WHERE {' AND '.join(where)}" if where else ""

    order = {
        "featured": "is_featured DESC, created_at DESC",
        "newest": "created_at DESC, id DESC",
        "price_asc": "price_cents ASC, name ASC",
        "price_desc": "price_cents DESC, name ASC",
        "name_asc": "name ASC",
    }.get(sort, "is_featured DESC, created_at DESC")

    sql = f"SELECT * FROM products {clause} ORDER BY {order}"
    if limit:
        sql += f" LIMIT {int(limit)}"

    return _attach_children(query_all(sql, params))


def get_product_by_slug(slug: str, include_unpublished: bool = False) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM products WHERE slug = ?", (slug,))
    if row is None:
        return None
    if row["status"] != "published" and not include_unpublished:
        return None
    return _attach_children([row])[0]


def get_product(product_id: int) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM products WHERE id = ?", (product_id,))
    if row is None:
        return None
    return _attach_children([row])[0]


def all_products(include_unpublished: bool = True) -> List[Dict[str, Any]]:
    return list_products(include_unpublished=include_unpublished)


def featured_products(limit: int = 4) -> List[Dict[str, Any]]:
    products = list_products(include_unpublished=False)
    flagged = [p for p in products if p["is_featured"]]
    chosen = flagged[:limit]
    if len(chosen) < limit:
        for product in products:
            if product not in chosen:
                chosen.append(product)
            if len(chosen) == limit:
                break
    return chosen


def category_counts(include_unpublished: bool = False) -> Dict[str, int]:
    where = "" if include_unpublished else "WHERE status = 'published'"
    rows = query_all(f"SELECT category, COUNT(*) AS n FROM products {where} GROUP BY category")
    counts = {key: 0 for key in CATEGORIES}
    for row in rows:
        counts[row["category"]] = row["n"]
    return counts


def size_availability(product: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return per-size availability, based on the product's stock mode.

    ``tracked``      -> a size is available only when its stock is above zero.
    ``made_to_order``-> every configured size is orderable, because the
                        supplier produces on demand. No stock numbers are
                        invented for this mode.

    The result follows the product's own size order, so posters can offer
    18x24 in / 24x36 in while tees keep XS to 2XL.
    """
    result: Dict[str, Dict[str, Any]] = {}
    for variant in product.get("variants", []):
        size = variant["size"]
        if product.get("stock_mode") == "tracked":
            available = variant["stock"] > 0
        else:
            available = True
        price_cents = int(variant.get("price_cents") or 0) or int(product["price_cents"])
        result[size] = {
            "size": size,
            "available": available,
            "stock": variant["stock"],
            "sku": variant["sku"],
            "price_cents": price_cents,
            "price_display": format_rupees(price_cents),
            "missing": False,
        }
    return result


def related_products(product: Dict[str, Any], limit: int = 4) -> List[Dict[str, Any]]:
    rows = query_all(
        "SELECT * FROM products WHERE category = ? AND status = 'published' AND id != ? "
        "ORDER BY is_featured DESC, created_at DESC LIMIT ?",
        (product["category"], product["id"], limit),
    )
    return _attach_children(rows)


# ---------------------------------------------------------------------------
# Writing the catalog (admin area)
# ---------------------------------------------------------------------------


def create_product(
    name: str,
    description: str = "",
    category: str = "cricket",
    price_cents: int = 0,
    compare_at_cents: int = 0,
    status: str = "draft",
    stock_mode: str = "made_to_order",
    sizes: Optional[Iterable[str]] = None,
    stocks: Optional[Dict[str, int]] = None,
    skus: Optional[Dict[str, str]] = None,
    prices: Optional[Dict[str, int]] = None,
    is_featured: bool = False,
    needs_review: bool = False,
    review_note: str = "",
    supplier_sku: str = "",
    meta_title: str = "",
    meta_description: str = "",
) -> int:
    """Insert a product and return its id."""
    now = utcnow()
    cursor = execute(
        """INSERT INTO products
           (slug, name, description, category, price_cents, compare_at_cents, status,
            stock_mode, is_featured, needs_review, review_note, supplier_sku,
            meta_title, meta_description, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            unique_slug(name),
            name,
            description,
            category if category in CATEGORIES else "cricket",
            price_cents,
            compare_at_cents,
            status if status in ("draft", "published") else "draft",
            stock_mode if stock_mode in STOCK_MODES else "made_to_order",
            1 if is_featured else 0,
            1 if needs_review else 0,
            review_note,
            supplier_sku,
            meta_title,
            meta_description,
            now,
            now,
        ),
    )
    product_id = cursor.lastrowid
    set_sizes(product_id, sizes or SIZES, stocks or {}, skus or {}, prices or {})
    return product_id


def update_product(product_id: int, **fields: Any) -> None:
    """Update the editable product fields."""
    allowed = {
        "name",
        "description",
        "category",
        "price_cents",
        "compare_at_cents",
        "status",
        "stock_mode",
        "is_featured",
        "needs_review",
        "review_note",
        "supplier_sku",
        "meta_title",
        "meta_description",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if "name" in updates:
        updates["slug"] = unique_slug(updates["name"], product_id)
    if not updates:
        return
    updates["updated_at"] = utcnow()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    execute(
        f"UPDATE products SET {assignments} WHERE id = ?",
        (*updates.values(), product_id),
    )


def set_sizes(
    product_id: int,
    sizes: Iterable[str],
    stocks: Dict[str, int],
    skus: Dict[str, str],
    prices: Optional[Dict[str, int]] = None,
) -> None:
    """Replace the size list of a product, keeping stock, SKU and price per size."""
    prices = prices or {}
    sizes = [s for s in sizes if s in ALL_SIZES]
    execute("DELETE FROM product_variants WHERE product_id = ?", (product_id,))
    rows = []
    for position, size in enumerate(sizes):
        rows.append(
            (
                product_id,
                size,
                int(stocks.get(size, 0) or 0),
                (skus.get(size) or "").strip(),
                int(prices.get(size, 0) or 0),
                position,
            )
        )
    if rows:
        execute_many(
            "INSERT INTO product_variants "
            "(product_id, size, stock, sku, price_cents, position) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )


def set_variant_stock(variant_id: int, stock: int) -> None:
    execute("UPDATE product_variants SET stock = ? WHERE id = ?", (int(stock), variant_id))


def add_image(product_id: int, filename: str, alt_text: str = "") -> int:
    row = query_one(
        "SELECT COALESCE(MAX(position), 0) + 1 AS next FROM product_images WHERE product_id = ?",
        (product_id,),
    )
    position = row["next"] if row else 1
    cursor = execute(
        "INSERT INTO product_images (product_id, filename, alt_text, position) VALUES (?,?,?,?)",
        (product_id, filename, alt_text or "", position),
    )
    return cursor.lastrowid


def delete_image(image_id: int) -> None:
    execute("DELETE FROM product_images WHERE id = ?", (image_id,))


def update_image(image_id: int, filename: str, alt_text: str) -> None:
    execute(
        "UPDATE product_images SET filename = ?, alt_text = ? WHERE id = ?",
        (filename, alt_text or "", image_id),
    )


def reorder_images(product_id: int, ordered_ids: List[int]) -> None:
    for position, image_id in enumerate(ordered_ids, start=1):
        execute(
            "UPDATE product_images SET position = ? WHERE id = ? AND product_id = ?",
            (position, image_id, product_id),
        )


def delete_product(product_id: int) -> None:
    execute("DELETE FROM products WHERE id = ?", (product_id,))


def duplicate_product(product_id: int) -> int:
    """Copy a product (without its images) so a new design can be started."""
    product = get_product(product_id)
    if product is None:
        raise ValueError("Product not found.")
    return create_product(
        name=f"{product['name']} (copy)",
        description=product["description"],
        category=product["category"],
        price_cents=product["price_cents"],
        compare_at_cents=product["compare_at_cents"],
        status="draft",
        stock_mode=product["stock_mode"],
        sizes=[v["size"] for v in product["variants"]],
        stocks={v["size"]: v["stock"] for v in product["variants"]},
        skus={v["size"]: v["sku"] for v in product["variants"]},
        prices={v["size"]: v["price_cents"] for v in product["variants"]},
    )


# ---------------------------------------------------------------------------
# Image library
# ---------------------------------------------------------------------------


def image_library() -> List[Dict[str, Any]]:
    """Every image file on disk, with the products that use it."""
    from config import UPLOAD_DIR

    used: Dict[str, List[str]] = {}
    for row in query_all(
        "SELECT p.name AS name, i.filename AS filename FROM product_images i "
        "JOIN products p ON p.id = i.product_id"
    ):
        used.setdefault(row["filename"], []).append(row["name"])

    files: List[Dict[str, Any]] = []
    if UPLOAD_DIR.exists():
        for path in sorted(UPLOAD_DIR.iterdir()):
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                files.append(
                    {
                        "filename": path.name,
                        "url": f"/static/img/products/{path.name}",
                        "used_by": used.get(path.name, []),
                        "size_kb": round(path.stat().st_size / 1024),
                    }
                )
    return files


def unused_images() -> List[Dict[str, Any]]:
    return [image for image in image_library() if not image["used_by"]]
