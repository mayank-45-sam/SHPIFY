"""Admin area: authentication, product management, images and orders.

Everything here is protected by the admin session and every write is
validated on the server. Uploads are re-encoded with Pillow so the
storefront stays fast on mobile.
"""

from __future__ import annotations

import csv
import io
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

import catalog
import coupons
import media
import notifications
import payments
import returns
import reviews
import supplier
from db import query_all, query_one, utcnow
from orders import (
    ORDER_STATUSES,
    STATUS_LABELS,
    get_order,
    list_orders,
    mark_paid,
    mark_payment_failed,
    restore_stock_for_order,
    update_order_status,
    update_tracking,
)
from security import csrf_token, login_admin, login_required, validate_csrf

bp = Blueprint("admin", __name__, url_prefix="/admin")

MAX_IMAGE_EDGE = 1200
JPEG_QUALITY = 86


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin_id"):
        return redirect(url_for("admin.dashboard"))

    error = ""
    if request.method == "POST":
        validate_csrf()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        if not current_app.config.get("ADMIN_PASSWORD_HASH"):
            error = (
                "No admin password is configured. Copy .env.example to .env, set "
                "ADMIN_PASSWORD_HASH, then restart the server."
            )
        elif login_admin(username, password):
            session["admin_id"] = username
            session.permanent = True
            flash("Signed in.", "success")
            next_url = request.args.get("next") or request.form.get("next")
            return redirect(next_url if next_url and next_url.startswith("/admin") else url_for("admin.dashboard"))
        else:
            error = "Wrong username or password."

    return render_template("admin/login.html", error=error)


@bp.get("/logout")
@login_required
def logout():
    session.pop("admin_id", None)
    flash("Signed out.", "info")
    return redirect(url_for("admin.login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def abandoned_carts() -> List[Dict[str, Any]]:
    hours = int(current_app.config.get("ABANDONED_CART_HOURS") or 2)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = query_all(
        """SELECT ci.cart_token, MIN(ci.created_at) AS since,
                  COUNT(*) AS lines, SUM(ci.quantity) AS items
           FROM cart_items ci
           WHERE ci.created_at <= ?
             AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.cart_token = ci.cart_token)
           GROUP BY ci.cart_token
           ORDER BY since DESC""",
        (cutoff,),
    )
    carts = []
    for row in rows:
        cart = dict(row)
        names = query_all(
            """SELECT p.name FROM cart_items ci JOIN products p ON p.id = ci.product_id
               WHERE ci.cart_token = ? ORDER BY ci.id LIMIT 3""",
            (cart["cart_token"],),
        )
        cart["product_names"] = [n["name"] for n in names]
        carts.append(cart)
    return carts


@bp.get("")
@login_required
def dashboard():
    products = catalog.all_products(include_unpublished=True)
    needs_review = [p for p in products if p["needs_review"]]
    no_image = [p for p in products if not p["images"]]
    drafts = [p for p in products if p["status"] != "published"]

    order_rows, order_summary = list_orders()

    revenue = sum(
        o["total_cents"] for o in order_rows if o["payment_status"] in ("paid", "test_mode")
    )
    # Money that is on its way: a UPI order the customer says they paid, plus
    # the ones nobody has paid yet.
    unpaid = sum(
        o["total_cents"] for o in order_rows if o["payment_status"] in ("pending", "submitted")
    )

    today = utcnow()[:10]
    today_orders = [o for o in order_rows if (o.get("created_at") or "").startswith(today)]
    today_revenue = sum(
        o["total_cents"] for o in today_orders if o["payment_status"] in ("paid", "cod")
    )
    cod_count = sum(1 for o in order_rows if o["payment_status"] == "cod")
    prepaid_count = sum(
        1 for o in order_rows if o["payment_status"] in ("paid", "submitted")
    )
    open_returns = [r for r in returns.list_returns() if r["status"] in ("requested", "approved")]
    low_stock = []
    for product in products:
        for variant in product.get("variants", []):
            if product["stock_mode"] == "tracked" and 0 < int(variant["stock"]) <= 3:
                low_stock.append(
                    {"name": product["name"], "size": variant["size"], "stock": variant["stock"]}
                )
                break

    return render_template(
        "admin/dashboard.html",
        products=products,
        needs_review=needs_review,
        no_image=no_image,
        drafts=drafts,
        orders=order_rows[:8],
        order_summary=order_summary,
        total_products=len(products),
        published_products=len(products) - len(drafts),
        revenue_cents=revenue,
        unpaid_cents=unpaid,
        payment_status=payments.mode_label(),
        missing_payment=payments.missing_payment_settings(),
        supplier_status=supplier.supplier_status(),
        images_pending=catalog.unused_images(),
        today_orders=len(today_orders),
        today_revenue_cents=today_revenue,
        cod_count=cod_count,
        prepaid_count=prepaid_count,
        pending_reviews=reviews.count_pending(),
        open_returns=len(open_returns),
        low_stock=low_stock,
        abandoned_count=len(abandoned_carts()),
    )


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


@bp.get("/products")
@login_required
def products_list():
    products = catalog.all_products(include_unpublished=True)
    category = (request.args.get("category") or "all").strip()
    if category not in catalog.CATEGORIES and category != "all":
        category = "all"
    if category != "all":
        products = [p for p in products if p["category"] == category]

    return render_template(
        "admin/products.html",
        products=products,
        active_category=category,
        categories=catalog.CATEGORIES,
        category_labels=catalog.CATEGORY_LABELS,
        sizes=catalog.SIZES,
    )


def _parse_product_form(form, size_choices=None) -> Dict[str, Any]:
    """Read and validate the product form."""
    errors: List[str] = []
    size_choices = list(size_choices or catalog.SIZES)

    name = (form.get("name") or "").strip()
    if len(name) < 3:
        errors.append("Product name must be at least 3 characters.")

    description = (form.get("description") or "").strip()

    category = (form.get("category") or "cricket").strip()
    if category not in catalog.CATEGORIES:
        errors.append("Choose a valid category.")
        category = "cricket"

    try:
        price_cents = catalog.rupees_to_cents(form.get("price") or "0")
        if price_cents <= 0:
            errors.append("Price must be greater than 0.")
    except ValueError:
        price_cents = 0
        errors.append("Price must be a number.")

    compare_raw = (form.get("compare_at") or "").strip()
    try:
        compare_cents = catalog.rupees_to_cents(compare_raw) if compare_raw else 0
    except ValueError:
        compare_cents = 0
        errors.append("Compare-at price must be a number.")

    status = (form.get("status") or "draft").strip()
    if status not in ("draft", "published"):
        status = "draft"

    stock_mode = (form.get("stock_mode") or "made_to_order").strip()
    if stock_mode not in catalog.STOCK_MODES:
        stock_mode = "made_to_order"

    sizes: List[str] = []
    stocks: Dict[str, int] = {}
    skus: Dict[str, str] = {}
    prices: Dict[str, int] = {}
    for size in size_choices:
        if form.get(f"size_{size}") != "on":
            continue
        sizes.append(size)
        raw_stock = (form.get(f"stock_{size}") or "0").strip()
        try:
            stock = int(raw_stock) if raw_stock else 0
        except ValueError:
            stock = 0
            errors.append(f"Stock for {size} must be a whole number.")
        if stock < 0:
            stock = 0
            errors.append(f"Stock for {size} cannot be negative.")
        stocks[size] = stock
        skus[size] = (form.get(f"sku_{size}") or "").strip()
        raw_price = (form.get(f"price_{size}") or "").strip()
        try:
            prices[size] = catalog.rupees_to_cents(raw_price) if raw_price else 0
        except ValueError:
            prices[size] = 0
            errors.append(f"Price for {size} must be a number.")

    if not sizes:
        errors.append("Select at least one size.")

    return {
        "errors": errors,
        "values": {
            "name": name,
            "description": description,
            "category": category,
            "price_cents": price_cents,
            "compare_at_cents": compare_cents,
            "status": status,
            "stock_mode": stock_mode,
            "is_featured": bool(form.get("is_featured")),
            "needs_review": bool(form.get("needs_review")),
            "review_note": (form.get("review_note") or "").strip(),
            "supplier_sku": (form.get("supplier_sku") or "").strip(),
            "meta_title": (form.get("meta_title") or "").strip(),
            "meta_description": (form.get("meta_description") or "").strip(),
            "sizes": sizes,
            "stocks": stocks,
            "skus": skus,
            "prices": prices,
        },
    }


@bp.route("/products/new", methods=["GET", "POST"])
@login_required
def product_new():
    errors: List[str] = []
    values: Dict[str, Any] = {
        "name": "",
        "description": "",
        "category": "cricket",
        "price_cents": current_app.config["DEFAULT_TEE_PRICE"] * 100,
        "compare_at_cents": 0,
        "status": "draft",
        "stock_mode": "made_to_order",
        "is_featured": False,
        "needs_review": False,
        "review_note": "",
        "supplier_sku": "",
        "meta_title": "",
        "meta_description": "",
        "sizes": list(catalog.SIZES),
        "stocks": {size: 0 for size in catalog.SIZES},
        "skus": {size: "" for size in catalog.SIZES},
        "prices": {size: 0 for size in catalog.SIZES},
    }

    if request.method == "POST":
        validate_csrf()
        parsed = _parse_product_form(request.form)
        errors = parsed["errors"]
        values = parsed["values"]
        if not errors:
            product_id = catalog.create_product(
                name=values["name"],
                description=values["description"],
                category=values["category"],
                price_cents=values["price_cents"],
                compare_at_cents=values["compare_at_cents"],
                status=values["status"],
                stock_mode=values["stock_mode"],
                sizes=values["sizes"],
                stocks=values["stocks"],
                skus=values["skus"],
                prices=values["prices"],
                is_featured=values["is_featured"],
                needs_review=values["needs_review"],
                review_note=values["review_note"],
                supplier_sku=values["supplier_sku"],
                meta_title=values["meta_title"],
                meta_description=values["meta_description"],
            )
            flash("Product created.", "success")
            return redirect(url_for("admin.product_edit", product_id=product_id))

    return render_template(
        "admin/product_edit.html",
        product=None,
        values=values,
        errors=errors,
        sizes=catalog.SIZES,
        categories=catalog.CATEGORIES,
        category_labels=catalog.CATEGORY_LABELS,
        image_library=catalog.image_library(),
    )


@bp.route("/products/<int:product_id>", methods=["GET", "POST"])
@login_required
def product_edit(product_id: int):
    product = catalog.get_product(product_id)
    if product is None:
        abort(404)

    size_choices = list(catalog.SIZES)
    for variant in product["variants"]:
        if variant["size"] not in size_choices:
            size_choices.append(variant["size"])

    errors: List[str] = []
    values: Dict[str, Any] = {
        "name": product["name"],
        "description": product["description"],
        "category": product["category"],
        "price_cents": product["price_cents"],
        "compare_at_cents": product["compare_at_cents"],
        "status": product["status"],
        "stock_mode": product["stock_mode"],
        "is_featured": bool(product["is_featured"]),
        "needs_review": bool(product["needs_review"]),
        "review_note": product["review_note"],
        "supplier_sku": product["supplier_sku"],
        "meta_title": product["meta_title"],
        "meta_description": product["meta_description"],
        "sizes": [v["size"] for v in product["variants"]],
        "stocks": {v["size"]: v["stock"] for v in product["variants"]},
        "skus": {v["size"]: v["sku"] for v in product["variants"]},
        "prices": {v["size"]: v["price_cents"] for v in product["variants"]},
    }

    if request.method == "POST":
        validate_csrf()
        parsed = _parse_product_form(request.form, size_choices)
        errors = parsed["errors"]
        values = parsed["values"]
        if not errors:
            catalog.update_product(
                product_id,
                name=values["name"],
                description=values["description"],
                category=values["category"],
                price_cents=values["price_cents"],
                compare_at_cents=values["compare_at_cents"],
                status=values["status"],
                stock_mode=values["stock_mode"],
                is_featured=values["is_featured"],
                needs_review=values["needs_review"],
                review_note=values["review_note"],
                supplier_sku=values["supplier_sku"],
                meta_title=values["meta_title"],
                meta_description=values["meta_description"],
            )
            catalog.set_sizes(
                product_id,
                values["sizes"],
                values["stocks"],
                values["skus"],
                values["prices"],
            )
            flash("Product saved.", "success")
            return redirect(url_for("admin.product_edit", product_id=product_id))

    return render_template(
        "admin/product_edit.html",
        product=product,
        values=values,
        errors=errors,
        sizes=size_choices,
        categories=catalog.CATEGORIES,
        category_labels=catalog.CATEGORY_LABELS,
        image_library=catalog.image_library(),
    )


@bp.post("/products/<int:product_id>/delete")
@login_required
def product_delete(product_id: int):
    validate_csrf()
    product = catalog.get_product(product_id)
    if product is None:
        abort(404)
    catalog.delete_product(product_id)
    flash(f"Deleted '{product['name']}'.", "success")
    return redirect(url_for("admin.products_list"))


@bp.post("/products/<int:product_id>/duplicate")
@login_required
def product_duplicate(product_id: int):
    validate_csrf()
    try:
        new_id = catalog.duplicate_product(product_id)
    except ValueError:
        abort(404)
    flash("Product duplicated as a draft.", "success")
    return redirect(url_for("admin.product_edit", product_id=new_id))


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def _save_upload(file_storage) -> str:
    """Validate, resize and store an uploaded image. Returns its reference."""
    allowed = current_app.config["ALLOWED_IMAGE_EXTENSIONS"]
    original = secure_filename(file_storage.filename or "")
    extension = os.path.splitext(original)[1].lower()
    if extension not in allowed:
        raise ValueError(
            f"Unsupported file type. Allowed: {', '.join(sorted(allowed))}."
        )

    stem = os.path.splitext(original)[0][:60] or "image"
    filename = f"{stem}-{secrets.token_hex(4)}.jpg"
    buffer = io.BytesIO()

    try:
        with Image.open(file_storage.stream) as image:
            image.load()
            if image.mode in ("RGBA", "P", "LA"):
                background = Image.new("RGB", image.size, (255, 255, 255))
                converted = image.convert("RGBA")
                background.paste(converted, mask=converted.split()[-1])
                image = background
            else:
                image = image.convert("RGB")

            if max(image.size) > MAX_IMAGE_EDGE:
                image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.LANCZOS)

            image.save(
                buffer, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True
            )
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("That file could not be read as an image.") from exc

    return media.store_product_image(buffer.getvalue(), filename)


@bp.post("/products/<int:product_id>/images")
@login_required
def product_images(product_id: int):
    validate_csrf()
    product = catalog.get_product(product_id)
    if product is None:
        abort(404)

    files = request.files.getlist("images")
    if not files or all(not item.filename for item in files):
        flash("Choose at least one image file.", "error")
        return redirect(url_for("admin.product_edit", product_id=product_id))

    saved = 0
    for item in files:
        if not item.filename:
            continue
        try:
            filename = _save_upload(item)
        except ValueError as exc:
            flash(str(exc), "error")
            continue
        alt = (item.filename or "").rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
        catalog.add_image(product_id, filename, f"{product['name']} - {alt}")
        saved += 1

    if saved:
        flash(f"Added {saved} image(s).", "success")
    return redirect(url_for("admin.product_edit", product_id=product_id))


@bp.post("/products/<int:product_id>/images/link")
@login_required
def product_images_link(product_id: int):
    """Attach an image that is already in the image library."""
    validate_csrf()
    product = catalog.get_product(product_id)
    if product is None:
        abort(404)

    filename = (request.form.get("filename") or "").strip()
    if not filename:
        flash("Choose an image from the library.", "error")
        return redirect(url_for("admin.product_edit", product_id=product_id))

    upload_dir = current_app.config["UPLOAD_DIR"]
    if not (upload_dir / filename).exists():
        flash("That image file does not exist.", "error")
        return redirect(url_for("admin.product_edit", product_id=product_id))

    alt = (request.form.get("alt_text") or "").strip() or product["name"]
    catalog.add_image(product_id, filename, alt)
    flash("Image attached.", "success")
    return redirect(url_for("admin.product_edit", product_id=product_id))


@bp.post("/images/<int:image_id>/delete")
@login_required
def image_delete(image_id: int):
    validate_csrf()
    row = query_one("SELECT product_id FROM product_images WHERE id = ?", (image_id,))
    if row is None:
        abort(404)
    catalog.delete_image(image_id)
    flash("Image removed from the product.", "success")
    return redirect(url_for("admin.product_edit", product_id=row["product_id"]))


@bp.post("/products/<int:product_id>/images/alt")
@login_required
def image_alt(product_id: int):
    validate_csrf()
    image_id = request.form.get("image_id")
    filename = (request.form.get("filename") or "").strip()
    alt = (request.form.get("alt_text") or "").strip()
    if image_id and filename:
        catalog.update_image(int(image_id), filename, alt)
        flash("Alt text updated.", "success")
    return redirect(url_for("admin.product_edit", product_id=product_id))


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


@bp.get("/orders")
@login_required
def orders_list():
    status = (request.args.get("status") or "").strip()
    search = (request.args.get("q") or "").strip()
    orders, summary = list_orders(status=status, search=search)
    return render_template(
        "admin/orders.html",
        orders=orders,
        summary=summary,
        statuses=ORDER_STATUSES,
        status_labels=STATUS_LABELS,
        active_status=status,
        search=search,
    )


@bp.get("/orders/<int:order_id>")
@login_required
def order_detail(order_id: int):
    order = get_order(order_id)
    if order is None:
        abort(404)
    return render_template(
        "admin/order_detail.html",
        order=order,
        statuses=ORDER_STATUSES,
        status_labels=STATUS_LABELS,
        supplier_status=supplier.supplier_status(),
    )


@bp.post("/orders/<int:order_id>/status")
@login_required
def order_status(order_id: int):
    validate_csrf()
    order = get_order(order_id)
    if order is None:
        abort(404)

    status = (request.form.get("status") or "").strip()
    message = (request.form.get("message") or "").strip()

    if status == "cancelled" and order["status"] != "cancelled":
        restore_stock_for_order(order)

    update_order_status(order_id, status, message)
    refreshed = get_order(order_id)
    if refreshed is not None:
        notifications.notify_order_status(refreshed, status)
    flash(f"Order marked as {STATUS_LABELS.get(status, status)}.", "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@bp.post("/orders/<int:order_id>/tracking")
@login_required
def order_tracking(order_id: int):
    validate_csrf()
    order = get_order(order_id)
    if order is None:
        abort(404)

    tracking = (request.form.get("tracking_number") or "").strip()
    carrier = (request.form.get("carrier") or "").strip()
    if not tracking:
        flash("Enter a tracking number.", "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    update_tracking(order_id, tracking, carrier)
    flash("Tracking details saved.", "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@bp.post("/orders/<int:order_id>/payment")
@login_required
def order_payment(order_id: int):
    """Confirm (or reject) a payment that the store owner checked by hand."""
    validate_csrf()
    order = get_order(order_id)
    if order is None:
        abort(404)

    action = (request.form.get("action") or "").strip()
    reference = (request.form.get("payment_ref") or "").strip()

    if action == "received":
        mark_paid(
            order_id,
            reference or order["payment_method"] or "upi",
            order["payment_method"] or "upi",
            decrement_stock=order["payment_status"] != "cod",
        )
        flash("Payment marked as received. The order is confirmed.", "success")
    elif action == "not_received":
        mark_payment_failed(order_id, reference or "No money received.")
        flash("Order marked as not paid.", "error")
    else:
        flash("Nothing was changed.", "error")

    return redirect(url_for("admin.order_detail", order_id=order_id))


@bp.post("/orders/<int:order_id>/supplier")
@login_required
def order_send_to_supplier(order_id: int):
    """Push an order to the print-on-demand supplier."""
    validate_csrf()
    order = get_order(order_id)
    if order is None:
        abort(404)

    lines = []
    for item in order["items"]:
        product = catalog.get_product(item["product_id"]) if item["product_id"] else None
        lines.append(
            {
                "product_id": item["product_id"],
                "variant_id": item["variant_id"],
                "name": item["product_name"],
                "size": item["size"],
                "sku": item["sku"],
                "supplier_sku": (product or {}).get("supplier_sku", ""),
                "unit_price_cents": item["unit_price_cents"],
                "quantity": item["quantity"],
            }
        )

    try:
        response = supplier.submit_order(order, lines)
    except supplier.SupplierNotConfigured as exc:
        flash(
            f"Supplier is not connected: {exc} The order stays in your admin "
            "area so you can fulfil it by hand.",
            "error",
        )
        return redirect(url_for("admin.order_detail", order_id=order_id))
    except supplier.SupplierError as exc:
        flash(f"The supplier rejected the order: {exc}", "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    reference = ""
    if isinstance(response, dict):
        reference = str(
            response.get("order_id") or response.get("id") or response.get("reference") or ""
        )
    from db import execute, utcnow

    execute(
        "UPDATE orders SET supplier_ref = ?, updated_at = ? WHERE id = ?",
        (reference, utcnow(), order_id),
    )
    update_order_status(order_id, "processing", f"Sent to supplier ({reference}).")
    flash("Order sent to the supplier for printing.", "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@bp.post("/supplier/check")
@login_required
def supplier_check():
    validate_csrf()
    result = supplier.check_connection()
    if result["ok"]:
        flash(result["message"], "success")
    else:
        flash(result["message"], "error")
    return redirect(url_for("admin.dashboard"))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@bp.get("/settings")
@login_required
def settings():
    return render_template(
        "admin/settings.html",
        payment_status=payments.mode_label(),
        missing_payment=payments.missing_payment_settings(),
        upi=payments.upi_details(),
        razorpay_configured=payments.payments_configured(),
        supplier_status=supplier.supplier_status(),
        store_name=current_app.config["STORE_NAME"],
        default_price=current_app.config["DEFAULT_TEE_PRICE"],
        shipping_fee=current_app.config["SHIPPING_FEE"],
        free_shipping_threshold=current_app.config["FREE_SHIPPING_THRESHOLD"],
        cod_enabled=payments.cod_enabled(),
        cod_fee=current_app.config.get("COD_FEE"),
        otp_required=current_app.config.get("OTP_REQUIRED"),
        serviceable_pincodes=current_app.config.get("SERVICEABLE_PINCODES"),
        gstin=current_app.config.get("STORE_GSTIN"),
        email_configured=notifications.email_configured(),
    )


# ---------------------------------------------------------------------------
# Coupons
# ---------------------------------------------------------------------------


@bp.get("/coupons")
@login_required
def coupons_list():
    return render_template("admin/coupons.html", coupons=coupons.list_coupons())


def _coupon_form(form) -> Dict[str, Any]:
    kind = (form.get("kind") or "percent").strip()
    if kind not in coupons.KINDS:
        kind = "percent"
    raw_value = (form.get("value") or "0").strip()
    try:
        if kind == "flat":
            value = catalog.rupees_to_cents(raw_value)
        else:
            value = int(raw_value)
    except ValueError:
        value = 0
    try:
        min_order_cents = catalog.rupees_to_cents(form.get("min_order") or "0")
    except ValueError:
        min_order_cents = 0
    try:
        max_uses = int(form.get("max_uses") or "0")
    except ValueError:
        max_uses = 0
    return {
        "code": (form.get("code") or "").strip().upper(),
        "kind": kind,
        "value": max(0, value),
        "min_order_cents": max(0, min_order_cents),
        "max_uses": max(0, max_uses),
        "expires_at": (form.get("expires_at") or "").strip(),
        "active": bool(form.get("active")),
    }


@bp.post("/coupons/new")
@login_required
def coupon_new():
    validate_csrf()
    values = _coupon_form(request.form)
    if len(values["code"]) < 3:
        flash("Coupon code must be at least 3 characters.", "error")
    elif values["value"] <= 0:
        flash("Give the coupon a discount value.", "error")
    elif query_one("SELECT id FROM coupons WHERE code = ?", (values["code"],)):
        flash("That coupon code already exists.", "error")
    else:
        if values["expires_at"]:
            values["expires_at"] = values["expires_at"] + "T23:59:59+00:00"
        coupons.create_coupon(**values)
        flash("Coupon created.", "success")
    return redirect(url_for("admin.coupons_list"))


@bp.post("/coupons/<int:coupon_id>")
@login_required
def coupon_update(coupon_id: int):
    validate_csrf()
    if coupons.get_coupon(coupon_id) is None:
        abort(404)
    values = _coupon_form(request.form)
    if values["expires_at"] and "T" not in values["expires_at"]:
        values["expires_at"] = values["expires_at"] + "T23:59:59+00:00"
    coupons.update_coupon(
        coupon_id,
        code=values["code"],
        kind=values["kind"],
        value=values["value"],
        min_order_cents=values["min_order_cents"],
        max_uses=values["max_uses"],
        expires_at=values["expires_at"],
        active=values["active"],
    )
    flash("Coupon saved.", "success")
    return redirect(url_for("admin.coupons_list"))


@bp.post("/coupons/<int:coupon_id>/delete")
@login_required
def coupon_delete(coupon_id: int):
    validate_csrf()
    coupons.delete_coupon(coupon_id)
    flash("Coupon deleted.", "success")
    return redirect(url_for("admin.coupons_list"))


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------


@bp.get("/reviews")
@login_required
def reviews_list():
    status = (request.args.get("status") or "").strip()
    return render_template(
        "admin/reviews.html",
        reviews=reviews.all_reviews(status),
        active_status=status,
    )


@bp.post("/reviews/<int:review_id>/status")
@login_required
def review_status(review_id: int):
    validate_csrf()
    status = (request.form.get("status") or "").strip()
    reviews.set_status(review_id, status)
    flash("Review updated.", "success")
    return redirect(request.referrer or url_for("admin.reviews_list"))


@bp.post("/reviews/<int:review_id>/delete")
@login_required
def review_delete(review_id: int):
    validate_csrf()
    reviews.delete_review(review_id)
    flash("Review deleted.", "success")
    return redirect(request.referrer or url_for("admin.reviews_list"))


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------


@bp.get("/returns")
@login_required
def returns_list():
    status = (request.args.get("status") or "").strip()
    return render_template(
        "admin/returns.html",
        returns=returns.list_returns(status),
        statuses=returns.RETURN_STATUSES,
        active_status=status,
    )


@bp.post("/returns/<int:return_id>/status")
@login_required
def return_status(return_id: int):
    validate_csrf()
    status = (request.form.get("status") or "").strip()
    returns.set_status(return_id, status)
    flash("Return updated.", "success")
    return redirect(url_for("admin.returns_list"))


# ---------------------------------------------------------------------------
# Abandoned carts, exports and bulk actions
# ---------------------------------------------------------------------------


@bp.get("/abandoned")
@login_required
def abandoned_list():
    return render_template(
        "admin/abandoned.html",
        carts=abandoned_carts(),
        hours=current_app.config.get("ABANDONED_CART_HOURS"),
    )


@bp.get("/orders/export")
@login_required
def orders_export():
    orders, _ = list_orders(
        status=(request.args.get("status") or "").strip(),
        search=(request.args.get("q") or "").strip(),
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Order", "Date", "Customer", "Phone", "Email", "City", "Pincode",
         "Status", "Payment status", "Payment method", "Subtotal", "Shipping",
         "Discount", "COD fee", "Total", "Tracking"]
    )
    for order in orders:
        writer.writerow(
            [
                order["order_number"], order["created_at"], order["customer_name"],
                order["phone"], order["email"], order["city"], order["pincode"],
                order["status_label"], order["payment_label"], order["payment_method"],
                f"{order['subtotal_cents'] / 100:.2f}",
                f"{order['shipping_cents'] / 100:.2f}",
                f"{order.get('discount_cents', 0) / 100:.2f}",
                f"{order.get('cod_fee_cents', 0) / 100:.2f}",
                f"{order['total_cents'] / 100:.2f}",
                order["tracking_number"],
            ]
        )
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=orders.csv"},
    )


@bp.post("/orders/bulk")
@login_required
def orders_bulk():
    validate_csrf()
    status = (request.form.get("status") or "").strip()
    ids = request.form.getlist("order_ids")
    if status not in ORDER_STATUSES:
        flash("Choose a valid status.", "error")
        return redirect(url_for("admin.orders_list"))
    changed = 0
    for raw_id in ids:
        try:
            order_id = int(raw_id)
        except ValueError:
            continue
        order = get_order(order_id)
        if order is None:
            continue
        if status == "cancelled" and order["status"] != "cancelled":
            restore_stock_for_order(order)
        update_order_status(order_id, status)
        changed += 1
    flash(f"Updated {changed} order(s).", "success")
    return redirect(url_for("admin.orders_list"))
