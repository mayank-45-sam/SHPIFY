"""Admin area tests: sign in, product CRUD, image handling and order management.

Standalone script, same style as the other test suites:

    python test_admin.py

It runs against a throwaway database and removes the files it uploads.
"""

from __future__ import annotations

import io
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image
from werkzeug.security import generate_password_hash

import db
import test_support
from app import create_app
from config import UPLOAD_DIR
from seed import copy_images, seed_app

TEST_DB = Path(tempfile.gettempdir()) / "playbook_tees_admin_test.db"

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


def clean_uploads() -> None:
    for path in UPLOAD_DIR.glob("test-upload-*"):
        path.unlink(missing_ok=True)


def set_payment_methods(app, methods) -> None:
    """Turn the payment methods on or off for the test app."""
    app.config["UPI_ID"] = "tester@upi" if "upi" in methods else ""
    app.config["UPI_PAYEE_NAME"] = "Test Payee" if "upi" in methods else ""
    app.config["UPI_QR_IMAGE"] = "img/payment/upi_qr.jpg"
    app.config["RAZORPAY_KEY_ID"] = "rzp_test_key" if "razorpay" in methods else ""
    app.config["RAZORPAY_KEY_SECRET"] = "secret_for_testing" if "razorpay" in methods else ""
    app.config["RAZORPAY_WEBHOOK_SECRET"] = "webhook_secret" if "razorpay" in methods else ""
    app.config["COD_ENABLED"] = "cod" in methods


def main() -> int:
    if TEST_DB.exists():
        TEST_DB.unlink()
    clean_uploads()

    app = create_app()
    app.config["TESTING"] = True
    app.config["ADMIN_USERNAME"] = "admin"
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("testpass123")
    test_support.prepare(app, str(TEST_DB))
    # The tests must not depend on what the shop owner has in .env.
    set_payment_methods(app, [])

    with app.app_context():
        db.init_db()
        copy_images()
        seed_app(app, verbose=False)

    client = app.test_client()

    print("=== A. Sign in ===")
    check("the admin area needs a login", client.get("/admin").status_code == 302)

    page = client.get("/admin/login").get_data(as_text=True)
    token = csrf(page)

    response = client.post(
        "/admin/login",
        data={"csrf_token": token, "username": "admin", "password": "wrong"},
    )
    check("a wrong password is refused", "Wrong username or password" in response.get_data(as_text=True))

    page = client.get("/admin/login").get_data(as_text=True)
    token = csrf(page)
    response = client.post(
        "/admin/login",
        data={"csrf_token": token, "username": "admin", "password": "testpass123"},
    )
    check("signing in works", response.status_code == 302)

    print("\n=== B. Pages ===")
    dashboard = client.get("/admin")
    check("the dashboard loads", dashboard.status_code == 200)
    body = dashboard.get_data(as_text=True)
    check("the dashboard warns about test mode payments", "test mode" in body.lower())
    check("the dashboard warns about the supplier", "not connected" in body.lower())

    for path in ("/admin/products", "/admin/products?category=cricket", "/admin/orders",
                 "/admin/orders?q=PT", "/admin/settings", "/admin/products/new"):
        check(f"GET {path} -> 200", client.get(path).status_code == 200)

    print("\n=== C. Product CRUD ===")
    page = client.get("/admin/products/new").get_data(as_text=True)
    token = csrf(page)
    data = {
        "csrf_token": token,
        "name": "Test Product From Admin",
        "description": "Created by the admin test suite.",
        "category": "superheroes",
        "price": "799",
        "compare_at": "999",
        "status": "draft",
        "stock_mode": "tracked",
        "is_featured": "on",
        "review_note": "check the artwork",
        "supplier_sku": "SUP-123",
        "meta_title": "Test Product",
        "meta_description": "A test product.",
    }
    for size in ("XS", "S", "M", "L", "XL", "2XL"):
        data[f"size_{size}"] = "on"
        data[f"stock_{size}"] = "5"
        data[f"sku_{size}"] = f"TST-{size}"

    response = client.post("/admin/products/new", data=data)
    location = response.headers.get("Location", "")
    check("the product is created", "/admin/products/" in location, location)

    page = client.get(location).get_data(as_text=True)
    check("the edit page shows the product", "Test Product From Admin" in page)
    check("the edit page shows the supplier SKU", "SUP-123" in page)

    response = client.post(
        "/admin/products/new",
        data={"csrf_token": token, "name": "x", "price": "abc"},
    )
    body = response.get_data(as_text=True)
    check("a bad price is rejected", "Price must be a number" in body)
    check("a short name is rejected", "at least 3 characters" in body)
    check("no sizes is rejected", "Select at least one size" in body)

    print("\n=== D. Images ===")
    buffer = io.BytesIO()
    Image.new("RGB", (800, 1000), (200, 150, 90)).save(buffer, "JPEG")
    buffer.seek(0)

    token = csrf(page)
    response = client.post(
        f"{location}/images",
        data={"csrf_token": token, "images": (buffer, "test-upload.jpg")},
        content_type="multipart/form-data",
    )
    check("an image can be uploaded", response.status_code == 302)
    check("the uploaded file is on disk", bool(list(UPLOAD_DIR.glob("test-upload-*.jpg"))))

    page = client.get(location).get_data(as_text=True)
    token = csrf(page)
    response = client.post(
        f"{location}/images/link",
        data={"csrf_token": token, "filename": "Back_2_c_1.jpg", "alt_text": "Back print"},
    )
    check("a library image can be attached", response.status_code == 302)

    page = client.get(location).get_data(as_text=True)
    check("the attached image is on the edit page", "Back print" in page)

    print("\n=== E. Publishing ===")
    token = csrf(page)
    data["csrf_token"] = token
    data["status"] = "published"
    client.post(location, data=data)

    shop = client.get("/shop?q=Test+Product").get_data(as_text=True)
    check("a published product appears in the shop", "Test Product From Admin" in shop)

    data["status"] = "draft"
    data["csrf_token"] = csrf(client.get(location).get_data(as_text=True))
    client.post(location, data=data)
    shop = client.get("/shop?q=Test+Product").get_data(as_text=True)
    check("a draft product is hidden from the shop", "Test Product From Admin" not in shop)

    print("\n=== F. Orders ===")
    # Place a real order through the storefront so there is something to manage.
    token = csrf(client.get("/").get_data(as_text=True))
    client.post(
        "/api/cart/add",
        json={"product_id": 1, "size": "M", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    token = csrf(client.get("/checkout").get_data(as_text=True))
    response = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "customer_name": "Order Tester",
            "phone": "9876543210",
            "email": "orders@example.com",
            "address_line1": "12 MG Road",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pincode": "560001",
        },
    )
    check("a test order is placed", "/checkout/order/" in response.headers.get("Location", ""))

    orders = client.get("/admin/orders").get_data(as_text=True)
    match = re.search(r"/admin/orders/(\d+)", orders)
    check("the orders list renders", match is not None)

    if match:
        order_url = f"/admin/orders/{match.group(1)}"
        page = client.get(order_url).get_data(as_text=True)
        token = csrf(page)

        response = client.post(
            f"{order_url}/status",
            data={"csrf_token": token, "status": "packed", "message": "Packed by hand"},
        )
        check("the status can be changed", response.status_code == 302)

        response = client.post(
            f"{order_url}/tracking",
            data={"csrf_token": token, "tracking_number": "TRK123", "carrier": "Delhivery"},
        )
        check("tracking can be saved", response.status_code == 302)

        response = client.post(f"{order_url}/supplier", data={"csrf_token": token})
        body = client.get(order_url).get_data(as_text=True)
        check("sending to an unconfigured supplier fails loudly",
              "not connected" in body.lower() or "supplier is not connected" in body.lower())

        body = client.get(order_url).get_data(as_text=True)
        check("the new status is shown", "Packed" in body)
        check("the tracking number is shown", "TRK123" in body)

    print("\n=== G. Confirming a UPI payment ===")
    set_payment_methods(app, ["upi"])

    token = csrf(client.get("/").get_data(as_text=True))
    client.post(
        "/api/cart/add",
        json={"product_id": 1, "size": "M", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    token = csrf(client.get("/checkout").get_data(as_text=True))
    response = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "customer_name": "UPI Tester",
            "phone": "9876543210",
            "email": "upi@example.com",
            "address_line1": "12 MG Road",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pincode": "560001",
            "payment_method": "upi",
        },
    )
    check("a UPI order is placed", "/checkout/pay/" in response.headers.get("Location", ""))

    orders_page = client.get("/admin/orders").get_data(as_text=True)
    match = re.search(r"/admin/orders/(\d+)", orders_page)
    check("the UPI order is listed in the admin", match is not None)

    if match:
        order_url = f"/admin/orders/{match.group(1)}"
        page = client.get(order_url).get_data(as_text=True)
        token = csrf(page)
        check("the admin page offers a payment action", "Payment received" in page)
        check("the admin page asks for a reference", "payment_ref" in page)

        response = client.post(
            f"{order_url}/payment",
            data={"csrf_token": token, "action": "received", "payment_ref": "UTR12345"},
        )
        check("the payment can be confirmed", response.status_code == 302)

        with app.app_context():
            import orders as orders_module

            rows, _summary = orders_module.list_orders()
        upi_row = next(r for r in rows if r["customer_name"] == "UPI Tester")
        check("the order is marked paid", upi_row["payment_status"] == "paid",
              upi_row["payment_status"])
        check("the order is confirmed", upi_row["status"] == "confirmed", upi_row["status"])
        check("the reference is stored", upi_row["payment_ref"] == "UTR12345",
              upi_row["payment_ref"])

    # A payment that never arrived must be marked as not paid.
    token = csrf(client.get("/").get_data(as_text=True))
    client.post(
        "/api/cart/add",
        json={"product_id": 1, "size": "L", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    token = csrf(client.get("/checkout").get_data(as_text=True))
    client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "customer_name": "No Pay Tester",
            "phone": "9876543211",
            "email": "nopay@example.com",
            "address_line1": "12 MG Road",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pincode": "560001",
            "payment_method": "upi",
        },
    )
    orders_page = client.get("/admin/orders").get_data(as_text=True)
    match = re.search(r"/admin/orders/(\d+)", orders_page)
    if match:
        order_url = f"/admin/orders/{match.group(1)}"
        token = csrf(client.get(order_url).get_data(as_text=True))
        client.post(
            f"{order_url}/payment",
            data={"csrf_token": token, "action": "not_received"},
        )
        with app.app_context():
            import orders as orders_module

            rows, _summary = orders_module.list_orders()
        missing_row = next(r for r in rows if r["customer_name"] == "No Pay Tester")
        check("an unpaid order is not marked paid", missing_row["payment_status"] != "paid",
              missing_row["payment_status"])
        check("the order stays unpaid", missing_row["payment_status"] == "failed",
              missing_row["payment_status"])

    print("\n=== H. Clean up ===")
    token = csrf(client.get(location).get_data(as_text=True))
    response = client.post(f"{location}/delete", data={"csrf_token": token})
    check("the product can be deleted", response.status_code == 302)
    check("the deleted product is gone", client.get(location).status_code == 404)

    clean_uploads()

    check("signing out works", client.get("/admin/logout").status_code == 302)
    check("the admin area is locked again", client.get("/admin").status_code == 302)

    print()
    if failures:
        print(f"{len(failures)} FAILURES")
        for failure in failures:
            print(" -", failure)
        return 1
    print("All admin tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
