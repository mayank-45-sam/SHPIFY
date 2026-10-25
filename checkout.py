"""Checkout, payment verification and order confirmation.

Order of operations for a real payment:

    1. The customer fills in the checkout form (server-side validation).
    2. The server re-checks the cart, the prices and the stock.
    3. The server creates the order with payment_status = 'pending' and one
       Razorpay order, whose id is stored on the order row.
    4. The browser opens Razorpay Checkout against that gateway order.
    5. Razorpay redirects back to /checkout/verify with a signature.
    6. The server verifies the HMAC signature *and* checks that the gateway
       order in the signature is the one it created for this order. Only
       then is the order marked paid. A tampered signature, or a signature
       from another order, leaves the order unpaid.
    7. Razorpay's webhook repeats the verification independently.

The same gateway order is reused when the customer comes back to pay, so one
store order never becomes a pile of Razorpay orders.

With no credentials configured the order is stored with payment_status =
'test_mode' and every screen says so. There is no path that marks an order
paid without a verified signature.
"""

from __future__ import annotations

import hmac
import re
from typing import Any, Dict, List, Tuple

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import catalog
import coupons
import notifications
import otp
import payments
import returns
import serviceability
from cart import cart_lines, cart_payload, cart_token, clear_cart
from db import query_one
from invoices import invoice_data
from orders import (
    create_order,
    get_order,
    get_order_by_number,
    get_order_by_razorpay_order_id,
    mark_paid,
    mark_payment_failed,
    mark_payment_submitted,
    set_razorpay_order_id,
)
from pricing import build_totals
from security import validate_csrf

bp = Blueprint("checkout", __name__, url_prefix="/checkout")


def _apply_extras(totals: Dict[str, Any], discount_cents: int, cod_fee_cents: int) -> Dict[str, Any]:
    """Return a copy of the totals with a discount and a COD fee applied."""
    result = dict(totals)
    subtotal = int(result["subtotal_cents"])
    discount = max(0, min(int(discount_cents or 0), subtotal))
    fee = max(0, int(cod_fee_cents or 0))
    result["discount_cents"] = discount
    result["cod_fee_cents"] = fee
    result["total_cents"] = subtotal + int(result["shipping_cents"]) - discount + fee
    return result

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PINCODE_RE = re.compile(r"^\d{6}$")


# ---------------------------------------------------------------------------
# Form validation
# ---------------------------------------------------------------------------


def normalise_phone(raw: str) -> str:
    """Return a 10 digit Indian mobile number, or raise ValueError."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) != 10:
        raise ValueError("Enter a 10 digit mobile number.")
    if not digits[0] in "6789":
        raise ValueError("That does not look like a valid Indian mobile number.")
    return digits


def validate_customer(form) -> Tuple[Dict[str, str], List[str]]:
    """Validate the checkout form. Returns (clean_data, error_messages)."""
    data = {
        "customer_name": (form.get("customer_name") or "").strip(),
        "phone": (form.get("phone") or "").strip(),
        "email": (form.get("email") or "").strip(),
        "address_line1": (form.get("address_line1") or "").strip(),
        "address_line2": (form.get("address_line2") or "").strip(),
        "city": (form.get("city") or "").strip(),
        "state": (form.get("state") or "").strip(),
        "pincode": (form.get("pincode") or "").strip(),
    }
    errors: List[str] = []

    if len(data["customer_name"]) < 2:
        errors.append("Please enter your full name.")

    try:
        data["phone"] = normalise_phone(data["phone"])
    except ValueError as exc:
        errors.append(str(exc))

    if not EMAIL_RE.match(data["email"]):
        errors.append("Please enter a valid email address.")

    if len(data["address_line1"]) < 5:
        errors.append("Please enter your street address.")

    if len(data["city"]) < 2:
        errors.append("Please enter your city.")

    if len(data["state"]) < 2:
        errors.append("Please enter your state.")

    if not PINCODE_RE.match(data["pincode"]):
        errors.append("PIN code must be exactly 6 digits.")

    return data, errors


def stock_problems(lines: List[Dict[str, Any]]) -> List[str]:
    """Re-check availability right before the order is created."""
    problems: List[str] = []
    for line in lines:
        product = catalog.get_product(int(line["product_id"]))
        if product is None or product["status"] != "published":
            problems.append(f"{line['name']} is no longer available.")
            continue
        if product["stock_mode"] != "tracked":
            continue
        variant = next((v for v in product["variants"] if v["size"] == line["size"]), None)
        if variant is None or variant["stock"] < int(line["quantity"]):
            available = variant["stock"] if variant else 0
            problems.append(
                f"{line['name']} (size {line['size']}) only has {available} left."
            )
    return problems


def payment_matches_order(
    order: Dict[str, Any],
    razorpay_order_id: str,
    razorpay_payment_id: str,
) -> bool:
    """Check that the signed payment belongs to *this* order.

    The signature only proves that Razorpay signed a payment for a gateway
    order. Without this check, a customer could pay a cheap order and then
    replay that signature onto an expensive one, so the gateway order id we
    created for this order has to match.
    """
    expected = (order.get("razorpay_order_id") or "").strip()
    if not expected:
        expected = (session.get("razorpay_order_id") or "").strip()
    if expected:
        return hmac.compare_digest(expected, razorpay_order_id or "")

    # Nothing was recorded for this order (it was placed before the gateway
    # order id was stored). Ask Razorpay what was paid and compare it.
    try:
        payment = payments.fetch_razorpay_payment(razorpay_payment_id)
    except payments.PaymentError:
        return False
    if int(payment.get("amount") or 0) != int(order["total_cents"]):
        return False
    if payment.get("order_id"):
        return hmac.compare_digest(str(payment["order_id"]), razorpay_order_id or "")
    return True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route("", methods=["GET", "POST"])
def checkout():
    """Checkout form. On POST the order is created and payment is started."""
    lines = cart_lines()
    totals = build_totals(lines)

    if not lines:
        flash("Your cart is empty. Add a tee before checking out.", "info")
        return redirect(url_for("storefront.shop"))

    errors: List[str] = []
    customer: Dict[str, str] = {}

    methods = payments.available_methods()
    chosen_method = (request.form.get("payment_method") or "").strip()
    if chosen_method not in payments.method_ids():
        chosen_method = payments.default_method()

    coupon_code = (
        request.form.get("coupon_code") or session.get("coupon_code") or ""
    ).strip().upper()
    discount_cents = 0
    coupon_row = None
    coupon_message = ""

    # A coupon applied from the cart/checkout page (GET) is shown right away.
    if request.method == "GET" and coupon_code:
        ok, discount_cents, coupon_message, coupon_row = coupons.validate_coupon(
            coupon_code, totals["subtotal_cents"]
        )
        if ok:
            totals = _apply_extras(totals, discount_cents, 0)
        else:
            session.pop("coupon_code", None)
            coupon_code = ""

    if request.method == "POST":
        validate_csrf()
        customer, errors = validate_customer(request.form)
        problems = stock_problems(lines)

        if errors or problems:
            errors.extend(problems)
        else:
            # Prices always come from the server side catalog.
            authoritative = []
            for line in lines:
                product = catalog.get_product(int(line["product_id"]))
                authoritative.append(
                    {
                        "product_id": line["product_id"],
                        "variant_id": line["variant_id"],
                        "name": product["name"],
                        "size": line["size"],
                        "sku": line["sku"],
                        "supplier_sku": product.get("supplier_sku", ""),
                        "image": product.get("primary_image"),
                        "custom_front": line.get("custom_front", ""),
                        "custom_back": line.get("custom_back", ""),
                        "unit_price_cents": int(line["unit_price_cents"]),
                        "quantity": int(line["quantity"]),
                    }
                )

            totals = build_totals(authoritative)

            if chosen_method == "razorpay":
                method, payment_status, order_status = "razorpay", "pending", "awaiting_payment"
            elif chosen_method == "upi":
                method, payment_status, order_status = "upi", "pending", "awaiting_payment"
            elif chosen_method == "cod":
                method, payment_status, order_status = "cod", "cod", "confirmed"
            else:
                # No method at all: keep the honest test order.
                method, payment_status, order_status = "test", "test_mode", "test_order"

            # Pincode serviceability and delivery estimate.
            deliverable, pin_message, estimate = serviceability.serviceable(customer["pincode"])
            if not deliverable:
                errors.append(pin_message)

            # Coupon (re-validated against the authoritative subtotal).
            coupon_ok = False
            if coupon_code:
                coupon_ok, discount_cents, coupon_message, coupon_row = coupons.validate_coupon(
                    coupon_code, totals["subtotal_cents"]
                )
                if not coupon_ok:
                    errors.append(coupon_message)
                    discount_cents = 0
                    coupon_row = None

            # Phone verification for COD (when enabled).
            otp_verified = True
            if method == "cod" and current_app.config.get("OTP_REQUIRED") and not errors:
                otp_verified, otp_message = otp.verify_code(
                    customer["phone"], request.form.get("otp_code") or ""
                )
                if not otp_verified:
                    errors.append(otp_message)

            cod_fee_cents = (
                int(current_app.config.get("COD_FEE") or 0) * 100 if method == "cod" else 0
            )
            totals = _apply_extras(
                totals, discount_cents if coupon_ok else 0, cod_fee_cents
            )

            if not errors:
                order = create_order(
                    customer=customer,
                    lines=authoritative,
                    totals=totals,
                    payment_method=method,
                    payment_status=payment_status,
                    status=order_status,
                    notes=(request.form.get("notes") or "").strip(),
                    decrement_stock=(method == "cod"),
                    coupon_code=coupon_code if coupon_ok else "",
                    discount_cents=discount_cents if coupon_ok else 0,
                    cod_fee_cents=cod_fee_cents,
                    cart_token=cart_token(),
                    otp_verified=otp_verified,
                    delivery_estimate=estimate,
                )
                session["last_order_number"] = order["order_number"]
                session.pop("coupon_code", None)
                if coupon_ok and coupon_row:
                    coupons.redeem_coupon(coupon_row["id"], order["id"])
                notifications.notify_order_placed(order)

                if method == "razorpay":
                    try:
                        payload = payments.checkout_payload(order["order_number"], totals["total_cents"])
                    except payments.PaymentError as exc:
                        mark_payment_failed(order["id"], str(exc))
                        errors.append(f"Could not start the payment: {exc}")
                    else:
                        if payload.get("order_id"):
                            set_razorpay_order_id(order["id"], payload["order_id"])
                        session["razorpay_order_id"] = payload.get("order_id")
                        return redirect(url_for("checkout.pay", order_number=order["order_number"]))
                elif method == "upi":
                    # The customer scans the QR on the pay page. The order stays
                    # unpaid until the money is confirmed in the admin area.
                    clear_cart()
                    session["clear_local_cart"] = True
                    return redirect(url_for("checkout.pay", order_number=order["order_number"]))
                elif method == "cod":
                    # Cash is collected by the courier; stock is held now.
                    clear_cart()
                    session["clear_local_cart"] = True
                    return redirect(url_for("checkout.order_confirmation", order_number=order["order_number"]))
                else:
                    # Test mode: the order exists but no money has moved.
                    clear_cart()
                    session["clear_local_cart"] = True
                    return redirect(url_for("checkout.order_confirmation", order_number=order["order_number"]))

    return render_template(
        "checkout.html",
        lines=lines,
        totals=totals,
        errors=errors,
        customer=customer,
        payment_methods=methods,
        chosen_method=chosen_method,
        payment_configured=payments.live_payments_available(),
        missing_payment=payments.missing_payment_settings(),
        coupon_code=coupon_code,
        coupon_message=coupon_message,
        coupon_applied=bool(coupon_row and discount_cents),
        cod_fee=current_app.config.get("COD_FEE") or 0,
        otp_required=bool(current_app.config.get("OTP_REQUIRED")),
    )


@bp.post("/coupon")
def apply_coupon():
    """Remember a coupon for the checkout page."""
    validate_csrf()
    code = (request.form.get("coupon_code") or "").strip().upper()
    if code:
        session["coupon_code"] = code
    else:
        session.pop("coupon_code", None)
    return redirect(url_for("checkout.checkout"))


@bp.post("/coupon/remove")
def remove_coupon():
    validate_csrf()
    session.pop("coupon_code", None)
    return redirect(url_for("checkout.checkout"))


@bp.get("/api/pincode/<pincode>")
def pincode_check(pincode: str):
    deliverable, message, estimate = serviceability.serviceable(pincode)
    return jsonify({"ok": deliverable, "message": message, "estimate": estimate})


@bp.post("/otp/send")
def otp_send():
    validate_csrf()
    data = request.get_json(silent=True) or request.form
    phone = (data.get("phone") or "").strip()
    ok, message, dev_code = otp.send_code(phone)
    payload = {"ok": ok, "message": message}
    if dev_code:
        payload["dev_code"] = dev_code
    return jsonify(payload), (200 if ok else 400)


@bp.post("/otp/verify")
def otp_verify():
    validate_csrf()
    data = request.get_json(silent=True) or request.form
    ok, message = otp.verify_code(
        (data.get("phone") or "").strip(), (data.get("code") or "").strip()
    )
    return jsonify({"ok": ok, "message": message}), (200 if ok else 400)


@bp.get("/pay/<order_number>")
def pay(order_number: str):
    """The payment page for an order.

    UPI orders get the QR code to scan. Razorpay orders open the payment
    window, reusing the same gateway order if the customer comes back to
    pay, so the Razorpay dashboard shows one order per store order.
    """
    order = get_order_by_number(order_number)
    if order is None:
        return render_template("404.html", code=404, message="Order not found."), 404

    method = order["payment_method"]
    clear_local_cart = session.pop("clear_local_cart", False)

    if method == "upi":
        if not payments.upi_configured():
            flash("The UPI QR code is not available right now.", "error")
            return redirect(url_for("checkout.order_confirmation", order_number=order_number))
        return render_template(
            "checkout_pay_upi.html",
            order=order,
            upi=payments.upi_details(),
            upi_link=payments.upi_link(order["total_cents"], order["order_number"]),
            upi_qr=payments.upi_qr_data_uri(order["total_cents"], order["order_number"]),
            storefront_name=current_app.config["STORE_NAME"],
            clear_local_cart=clear_local_cart,
        )

    if method != "razorpay":
        return redirect(url_for("checkout.order_confirmation", order_number=order_number))

    if not payments.payments_configured():
        return redirect(url_for("checkout.order_confirmation", order_number=order_number))
    if order["payment_status"] == "paid":
        return redirect(url_for("checkout.order_confirmation", order_number=order_number))

    try:
        payload = payments.checkout_payload(
            order["order_number"],
            order["total_cents"],
            order.get("razorpay_order_id") or "",
        )
    except payments.PaymentError as exc:
        flash(f"Could not open the payment window: {exc}", "error")
        return redirect(url_for("checkout.order_confirmation", order_number=order_number))

    if payload.get("order_id"):
        set_razorpay_order_id(order["id"], payload["order_id"])

    return render_template(
        "checkout_pay.html",
        order=order,
        payload=payload,
        storefront_name=current_app.config["STORE_NAME"],
        clear_local_cart=clear_local_cart,
    )


@bp.post("/upi/paid")
def upi_payment_sent():
    """The customer says they have paid by UPI.

    This is never treated as proof. The order stays unpaid until the shop
    owner confirms the money in the admin area.
    """
    validate_csrf()
    order_number = (request.form.get("order_number") or session.get("last_order_number") or "").strip()
    order = get_order_by_number(order_number)
    if order is None:
        return render_template("404.html", code=404, message="Order not found."), 404

    # Only the customer who placed this order, or someone who can prove it
    # with the phone number on it, may report a payment.
    if session.get("last_order_number") != order["order_number"]:
        phone = (request.form.get("phone") or "").strip()
        if phone != order["phone"].strip():
            return render_template(
                "404.html", code=403, message="This order is not yours."
            ), 403

    if order["payment_status"] in ("pending", "failed"):
        mark_payment_submitted(order["id"], "upi")

    return redirect(url_for("checkout.order_confirmation", order_number=order_number))


@bp.post("/verify")
def verify_payment():
    """Verify the Razorpay signature and only then mark the order paid."""
    validate_csrf()
    data = request.form if request.form else (request.get_json(silent=True) or {})
    order_number = (data.get("order_number") or session.get("last_order_number") or "").strip()
    razorpay_order_id = (data.get("razorpay_order_id") or "").strip()
    razorpay_payment_id = (data.get("razorpay_payment_id") or "").strip()
    razorpay_signature = (data.get("razorpay_signature") or "").strip()

    order = get_order_by_number(order_number)
    if order is None:
        return render_template("404.html", code=404, message="Order not found."), 404

    if not payments.verify_payment_signature(
        razorpay_order_id, razorpay_payment_id, razorpay_signature
    ):
        mark_payment_failed(order["id"], "Signature verification failed.")
        flash(
            "We could not verify the payment. Your order is saved but unpaid. "
            "If money was debited, it will be refunded automatically.",
            "error",
        )
        return redirect(url_for("checkout.order_confirmation", order_number=order_number))

    if not payment_matches_order(order, razorpay_order_id, razorpay_payment_id):
        mark_payment_failed(order["id"], "Payment does not belong to this order.")
        flash(
            "We could not match that payment to your order. Your order is saved but "
            "unpaid. If money was debited, it will be refunded automatically.",
            "error",
        )
        return redirect(url_for("checkout.order_confirmation", order_number=order_number))

    mark_paid(order["id"], razorpay_payment_id, "razorpay")
    clear_cart()
    session["clear_local_cart"] = True
    return redirect(url_for("checkout.order_confirmation", order_number=order_number))


@bp.post("/api/verify")
def verify_payment_api():
    """Same verification, returned as JSON for the checkout page."""
    validate_csrf()
    data = request.get_json(silent=True) or {}
    order_number = (data.get("order_number") or "").strip()
    order = get_order_by_number(order_number)
    if order is None:
        return jsonify({"ok": False, "error": "Order not found."}), 404

    verified = payments.verify_payment_signature(
        (data.get("razorpay_order_id") or "").strip(),
        (data.get("razorpay_payment_id") or "").strip(),
        (data.get("razorpay_signature") or "").strip(),
    )
    if not verified:
        mark_payment_failed(order["id"], "Signature verification failed.")
        return jsonify({"ok": False, "error": "Payment could not be verified."}), 400

    razorpay_order_id = (data.get("razorpay_order_id") or "").strip()
    razorpay_payment_id = (data.get("razorpay_payment_id") or "").strip()
    if not payment_matches_order(order, razorpay_order_id, razorpay_payment_id):
        mark_payment_failed(order["id"], "Payment does not belong to this order.")
        return jsonify({"ok": False, "error": "Payment does not belong to this order."}), 400

    mark_paid(order["id"], razorpay_payment_id, "razorpay")
    clear_cart()
    session["clear_local_cart"] = True
    return jsonify({"ok": True, "redirect": url_for("checkout.order_confirmation", order_number=order_number)})


@bp.get("/order/<order_number>")
def order_confirmation(order_number: str):
    """Order status page."""
    order = get_order_by_number(order_number)
    if order is None:
        return render_template("404.html", code=404, message="Order not found."), 404

    allowed = session.get("last_order_number") == order["order_number"]
    if not allowed:
        phone = (request.args.get("phone") or "").strip()
        if phone != order["phone"].strip():
            return render_template(
                "track_order.html", order=None, error="", searched=False
            ), 403

    is_upi = order["payment_method"] == "upi"
    clear_local_cart = session.pop("clear_local_cart", False)
    existing_return = returns.return_for_order(order["id"])

    return render_template(
        "order_confirmation.html",
        order=order,
        test_mode=order["payment_status"] == "test_mode",
        clear_local_cart=clear_local_cart,
        return_request=existing_return,
        can_request_return=(
            order["status"] == "delivered" and existing_return is None
        ),
        upi=payments.upi_details() if is_upi else None,
        upi_link=(
            payments.upi_link(order["total_cents"], order["order_number"])
            if is_upi
            else ""
        ),
        upi_qr=(
            payments.upi_qr_data_uri(order["total_cents"], order["order_number"])
            if is_upi
            else ""
        ),
        can_pay_again=(
            payments.live_payments_available()
            and order["payment_method"] in ("upi", "razorpay")
            and order["payment_status"] in ("pending", "failed")
        ),
    )


@bp.get("/order/<order_number>/invoice")
def invoice(order_number: str):
    """Printable GST invoice (use the browser's Save as PDF)."""
    order = get_order_by_number(order_number)
    if order is None:
        return render_template("404.html", code=404, message="Order not found."), 404
    allowed = session.get("last_order_number") == order["order_number"]
    if not allowed and (request.args.get("phone") or "").strip() != order["phone"].strip():
        return render_template("404.html", code=403, message="This order is not yours."), 403
    return render_template("invoice.html", order=order, invoice=invoice_data(order))


@bp.post("/order/<order_number>/return")
def request_return(order_number: str):
    """Ask for a return on a delivered order."""
    validate_csrf()
    order = get_order_by_number(order_number)
    if order is None:
        return render_template("404.html", code=404, message="Order not found."), 404
    allowed = session.get("last_order_number") == order["order_number"]
    if not allowed and (request.form.get("phone") or "").strip() != order["phone"].strip():
        return render_template("404.html", code=403, message="This order is not yours."), 403

    if order["status"] != "delivered":
        flash("A return can be requested once the order is delivered.", "error")
        return redirect(url_for("checkout.order_confirmation", order_number=order_number))
    if returns.return_for_order(order["id"]) is not None:
        flash("A return request already exists for this order.", "info")
        return redirect(url_for("checkout.order_confirmation", order_number=order_number))

    reason = (request.form.get("reason") or "").strip()
    details = (request.form.get("details") or "").strip()
    if len(reason) < 3:
        flash("Tell us why you want to return the order.", "error")
        return redirect(url_for("checkout.order_confirmation", order_number=order_number))

    returns.create_return(order["id"], reason, details)
    notifications.notify_order_status(order, "return requested")
    flash("Return request received. We will contact you within one working day.", "success")
    return redirect(url_for("checkout.order_confirmation", order_number=order_number))


@bp.post("/api/webhooks/razorpay")
def razorpay_webhook():
    """Razorpay webhook. Verifies the signature independently of the browser."""
    raw = request.get_data()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not payments.verify_webhook_signature(raw, signature):
        return jsonify({"ok": False, "error": "Invalid webhook signature."}), 400

    payload = request.get_json(silent=True) or {}
    event = payload.get("event", "")
    details = payments.extract_webhook_payment(payload)

    order = None
    if details["order_number"]:
        order = get_order_by_number(details["order_number"])
    if order is None and details["razorpay_order_id"]:
        order = get_order_by_razorpay_order_id(details["razorpay_order_id"])

    if event == "payment.captured" and order and order["payment_status"] != "paid":
        amount = details["amount"]
        if amount is not None and amount != int(order["total_cents"]):
            # The money that arrived is not the money that was due. Say so
            # instead of quietly confirming the order.
            mark_payment_failed(
                order["id"],
                f"Captured {amount} paise, order total is {order['total_cents']} paise.",
            )
            return jsonify({"ok": False, "error": "Amount mismatch."}), 400

        mark_paid(order["id"], details["payment_id"], "razorpay")

    return jsonify({"ok": True})
