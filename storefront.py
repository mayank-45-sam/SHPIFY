"""Public storefront routes."""

from __future__ import annotations

from typing import Any, Dict

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

import catalog
import media
import reviews
import wishlist
from cart import cart_lines, cart_payload, cart_token
from payments import method_ids, mode_label, missing_payment_settings, payments_configured
from pricing import build_totals
from supplier import supplier_status

bp = Blueprint("storefront", __name__)


@bp.get("/media/<path:filename>")
def stored_image(filename: str):
    """Serve an upload kept in the database (used on diskless hosts)."""
    image = media.load_db_image(filename)
    if image is None:
        abort(404)
    data, content_type = image
    response = Response(data, mimetype=content_type)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@bp.get("/")
def home():
    """Homepage: hero, categories, featured products and bestsellers."""
    featured = catalog.featured_products(12)
    latest = catalog.list_products(sort="newest", limit=24)

    # The hero and the featured grid lead with tees; posters follow in the
    # "latest" strip so the homepage still shows artwork the store is proud of.
    featured_tees = [
        p for p in featured if any(v["size"] in catalog.SIZES for v in p["variants"])
    ][:4]
    if len(featured_tees) < 4:
        for product in featured:
            if product not in featured_tees:
                featured_tees.append(product)
            if len(featured_tees) == 4:
                break
    featured = featured_tees
    counts = catalog.category_counts()
    seen = {product["id"] for product in featured}
    bestsellers = [p for p in latest if p["id"] not in seen][:4]
    if len(bestsellers) < 4:
        for product in latest:
            if product["id"] not in seen and product not in bestsellers:
                bestsellers.append(product)
            if len(bestsellers) == 4:
                break

    return render_template(
        "index.html",
        featured=featured,
        bestsellers=bestsellers,
        counts=counts,
        categories=catalog.CATEGORIES,
        category_labels=catalog.CATEGORY_LABELS,
        category_taglines=catalog.CATEGORY_TAGLINES,
    )


@bp.get("/shop")
def shop():
    """Shop page with category filters, search and sorting."""
    category = (request.args.get("category") or "all").strip().lower()
    if category not in catalog.CATEGORIES and category != "all":
        category = "all"

    search = (request.args.get("q") or "").strip()
    sort = (request.args.get("sort") or "featured").strip()
    if sort not in catalog.SORT_OPTIONS:
        sort = "featured"

    products = catalog.list_products(
        category=None if category == "all" else category,
        search=search or None,
        sort=sort,
    )

    return render_template(
        "shop.html",
        products=products,
        active_category=category,
        search=search,
        sort=sort,
        counts=catalog.category_counts(),
        categories=catalog.CATEGORIES,
        category_labels=catalog.CATEGORY_LABELS,
        category_taglines=catalog.CATEGORY_TAGLINES,
        sort_options=catalog.SORT_OPTIONS,
        sizes=catalog.SIZES,
    )


@bp.get("/cart")
def cart_page():
    """Full cart page. The drawer is the quick version of the same data."""
    lines = cart_lines()
    totals = build_totals(lines)
    return render_template("cart.html", lines=lines, totals=totals)


@bp.get("/product/<slug>")
def product_detail(slug: str):
    """Product detail page."""
    product = catalog.get_product_by_slug(slug)
    if product is None:
        return render_template("404.html", code=404, message="Product not found."), 404

    availability = catalog.size_availability(product)
    sizes = [variant["size"] for variant in product["variants"]]
    return render_template(
        "product.html",
        product=product,
        # Named `availability_by_size` on purpose: `availability` is a template
        # global (a helper function), and shadowing it here would break every
        # product card included on this page.
        availability_by_size=availability,
        sizes=sizes,
        show_size_guide=any(size in catalog.SIZES for size in sizes),
        related=catalog.related_products(product, 4),
        category_labels=catalog.CATEGORY_LABELS,
        product_reviews=reviews.list_reviews(product["id"]),
        rating=reviews.rating_summary(product["id"]),
        wishlisted=wishlist.is_wishlisted(cart_token(), product["id"]),
    )


@bp.post("/product/<slug>/review")
def submit_review(slug: str):
    """Accept a review. It stays pending until approved in the admin area."""
    from flask import flash
    from security import validate_csrf

    validate_csrf()
    product = catalog.get_product_by_slug(slug)
    if product is None:
        return render_template("404.html", code=404, message="Product not found."), 404

    name = (request.form.get("name") or "").strip()
    title = (request.form.get("title") or "").strip()
    body = (request.form.get("body") or "").strip()
    try:
        rating = int(request.form.get("rating") or 5)
    except ValueError:
        rating = 5
    if len(name) < 2 or len(body) < 5:
        flash("Please add your name and a few words about the product.", "error")
    else:
        reviews.create_review(
            product["id"],
            name,
            rating,
            title,
            body,
            (request.form.get("order_number") or "").strip(),
            (request.form.get("phone") or "").strip(),
        )
        flash("Thanks! Your review will appear once it is approved.", "success")
    return redirect(url_for("storefront.product_detail", slug=slug) + "#reviews")


@bp.get("/wishlist")
def wishlist_page():
    return render_template(
        "wishlist.html",
        products=wishlist.list_wishlist(cart_token()),
        category_labels=catalog.CATEGORY_LABELS,
    )


@bp.post("/api/wishlist/toggle")
def wishlist_toggle():
    from security import validate_csrf

    validate_csrf()
    data = request.get_json(silent=True) or {}
    try:
        product_id = int(data.get("product_id"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Unknown product."}, 400
    product = catalog.get_product(product_id)
    if product is None or product["status"] != "published":
        return {"ok": False, "error": "Unknown product."}, 404
    added = wishlist.toggle(cart_token(), product_id)
    return {
        "ok": True,
        "added": added,
        "count": wishlist.count(cart_token()),
        "message": "Saved to wishlist." if added else "Removed from wishlist.",
    }


@bp.get("/size-guide")
def size_guide():
    return render_template("size_guide.html", sizes=catalog.SIZES)


@bp.get("/sitemap.xml")
def sitemap():
    from flask import Response

    products = catalog.list_products(limit=500)
    base = request.url_root.rstrip("/")
    urls = [
        (base + "/", "1.0"),
        (base + url_for("storefront.shop"), "0.9"),
        (base + url_for("storefront.cart_page"), "0.5"),
        (base + url_for("storefront.track_order"), "0.4"),
        (base + url_for("storefront.contact"), "0.4"),
        (base + url_for("storefront.size_guide"), "0.4"),
    ]
    for product in products:
        urls.append((base + url_for("storefront.product_detail", slug=product["slug"]), "0.8"))
    for category in catalog.CATEGORIES:
        urls.append((base + url_for("storefront.shop", category=category), "0.6"))
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, priority in urls:
        body.append(f"  <url><loc>{loc}</loc><priority>{priority}</priority></url>")
    body.append("</urlset>")
    return Response("\n".join(body), mimetype="application/xml")


@bp.get("/category/<category>")
def category_redirect(category: str):
    """Shortcut links such as /category/cricket."""
    if category not in catalog.CATEGORIES:
        return render_template("404.html", code=404, message="Category not found."), 404
    return redirect(url_for("storefront.shop", category=category))


@bp.get("/track-order")
def track_order():
    """Look up an order with its order number and phone number."""
    order = None
    error = ""
    searched = False

    if request.method == "GET" and request.args.get("order_number"):
        searched = True
        number = (request.args.get("order_number") or "").strip().upper()
        phone = (request.args.get("phone") or "").strip()
        if not number or not phone:
            error = "Enter both your order number and the phone number you used."
        else:
            from orders import get_order_by_number

            found = get_order_by_number(number)
            if found is None:
                error = "We could not find an order with that number."
            elif found["phone"].strip() != phone:
                error = "That order number and phone number do not match."
            else:
                order = found

    return render_template("track_order.html", order=order, error=error, searched=searched)


@bp.route("/contact", methods=["GET", "POST"])
def contact():
    """Contact page with the store details and a form."""
    if request.method == "POST":
        from security import validate_csrf

        validate_csrf()
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        message = (request.form.get("message") or "").strip()

        if len(name) < 2 or "@" not in email or len(message) < 5:
            flash("Please fill in your name, a valid email and a short message.", "error")
        else:
            # No mail server is configured, so the message is logged rather than
            # silently pretending an email was sent.
            app_logger = current_app.logger
            app_logger.info("Contact form: name=%s email=%s message=%s", name, email, message)
            flash(
                "Thanks, we have your message. We reply within one working day.",
                "success",
            )
            return redirect(url_for("storefront.contact"))

    return render_template("contact.html")


@bp.get("/api/health")
def health():
    """Simple health check used by the run script and tests."""
    return {
        "ok": True,
        "payment_mode": mode_label(),
        "payment_methods": method_ids(),
        "supplier_configured": supplier_status()["configured"],
    }


@bp.get("/site-context")
def site_context():
    """Small JSON blob the front-end uses for labels and mode banners."""
    config = current_app.config
    return {
        "store_name": config["STORE_NAME"],
        "currency": "₹",
        "payment_mode": mode_label(),
        "payment_methods": method_ids(),
        "payment_missing": missing_payment_settings(),
        "supplier": supplier_status(),
        "shipping_fee": config["SHIPPING_FEE"],
        "free_shipping_threshold": config["FREE_SHIPPING_THRESHOLD"],
    }



@bp.route("/customize", methods=["GET", "POST"])
def customize():
    """Customize a plain white t-shirt with front and back photos."""
    from flask import session
    from security import validate_csrf
    import uuid

    if request.method == "POST":
        validate_csrf()
        front = request.files.get("front_photo")
        back = request.files.get("back_photo")
        if not front or front.filename == '':
            flash("Please upload a photo for the front.", "error")
            return redirect(url_for("storefront.customize"))
        if not back or back.filename == '':
            flash("Please upload a photo for the back.", "error")
            return redirect(url_for("storefront.customize"))

        allowed = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        def allowed_file(f):
            if '.' not in f.filename:
                return False
            ext = f.filename.rsplit('.', 1)[1].lower()
            return ext in allowed

        if not allowed_file(front) or not allowed_file(back):
            flash("Please upload valid image files (PNG, JPG, JPEG, GIF, WEBP).", "error")
            return redirect(url_for("storefront.customize"))

        ext_front = front.filename.rsplit('.', 1)[1].lower()
        ext_back = back.filename.rsplit('.', 1)[1].lower()
        fname_front = f"custom_{uuid.uuid4().hex}_front.{ext_front}"
        fname_back = f"custom_{uuid.uuid4().hex}_back.{ext_back}"
        try:
            stored_front = media.store_custom_image(front, fname_front)
            stored_back = media.store_custom_image(back, fname_back)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("storefront.customize"))

        session['custom_shirt'] = {
            'front': stored_front,
            'back': stored_back,
            'price_cents': 59900
        }
        flash("Photos uploaded! Preview and adjust your design.", "success")
        return redirect(url_for("storefront.customize_preview"))

    return render_template("customize.html")


@bp.get("/customize/cart")
def customize_cart():
    from flask import session
    from catalog import format_rupees
    custom = session.get('custom_shirt')
    if not custom:
        flash("Please upload your photos first.", "error")
        return redirect(url_for("storefront.customize"))
    return render_template("customize_cart.html", custom=custom, price_display=format_rupees(custom['price_cents']))


@bp.post("/customize/add")
def customize_add():
    from security import validate_csrf
    from flask import session
    import catalog
    from db import execute, utcnow, query_all
    from cart import cart_token

    validate_csrf()
    custom = session.get('custom_shirt')
    if not custom:
        flash("Please upload your photos first.", "error")
        return redirect(url_for("storefront.customize"))

    product = catalog.get_product_by_slug('custom-plain-white-tshirt')
    if product is None:
        product_id = catalog.create_product(
            name='Customize Your T-Shirt - Plain White',
            description='Plain white t-shirt customized with your own photos on front and back.',
            category='cricket',
            price_cents=59900,
            compare_at_cents=0,
            status='published',
            stock_mode='made_to_order',
            sizes=('S', 'M', 'L', 'XL', '2XL'),
        )
        product = catalog.get_product(product_id)

    variant_id = product['variants'][0]['id'] if product['variants'] else None
    if variant_id is None:
        flash("Unable to add to cart.", "error")
        return redirect(url_for("storefront.customize"))

    custom_front = custom.get('front') or ''
    custom_back = custom.get('back') or ''

    token = cart_token()
    existing = query_all(
        "SELECT id, quantity FROM cart_items "
        "WHERE cart_token = ? AND variant_id = ? AND custom_front = ? AND custom_back = ?",
        (token, variant_id, custom_front, custom_back),
    )
    if existing:
        execute("UPDATE cart_items SET quantity = ? WHERE id = ?", (min(int(existing[0]['quantity']) + 1, 10), existing[0]['id']))
    else:
        execute(
            "INSERT INTO cart_items "
            "(cart_token, product_id, variant_id, quantity, custom_front, custom_back, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (token, product['id'], variant_id, 1, custom_front, custom_back, utcnow()),
        )
    session.pop('custom_shirt', None)
    flash("Custom t-shirt added to cart.", "success")
    return redirect(url_for("storefront.cart_page"))








@bp.get("/customize/preview")
def customize_preview():
    from flask import session
    from catalog import format_rupees
    custom = session.get('custom_shirt')
    if not custom:
        flash("Please upload your photos first.", "error")
        return redirect(url_for("storefront.customize"))
    return render_template("customize_preview.html", custom=custom, price_display=format_rupees(custom['price_cents']))
