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


def notify_order_placed(order: Dict) -> None:
    subject = f"Order {order['order_number']} confirmed - {current_app.config['STORE_NAME']}"
    body = order_summary(order) + "\n\nWe will message you when it ships."
    send_email(order.get("email", ""), subject, body)
    alert = current_app.config.get("ORDER_ALERT_EMAIL") or current_app.config.get("STORE_EMAIL")
    if alert and alert != order.get("email"):
        send_email(alert, f"New order {order['order_number']}", order_summary(order))
    send_sms(order.get("phone", ""), f"Order {order['order_number']} confirmed. Total {_rupees(order['total_cents'])}.")


def notify_order_status(order: Dict, status: str) -> None:
    label = order.get("status_label") or status
    subject = f"Order {order['order_number']} is now {label}"
    body = f"Hi {order['customer_name']},\n\nYour order {order['order_number']} is now {label}."
    if order.get("tracking_number"):
        body += f"\nCarrier: {order['carrier']} {order['tracking_number']}"
    body += f"\n\n{current_app.config['STORE_NAME']}"
    send_email(order.get("email", ""), subject, body)
    send_sms(order.get("phone", ""), f"Order {order['order_number']} is now {label}.")
