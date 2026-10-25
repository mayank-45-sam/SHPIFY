"""Storefront tests: public pages, product filtering, cart, checkout and tracking.

Standalone script, same style as the CSV test suites:

    python test_storefront.py

It runs against a throwaway database, so your real orders are never touched.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

import db
import test_support
from app import create_app
from seed import copy_images, seed_app

TEST_DB = Path(tempfile.gettempdir()) / "playbook_tees_test.db"

failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  {detail}" if not condition else ""))
    if not condition:
        failures.append(name)


def csrf(html: str) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    if not match:
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return match.group(1) if match else ""


def build_app():
    if TEST_DB.exists():
        TEST_DB.unlink()
    app = create_app()
    app.config["TESTING"] = True
    # The tests must not depend on what the shop owner has in .env, so every
    # payment method is switched off unless a test asks for it.
    set_payment_methods(app, [])
    test_support.prepare(app, str(TEST_DB))
    with app.app_context():
        db.init_db()
        copy_images()
        seed_app(app, verbose=False)
    return app


def set_payment_methods(app, methods):
    """Turn the payment methods on or off for a test app."""
    app.config["UPI_ID"] = "tester@upi" if "upi" in methods else ""
    app.config["UPI_PAYEE_NAME"] = "Test Payee" if "upi" in methods else ""
    app.config["UPI_QR_IMAGE"] = "img/payment/upi_qr.jpg"
    app.config["RAZORPAY_KEY_ID"] = "rzp_test_key" if "razorpay" in methods else ""
    app.config["RAZORPAY_KEY_SECRET"] = "secret_for_testing" if "razorpay" in methods else ""
    app.config["RAZORPAY_WEBHOOK_SECRET"] = "webhook_secret" if "razorpay" in methods else ""
    app.config["COD_ENABLED"] = "cod" in methods


def main() -> int:
    app = build_app()
    client = app.test_client()

    print("=== A. Public pages ===")
    for path in ("/", "/shop", "/shop?category=cricket", "/shop?q=cricket",
                 "/shop?sort=price_asc", "/product/cricket-fan-graphic-t-shirt",
                 "/cart", "/track-order", "/contact", "/api/health", "/site-context"):
        response = client.get(path)
        check(f"GET {path} -> 200", response.status_code == 200, str(response.status_code))

    check("GET /no-such-page -> 404", client.get("/no-such-page").status_code == 404)
    check("a draft product is hidden",
          client.get("/product/rohit-sharma-cricket-graphic-t-shirt").status_code == 404)

    home = client.get("/").get_data(as_text=True)
    check("homepage shows a product", "Cricket Fan Graphic T-Shirt" in home)
    check("homepage shows the real image", "Front_1_c_1.jpg" in home)

    check("an empty cart cannot check out",
          app.test_client().get("/checkout").status_code == 302)

    shop = client.get("/shop?category=cricket").get_data(as_text=True)
    check("category filter shows published cricket products",
          "Cricket Fan" in shop and "Virat Kohli" in shop)
    check("category filter hides drafts", "Rohit Sharma" not in shop)
    check("search works", "Cricket Fan" in client.get("/shop?q=Cricket+Fan").get_data(as_text=True))
    check("search with no match shows the empty state",
          "Nothing here yet" in client.get("/shop?q=zzzznotathing").get_data(as_text=True))

    print("\n=== A2. Every published product page renders ===")
    # A product page includes related product cards. If one of those cards
    # raises, the whole page 500s, so check every published product.
    import catalog

    with app.app_context():
        published = catalog.list_products(limit=50)
    check("there are published products to check", len(published) >= 2, str(len(published)))
    for product in published:
        response = client.get(f"/product/{product['slug']}")
        check(f"{product['name']} page -> 200", response.status_code == 200,
              str(response.status_code))
        body = response.get_data(as_text=True)
        check(f"{product['name']} shows its price",
              product["price_display"] in body, product["price_display"])

    print("\n=== B. Cart ===")
    token = csrf(home)

    response = client.post(
        "/api/cart/add",
        json={"product_id": 1, "size": "M", "quantity": 2},
        headers={"X-CSRF-Token": token},
    )
    payload = response.get_json()
    check("add to cart works", response.status_code == 200 and payload.get("ok") is True)
    check("item count is right", payload.get("item_count") == 2, str(payload.get("item_count")))
    check("subtotal uses the database price", payload["totals"]["subtotal_cents"] == 59900 * 2,
          str(payload["totals"]["subtotal_cents"]))
    check("shipping is free over the threshold", payload["totals"]["shipping_cents"] == 0,
          str(payload["totals"]["shipping_cents"]))

    solo = app.test_client()
    solo_token = csrf(solo.get("/").get_data(as_text=True))
    solo_payload = solo.post(
        "/api/cart/add",
        json={"product_id": 1, "size": "L", "quantity": 1},
        headers={"X-CSRF-Token": solo_token},
    ).get_json()
    check("shipping is charged below the threshold",
          solo_payload["totals"]["shipping_cents"] == 4900,
          str(solo_payload["totals"]["shipping_cents"]))
    check("the free shipping message is shown",
          "free shipping" in solo_payload["totals"]["shipping_message"].lower())

    cart_id = payload["lines"][0]["cart_id"]

    response = client.post(
        "/api/cart/update",
        json={"cart_id": cart_id, "quantity": 3},
        headers={"X-CSRF-Token": token},
    )
    check("quantity can be changed", response.get_json()["item_count"] == 3)

    response = client.post(
        "/api/cart/add",
        json={"product_id": 1, "size": "", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    check("a size is required", response.status_code == 400)

    response = client.post(
        "/api/cart/add",
        json={"product_id": 1, "size": "M"},
    )
    check("a request without a CSRF token is rejected", response.status_code == 400)

    response = client.post(
        "/api/cart/add",
        json={"product_id": 9999, "size": "M", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    check("an unknown product is rejected", response.status_code == 404)

    response = client.post(
        "/api/cart/update",
        json={"cart_id": cart_id, "quantity": 99},
        headers={"X-CSRF-Token": token},
    )
    check("the per line maximum is enforced", response.status_code == 400)

    check("the cart page shows the item",
          "Cricket Fan Graphic T-Shirt" in client.get("/cart").get_data(as_text=True))

    response = client.post(
        "/api/cart/sync",
        json={"items": [{"product_id": 1, "size": "L", "quantity": 1}]},
        headers={"X-CSRF-Token": token},
    )
    check("the local cart syncs back to the server", response.get_json()["item_count"] == 4)

    # localStorage is a mirror, so syncing it again must not add the piece
    # a second time (this used to double the quantity on every page load).
    response = client.post(
        "/api/cart/sync",
        json={"items": [{"product_id": 1, "size": "L", "quantity": 1}]},
        headers={"X-CSRF-Token": token},
    )
    check("syncing the same cart twice does not inflate it",
          response.get_json()["item_count"] == 4,
          str(response.get_json()["item_count"]))

    print("\n=== C. Checkout ===")
    checkout_page = client.get("/checkout").get_data(as_text=True)
    token = csrf(checkout_page)
    check("test mode is explained", "Test mode" in checkout_page)

    response = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "customer_name": "A",
            "phone": "123",
            "email": "nope",
            "address_line1": "x",
            "city": "",
            "state": "",
            "pincode": "12",
        },
    )
    body = response.get_data(as_text=True)
    check("bad details are rejected", response.status_code == 200)
    check("the phone error is shown", "10 digit mobile" in body)
    check("the email error is shown", "valid email" in body)
    check("the pincode error is shown", "6 digits" in body)

    token = csrf(body)
    response = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "customer_name": "Test Customer",
            "phone": "9876543210",
            "email": "test@example.com",
            "address_line1": "12 MG Road",
            "address_line2": "Near the park",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pincode": "560001",
            "notes": "Please pack carefully",
        },
    )
    location = response.headers.get("Location", "")
    check("a valid order is created", "/checkout/order/" in location, location)

    if "/checkout/order/" in location:
        order_number = location.rsplit("/", 1)[-1]
        body = client.get(location).get_data(as_text=True)
        check("the confirmation page shows the order", order_number in body)
        check("the confirmation page names the customer", "Test Customer" in body)
        check("the confirmation page says it is a test order", "test order" in body.lower())

        body = client.get(
            f"/track-order?order_number={order_number}&phone=9876543210"
        ).get_data(as_text=True)
        check("tracking finds the order", "Test Customer" in body)

        body = client.get(
            f"/track-order?order_number={order_number}&phone=9000000000"
        ).get_data(as_text=True)
        check("tracking refuses the wrong phone", "Test Customer" not in body)

    print("\n=== D. Contact ===")
    contact = client.get("/contact").get_data(as_text=True)
    token = csrf(contact)
    response = client.post(
        "/contact",
        data={
            "csrf_token": token,
            "name": "Test Customer",
            "email": "test@example.com",
            "phone": "9876543210",
            "message": "Do you ship to Kerala?",
        },
    )
    check("the contact form accepts a message", response.status_code == 302)

    print("\n=== E. UPI payments ===")
    upi_client = app.test_client()
    set_payment_methods(app, ["upi"])

    token = csrf(upi_client.get("/").get_data(as_text=True))
    upi_client.post(
        "/api/cart/add",
        json={"product_id": 1, "size": "M", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )

    checkout_page = upi_client.get("/checkout").get_data(as_text=True)
    token = csrf(checkout_page)
    check("the test mode notice is gone", "No payment method is set up" not in checkout_page)
    check("UPI is offered", 'value="upi"' in checkout_page)
    check("the customer can pick the method", "payment_method" in checkout_page)

    response = upi_client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "customer_name": "UPI Customer",
            "phone": "9876543210",
            "email": "upi@example.com",
            "address_line1": "12 MG Road",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pincode": "560001",
            "payment_method": "upi",
        },
    )
    location = response.headers.get("Location", "")
    check("a UPI order goes to the payment page", "/checkout/pay/" in location, location)

    if "/checkout/pay/" in location:
        order_number = location.rsplit("/", 1)[-1]
        pay_page = upi_client.get(location).get_data(as_text=True)
        check("the QR code is on the page", "/static/img/payment/upi_qr.jpg" in pay_page
              or "data:image/png;base64," in pay_page)
        check("the UPI id is on the page", "tester@upi" in pay_page)
        check("the exact amount is on the page", "&#8377;648" in pay_page,
              pay_page[pay_page.find("&#8377;"):pay_page.find("&#8377;") + 20])
        check("the QR carries the exact amount",
              "data:image/png;base64," in pay_page)
        check("the UPI app link carries the amount",
              f"am=648" in pay_page and f"tn={order_number}" in pay_page)

        token = csrf(pay_page)
        response = upi_client.post(
            "/checkout/upi/paid",
            data={"csrf_token": token, "order_number": order_number},
        )
        check("the customer can report the payment", response.status_code == 302)

        body = upi_client.get(f"/checkout/order/{order_number}").get_data(as_text=True)
        check("the order page says the payment is being checked",
              "checking your payment" in body.lower())
        check("the order is not marked paid", "Payment received" not in body)

        with app.app_context():
            import orders as orders_module

            row = orders_module.get_order_by_number(order_number)
        check("the payment status is submitted", row["payment_status"] == "submitted",
              row["payment_status"])
        check("the payment method is upi", row["payment_method"] == "upi")
        check("the order is not confirmed yet", row["status"] == "awaiting_payment", row["status"])

        # Reporting a payment is not proof: it must never mark an order paid.
        with app.app_context():
            import orders as orders_module

            row = orders_module.get_order_by_number(order_number)
        check("a self reported payment is never paid", row["payment_status"] != "paid")

        # Someone else's order cannot be touched.
        other = app.test_client()
        token = csrf(other.get("/").get_data(as_text=True))
        response = other.post(
            "/checkout/upi/paid",
            data={"csrf_token": token, "order_number": order_number, "phone": "9000000000"},
        )
        check("another customer cannot report this payment", response.status_code == 403,
              str(response.status_code))

    print("\n=== F. Cash on delivery ===")
    set_payment_methods(app, ["cod"])
    cod_client = app.test_client()
    token = csrf(cod_client.get("/").get_data(as_text=True))
    cod_client.post(
        "/api/cart/add",
        json={"product_id": 1, "size": "M", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )

    checkout_page = cod_client.get("/checkout").get_data(as_text=True)
    token = csrf(checkout_page)
    check("cash on delivery is offered", 'value="cod"' in checkout_page)

    response = cod_client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "customer_name": "COD Customer",
            "phone": "9876543210",
            "email": "cod@example.com",
            "address_line1": "12 MG Road",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pincode": "560001",
            "payment_method": "cod",
        },
    )
    location = response.headers.get("Location", "")
    check("a COD order is confirmed straight away", "/checkout/order/" in location, location)

    if "/checkout/order/" in location:
        order_number = location.rsplit("/", 1)[-1]
        body = cod_client.get(location).get_data(as_text=True)
        check("the confirmation explains cash on delivery",
              "Cash on delivery" in body)
        with app.app_context():
            import orders as orders_module

            row = orders_module.get_order_by_number(order_number)
        check("the COD payment is not marked paid", row["payment_status"] == "cod")
        check("the COD order is confirmed", row["status"] == "confirmed")
        check("the COD method is stored", row["payment_method"] == "cod")

    print("\n=== G. Admin is protected ===")
    check("the admin area needs a login", client.get("/admin").status_code == 302)
    check("the login page is public", client.get("/admin/login").status_code == 200)

    print()
    if failures:
        print(f"{len(failures)} FAILURES")
        for failure in failures:
            print(" -", failure)
        return 1
    print("All storefront tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
