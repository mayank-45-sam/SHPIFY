"""Add customize t-shirt feature to storefront."""
path = r"C:\Users\mayan\OneDrive\Desktop\SHPIFY\storefront.py"
with open(path, 'a', encoding='utf-8') as f:
    f.write('''


@bp.route("/customize", methods=["GET", "POST"])
def customize():
    """Customize a plain white t-shirt with front and back photos."""
    from flask import current_app, session
    from security import validate_csrf
    import os
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

        upload_dir = os.path.join(current_app.root_path, 'static', 'img', 'custom')
        os.makedirs(upload_dir, exist_ok=True)
        ext_front = front.filename.rsplit('.', 1)[1].lower()
        ext_back = back.filename.rsplit('.', 1)[1].lower()
        fname_front = f"custom_{uuid.uuid4().hex}_front.{ext_front}"
        fname_back = f"custom_{uuid.uuid4().hex}_back.{ext_back}"
        front.save(os.path.join(upload_dir, fname_front))
        back.save(os.path.join(upload_dir, fname_back))

        session['custom_shirt'] = {
            'front': fname_front,
            'back': fname_back,
            'price_cents': 59900
        }
        flash("Photos uploaded! Add to cart.", "success")
        return redirect(url_for("storefront.customize_cart"))

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

    token = cart_token()
    existing = query_all(
        "SELECT id, quantity FROM cart_items WHERE cart_token = ? AND variant_id = ?",
        (token(), variant_id),
    )
    if existing:
        execute("UPDATE cart_items SET quantity = ? WHERE id = ?", (min(int(existing[0]['quantity']) + 1, 10), existing[0]['id']))
    else:
        execute(
            "INSERT INTO cart_items (cart_token, product_id, variant_id, quantity, created_at) VALUES (?,?,?,?,?)",
            (token(), product['id'], variant_id, 1, utcnow()),
        )
    session.pop('custom_shirt', None)
    flash("Custom t-shirt added to cart.", "success")
    return redirect(url_for("storefront.cart_page"))
''')

print("done")
