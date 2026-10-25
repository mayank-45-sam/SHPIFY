"""Security helpers: CSRF protection and admin authentication.

Secrets stay on the server. The browser only ever receives a CSRF token and
the public Razorpay key id, which is safe to expose by design.
"""

from __future__ import annotations

import functools
import hmac
import secrets
from typing import Callable

from flask import abort, flash, redirect, request, session, url_for
from werkzeug.security import check_password_hash


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


def csrf_token() -> str:
    """Return the session CSRF token, creating one if needed."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf() -> None:
    """Abort the request when the CSRF token is missing or wrong."""
    expected = session.get("_csrf_token", "")
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not expected or not sent or not hmac.compare_digest(expected, sent):
        abort(400, "Your session expired or the form was invalid. Please try again.")


# ---------------------------------------------------------------------------
# Admin authentication
# ---------------------------------------------------------------------------


def admin_logged_in() -> bool:
    return bool(session.get("admin_id"))


def login_admin(username: str, password: str) -> bool:
    """Check the credentials against the configured admin account."""
    from flask import current_app

    config = current_app.config
    if not config.get("ADMIN_PASSWORD_HASH"):
        return False
    if not hmac.compare_digest(username, config.get("ADMIN_USERNAME", "")):
        return False
    return check_password_hash(config["ADMIN_PASSWORD_HASH"], password)


def login_required(view: Callable) -> Callable:
    """Protect a view so only a signed-in admin can reach it."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not admin_logged_in():
            flash("Please sign in to the admin area.", "info")
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped
