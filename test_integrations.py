"""Integration tests for the two external edges: payments and the print supplier.

Standalone script:

    python test_integrations.py

Nothing here touches the network. It checks that the signatures are verified
correctly and that the supplier payload has the shape the API expects. The
live payment flow is exercised with the Razorpay HTTP calls stubbed out.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import tempfile
from pathlib import Path

from app import create_app
import db
import payments
import supplier
import test_support
from seed import copy_images, seed_app

TEST_DB = Path(tempfile.gettempdir()) / "playbook_tees_payments_test.db"

failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  {detail}" if not condition else ""))
    if not condition:
        failures.append(name)


def csrf(html: str) -> str:
    import re

    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    if not match:
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return match.group(1) if match else ""


class FakeRazorpay:
    """Stands in for the Razorpay API so no money and no network are needed."""

    def __init__(self):
        self.orders = {}
        self.counter = 0

    def create(self, amount_cents, receipt, notes=None):
        self.counter += 1
        order_id = f"order_FAKE{self.counter:04d}"
        self.orders[order_id] = {
            "id": order_id,
            "amount": int(amount_cents),
            "currency": "INR",
            "status": "created",
            "receipt": receipt,
            "notes": notes or {},
        }
        return dict(self.orders[order_id])

    def fetch(self, order_id):
        if order_id not in self.orders:
            raise payments.PaymentError("Razorpay returned HTTP 404.")
        return dict(self.orders[order_id])


def main() -> int:
    app = create_app()
    app.config["TESTING"] = True
    app.config["RAZORPAY_KEY_ID"] = "rzp_test_key"
    app.config["RAZORPAY_KEY_SECRET"] = "secret_for_testing"
    app.config["RAZORPAY_WEBHOOK_SECRET"] = "webhook_secret"
    # The UPI id in .env must not change what these tests check.
    app.config["UPI_ID"] = ""
    app.config["COD_ENABLED"] = False
    test_support.prepare(app, str(TEST_DB))

    with app.app_context():
        print("=== A. Payment signatures ===")
        check("payments are reported as configured", payments.payments_configured() is True)
        check("the mode label is live", payments.mode_label() == "live")

        order_id = "order_ABC123"
        payment_id = "pay_XYZ789"
        body = f"{order_id}|{payment_id}".encode()
        good = hmac.new(b"secret_for_testing", body, hashlib.sha256).hexdigest()

        check("a correct signature is accepted",
              payments.verify_payment_signature(order_id, payment_id, good) is True)
        check("a tampered signature is refused",
              payments.verify_payment_signature(order_id, payment_id, good[:-1] + "0") is False)
        check("a signature from another order is refused",
              payments.verify_payment_signature("order_OTHER", payment_id, good) is False)
        check("a missing signature is refused",
              payments.verify_payment_signature(order_id, payment_id, "") is False)
        check("a missing payment id is refused",
              payments.verify_payment_signature(order_id, "", good) is False)

        raw = b'{"event":"payment.captured"}'
        webhook = hmac.new(b"webhook_secret", raw, hashlib.sha256).hexdigest()
        check("a correct webhook signature is accepted",
              payments.verify_webhook_signature(raw, webhook) is True)
        check("a wrong webhook signature is refused",
              payments.verify_webhook_signature(raw, "nope") is False)
        check("a changed webhook body is refused",
              payments.verify_webhook_signature(b'{"event":"payment.failed"}', webhook) is False)

        print("\n=== B. No credentials means no payment ===")
        app.config["RAZORPAY_KEY_ID"] = ""
        app.config["RAZORPAY_KEY_SECRET"] = ""
        app.config["UPI_ID"] = ""
        check("payments are not configured", payments.payments_configured() is False)
        check("the mode label is test", payments.mode_label() == "test")
        check("the missing razorpay settings are listed",
              payments.razorpay_missing_settings() == ["RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"])
        check("the missing settings name both methods",
              "UPI_ID" in payments.missing_payment_settings()
              and "RAZORPAY_KEY_ID" in payments.missing_payment_settings())
        check("a signature cannot be verified without a secret",
              payments.verify_payment_signature("a", "b", "c") is False)

        payload = payments.checkout_payload("PT-260101-ABCDE", 59900)
        check("the test payload has no key", payload.get("mode") == "test")
        check("the test payload has no key id", payload.get("key_id") is None)

        print("\n=== B2. UPI links carry the exact amount ===")
        app.config["UPI_ID"] = "tester@upi"
        app.config["UPI_PAYEE_NAME"] = "Test Payee"

        link = payments.upi_link(59900, "PT-260101-ABCDE")
        check("the link is a upi deep link", link.startswith("upi://pay?"), link)
        check("the link carries the vpa", "pa=tester%40upi" in link, link)
        check("the link carries the amount", "am=599" in link, link)
        check("the link carries the currency", "cu=INR" in link, link)
        check("the link carries the order number", "tn=PT-260101-ABCDE" in link, link)
        check("the payee name is encoded with %20 not +", "%20" in link and "+" not in link, link)

        link = payments.upi_link(64850, "PT-260101-ABCDE")
        check("a paise amount keeps two decimals", "am=648.50" in link, link)

        link = payments.upi_link(100000, "PT-260101-ABCDE")
        check("a big amount has no thousand separator", "am=1000" in link, link)

        data_uri = payments.upi_qr_data_uri(59900, "PT-260101-ABCDE")
        check("a QR code is generated", data_uri.startswith("data:image/png;base64,"))
        check("the QR is small", len(data_uri) < 20000, str(len(data_uri)))

        app.config["UPI_ID"] = ""
        check("no upi id means no link", payments.upi_link(59900, "PT-260101-ABCDE") == "")
        check("no upi id means no QR", payments.upi_qr_data_uri(59900, "PT-260101-ABCDE") == "")

        print("\n=== C. Supplier payload ===")
        order = {
            "order_number": "PT-260101-ABCDE",
            "total_cents": 124800,
            "customer_name": "Test Customer",
            "phone": "9876543210",
            "email": "test@example.com",
            "address_line1": "12 MG Road",
            "address_line2": "Near the park",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pincode": "560001",
        }
        lines = [
            {
                "product_id": 1,
                "variant_id": 2,
                "name": "Cricket Fan Graphic T-Shirt",
                "size": "M",
                "sku": "CR-M",
                "supplier_sku": "SUP-CR-M",
                "unit_price_cents": 59900,
                "quantity": 2,
            }
        ]
        payload = supplier.build_supplier_payload(order, lines)

        check("the order number is short enough", len(payload["order_number"]) <= 15,
              payload["order_number"])
        check("the order number has no dashes", "-" not in payload["order_number"])
        check("shipping is enabled", payload["qikink_shipping"] == "1")
        check("the gateway is Prepaid", payload["gateway"] == "Prepaid")
        check("the total value is a rupee string", payload["total_order_value"] == "1248.00",
              payload["total_order_value"])

        item = payload["line_items"][0]
        check("the line item looks up the supplier catalogue",
              item["search_from_my_products"] == 1)
        check("the supplier sku is used", item["sku"] == "SUP-CR-M")
        check("the quantity is a string", item["quantity"] == "2")
        check("the price is a string", item["price"] == "599.00", item["price"])

        address = payload["shipping_address"]
        check("the first name is split off", address["first_name"] == "Test")
        check("the last name is split off", address["last_name"] == "Customer")
        check("the address is joined", address["address"] == "12 MG Road, Near the park")
        check("the city is sent", address["city"] == "Bengaluru")
        check("the state is sent as province", address["province"] == "Karnataka")
        check("the pincode is sent as zip", address["zip"] == "560001")
        check("the country is India", address["country_code"] == "IN")

        print("\n=== D. Supplier is not connected ===")
        check("the supplier is not configured", supplier.supplier_configured() is False)
        status = supplier.supplier_status()
        check("the status says it is not configured", status["configured"] is False)
        check("the status lists the missing settings",
              "QIKINK_CLIENT_ID" in status["missing"])
        check("the status explains what to do", "not connected" in status["note"].lower())
        check("orders cannot be submitted",
              status["can_submit_orders"] is False)

        try:
            supplier.submit_order(order, lines)
            check("submitting without credentials raises", False)
        except supplier.SupplierNotConfigured as exc:
            check("submitting without credentials raises", True)
            check("the error says what is missing", "QIKINK_CLIENT_ID" in str(exc))

        result = supplier.check_connection()
        check("the connection check fails honestly", result["ok"] is False)
        check("the connection check explains what is missing", "Missing" in result["message"])

        live_payment_flow()

    print()
    if failures:
        print(f"{len(failures)} FAILURES")
        for failure in failures:
            print(" -", failure)
        return 1
    print("All integration tests passed.")
    return 0


def build_live_app():
    """A throwaway store with Razorpay switched on."""
    if TEST_DB.exists():
        TEST_DB.unlink()
    app = create_app()
    app.config["TESTING"] = True
    app.config["RAZORPAY_KEY_ID"] = "rzp_test_key"
    app.config["RAZORPAY_KEY_SECRET"] = "secret_for_testing"
    app.config["RAZORPAY_WEBHOOK_SECRET"] = "webhook_secret"
    app.config["UPI_ID"] = ""
    test_support.prepare(app, str(TEST_DB))
    with app.app_context():
        db.init_db()
        copy_images()
        seed_app(app, verbose=False)
    return app


def place_order(client, token, notes=""):
    """Fill in the checkout form and return the order number."""
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
            "notes": notes,
        },
    )
    location = response.headers.get("Location", "")
    return location.rsplit("/", 1)[-1] if "/checkout/pay/" in location else ""


def sign(order_id, payment_id):
    body = f"{order_id}|{payment_id}".encode()
    return hmac.new(b"secret_for_testing", body, hashlib.sha256).hexdigest()


def live_payment_flow():
    """Exercise the live payment path with the Razorpay API stubbed out."""
    print("\n=== E. Live payment flow (Razorpay stubbed) ===")
    app = build_live_app()
    gateway = FakeRazorpay()

    payments.create_razorpay_order = gateway.create
    payments.fetch_razorpay_order = gateway.fetch

    client = app.test_client()
    token = csrf(client.get("/").get_data(as_text=True))

    client.post(
        "/api/cart/add",
        json={"product_id": 1, "size": "M", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    token = csrf(client.get("/checkout").get_data(as_text=True))

    order_number = place_order(client, token)
    check("the order was created", bool(order_number), order_number)
    check("one gateway order was created", gateway.counter == 1, str(gateway.counter))

    body = client.get(f"/checkout/pay/{order_number}").get_data(as_text=True)
    check("the pay page opens the payment window", "checkout.razorpay.com" in body)
    check("the gateway order id reaches the browser", "order_FAKE0001" in body)

    client.get(f"/checkout/pay/{order_number}")
    check("reloading the pay page does not create another gateway order",
          gateway.counter == 1, str(gateway.counter))

    body = client.get(f"/checkout/order/{order_number}").get_data(as_text=True)
    check("the unpaid order offers a Pay now button", "Pay now" in body)

    response = client.post(
        "/checkout/api/verify",
        json={
            "order_number": order_number,
            "razorpay_order_id": "order_FAKE0001",
            "razorpay_payment_id": "pay_FAKE1",
            "razorpay_signature": sign("order_FAKE0001", "pay_FAKE1"),
        },
        headers={"X-CSRF-Token": token},
    )
    check("a verified payment is accepted", response.get_json().get("ok") is True,
          response.get_data(as_text=True))
    body = client.get(f"/checkout/order/{order_number}").get_data(as_text=True)
    check("the order is marked paid", "Payment received" in body)

    # A signature is only valid for the gateway order it was made for, so a
    # cheap order cannot be replayed onto an expensive one.
    client.post(
        "/api/cart/add",
        json={"product_id": 2, "size": "L", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    token = csrf(client.get("/checkout").get_data(as_text=True))
    second = place_order(client, token)
    check("a second order was created", bool(second), second)
    check("a second gateway order was created", gateway.counter == 2, str(gateway.counter))

    response = client.post(
        "/checkout/api/verify",
        json={
            "order_number": second,
            "razorpay_order_id": "order_FAKE0001",
            "razorpay_payment_id": "pay_FAKE1",
            "razorpay_signature": sign("order_FAKE0001", "pay_FAKE1"),
        },
        headers={"X-CSRF-Token": token},
    )
    check("a payment from another order is refused",
          response.get_json().get("ok") is not True)
    body = client.get(f"/checkout/order/{second}").get_data(as_text=True)
    check("the replayed order is still unpaid", "Payment received" not in body)

    # The webhook confirms the payment on its own, using Razorpay's payload.
    with app.app_context():
        from orders import get_order_by_number

        second_total = get_order_by_number(second)["total_cents"]

    webhook_body = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_FAKE2",
                    "order_id": "order_FAKE0002",
                    "amount": second_total,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {"order_number": second},
                }
            }
        },
    }
    raw = json.dumps(webhook_body).encode()
    signature = hmac.new(b"webhook_secret", raw, hashlib.sha256).hexdigest()
    response = client.post(
        "/checkout/api/webhooks/razorpay",
        data=raw,
        content_type="application/json",
        headers={"X-Razorpay-Signature": signature},
    )
    check("the webhook is accepted", response.status_code == 200, str(response.status_code))
    body = client.get(f"/checkout/order/{second}").get_data(as_text=True)
    check("the webhook marks the order paid", "Payment received" in body)

    response = client.post(
        "/checkout/api/webhooks/razorpay",
        data=raw,
        content_type="application/json",
        headers={"X-Razorpay-Signature": "wrong"},
    )
    check("a webhook with a bad signature is refused", response.status_code == 400)

    # Money that is not the money that was due must not confirm the order.
    client.post(
        "/api/cart/add",
        json={"product_id": 3, "size": "M", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    token = csrf(client.get("/checkout").get_data(as_text=True))
    third = place_order(client, token)

    webhook_body = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_FAKE3",
                    "order_id": "order_FAKE0003",
                    "amount": 1,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {"order_number": third},
                }
            }
        },
    }
    raw = json.dumps(webhook_body).encode()
    signature = hmac.new(b"webhook_secret", raw, hashlib.sha256).hexdigest()
    response = client.post(
        "/checkout/api/webhooks/razorpay",
        data=raw,
        content_type="application/json",
        headers={"X-Razorpay-Signature": signature},
    )
    check("a webhook with the wrong amount is refused", response.status_code == 400)
    body = client.get(f"/checkout/order/{third}").get_data(as_text=True)
    check("the mismatched order is not marked paid", "Payment received" not in body)

    print("\n=== F. Webhook payload parsing ===")
    nested = payments.extract_webhook_payment(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_123",
                        "order_id": "order_123",
                        "amount": 59900,
                        "notes": {"order_number": "PT-260101-ABCDE"},
                    }
                }
            },
        }
    )
    check("the order number is found", nested["order_number"] == "PT-260101-ABCDE",
          nested["order_number"])
    check("the payment id is found", nested["payment_id"] == "pay_123", nested["payment_id"])
    check("the gateway order id is found", nested["razorpay_order_id"] == "order_123",
          nested["razorpay_order_id"])
    check("the amount is found", nested["amount"] == 59900, str(nested["amount"]))

    flat = payments.extract_webhook_payment(
        {"payload": {"payment": {"id": "pay_9", "notes": {"order_number": "PT-1"}}}}
    )
    check("the flat payload shape is understood", flat["order_number"] == "PT-1",
          flat["order_number"])
    check("an empty payload is harmless", payments.extract_webhook_payment({})["order_number"] == "")


if __name__ == "__main__":
    sys.exit(main())
