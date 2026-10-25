"""Order notifications.

Email is sent over SMTP when it is configured; otherwise every message is
written to the application log so nothing is silently pretended. WhatsApp uses
a click-to-chat link (no paid API needed) and can optionally be pushed through
an SMS/WhatsApp webhook.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Dict
from urllib.parse import quote

import requests
from flask import current_app


def email_configured() -> bool:
    return bool(current_app.config.get("SMTP_HOST") and current_app.config.get("SMTP_FROM"))


def smtp_ready() -> bool:
    return email_configured()


def send_email(to: str, subject: str, body: str) -> bool:
    """Send one plain-text email. Returns True when it really went out."""
    to = (to or "").strip()
    if not to:
        return False
    if not email_configured():
        current_app.logger.info(
            "EMAIL (not configured) to=%s subject=%s\n%s", to, subject, body
        )
        return False

    message = EmailMessage()
    message["From"] = current_app.config["SMTP_FROM"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    host = current_app.config["SMTP_HOST"]
    port = int(current_app.config.get("SMTP_PORT") or 587)
    user = current_app.config.get("SMTP_USER") or ""
    password = current_app.config.get("SMTP_PASSWORD") or ""
    try:
        if current_app.config.get("SMTP_USE_TLS", True):
            context = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=20) as server:
                server.starttls(context=context)
                if user:
                    server.login(user, password)
                server.send_message(message)
        else:
            with smtplib.SMTP_SSL(host, port, timeout=20) as server:
                if user:
                    server.login(user, password)
                server.send_message(message)
    except Exception as exc:  # pragma: no cover - network dependent
        current_app.logger.warning("EMAIL failed to=%s: %s", to, exc)
        return False
    return True


def send_sms(phone: str, message: str) -> bool:
    """Send an SMS/WhatsApp message through an optional webhook."""
    url = (current_app.config.get("SMS_WEBHOOK_URL") or "").strip()
    phone = (phone or "").strip()
    if not url or not phone:
        current_app.logger.info("SMS (not configured) to=%s: %s", phone, message)
        return False
    try:
        response = requests.post(
            url, json={"phone": phone, "message": message}, timeout=15
        )
    except requests.RequestException as exc:  # pragma: no cover - network dependent
        current_app.logger.warning("SMS failed to=%s: %s", phone, exc)
        return False
    return 200 <= response.status_code < 300


def _owner_phone() -> str:
    return (
        (current_app.config.get("ORDER_ALERT_PHONE") or "").strip()
        or (current_app.config.get("CALLMEBOT_PHONE") or "").strip()
    )


def _e164(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return f"+{digits}" if digits else ""


def send_owner_alert(message: str) -> bool:
    """Tell the shop owner about a new order.

    Tries, in order: free WhatsApp through CallMeBot, free push through
    ntfy.sh, then the generic SMS webhook. Returns True when one of them
    accepted the message. With nothing configured the alert is logged, so the
    site never silently pretends the owner was told.
    """
    phone = _owner_phone()
    callmebot_key = (current_app.config.get("CALLMEBOT_API_KEY") or "").strip()

    if callmebot_key and phone:
        try:
            response = requests.get(
                "https://api.callmebot.com/whatsapp.php",
                params={
                    "phone": _e164(phone),
                    "text": message,
                    "apikey": callmebot_key,
                },
                timeout=15,
            )
            if 200 <= response.status_code < 300:
                return True
            current_app.logger.warning(
                "CALLMEBOT failed to=%s: HTTP %s", phone, response.status_code
            )
        except requests.RequestException as exc:  # pragma: no cover - network
            current_app.logger.warning("CALLMEBOT failed to=%s: %s", phone, exc)

    topic = (current_app.config.get("NTFY_TOPIC") or "").strip()
    if topic:
        try:
            response = requests.post(
                f"https://ntfy.sh/{quote(topic, safe='')}",
                data=message.encode("utf-8"),
                headers={
                    "Title": f"New order - {current_app.config['STORE_NAME']}",
                    "Tags": "shopping_cart",
                    "Priority": "high",
                },
                timeout=15,
            )
            if 200 <= response.status_code < 300:
                return True
            current_app.logger.warning(
                "NTFY failed topic=%s: HTTP %s", topic, response.status_code
            )
        except requests.RequestException as exc:  # pragma: no cover - network
            current_app.logger.warning("NTFY failed topic=%s: %s", topic, exc)

    webhook = (current_app.config.get("SMS_WEBHOOK_URL") or "").strip()
    if webhook and phone:
        if send_sms(phone, message):
            return True

    if not (callmebot_key or topic or webhook):
        current_app.logger.info(
            "OWNER ALERT (not configured) to=%s: %s", phone or "(no number)", message
        )
    return False


def whatsapp_link(phone: str, message: str = "") -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    if not digits:
        return ""
    if message:
        return f"https://wa.me/{digits}?text={quote(message)}"
    return f"https://wa.me/{digits}"


def _rupees(cents: int) -> str:
    return f"Rs {int(cents) / 100:.2f}"


def order_summary(order: Dict) -> str:
    lines = [
        f"Order {order['order_number']}",
        f"Name: {order['customer_name']}",
        f"Total: {_rupees(order['total_cents'])}",
        f"Payment: {order.get('payment_label') or order.get('payment_status', '')}",
        "Items:",
    ]
    for item in order.get("items", []):
        lines.append(
            f"  - {item['product_name']} ({item['size']}) x{item['quantity']} "
            f"{_rupees(item['unit_price_cents'] * item['quantity'])}"
        )
    return "\n".join(lines)


def owner_summary(order: Dict) -> str:
    lines = [
        f"New order {order['order_number']}",
        f"Customer: {order['customer_name']} ({order.get('phone', '')})",
        f"Total: {_rupees(order['total_cents'])}",
        f"Payment: {order.get('payment_label') or order.get('payment_status', '')}",
        "Items:",
    ]
    for item in order.get("items", []):
        lines.append(
            f"  - {item['product_name']} ({item['size']}) x{item['quantity']}"
        )
    city = order.get("city") or ""
    pincode = order.get("pincode") or ""
    if city or pincode:
        lines.append(f"Ship to: {city} {pincode}".strip())
    return "\n".join(lines)


def notify_order_placed(order: Dict) -> None:
    subject = f"Order {order['order_number']} confirmed - {current_app.config['STORE_NAME']}"
    body = order_summary(order) + "\n\nWe will message you when it ships."
    send_email(order.get("email", ""), subject, body)
    alert = current_app.config.get("ORDER_ALERT_EMAIL") or current_app.config.get("STORE_EMAIL")
    if alert and alert != order.get("email"):
        send_email(alert, f"New order {order['order_number']}", order_summary(order))
    send_sms(order.get("phone", ""), f"Order {order['order_number']} confirmed. Total {_rupees(order['total_cents'])}.")
    send_owner_alert(owner_summary(order))


def notify_order_status(order: Dict, status: str) -> None:
    label = order.get("status_label") or status
    subject = f"Order {order['order_number']} is now {label}"
    body = f"Hi {order['customer_name']},\n\nYour order {order['order_number']} is now {label}."
    if order.get("tracking_number"):
        body += f"\nCarrier: {order['carrier']} {order['tracking_number']}"
    body += f"\n\n{current_app.config['STORE_NAME']}"
    send_email(order.get("email", ""), subject, body)
    send_sms(order.get("phone", ""), f"Order {order['order_number']} is now {label}.")
