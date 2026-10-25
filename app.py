"""Playbook Tees - Flask application entry point.

Run it with:

    python app.py

or with the Flask CLI:

    flask --app app run --debug
"""

from __future__ import annotations

import os

from flask import Flask, render_template, request

import admin
import cart
import checkout
import db
import storefront
from config import Config
from security import csrf_token


def create_app() -> Flask:
    """Build and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    Config.init_app(app)

    db.register(app)

    app.register_blueprint(cart.bp)
    app.register_blueprint(storefront.bp)
    app.register_blueprint(checkout.bp)
    app.register_blueprint(admin.bp)

    # ------------------------------------------------------------------
    # Template helpers
    # ------------------------------------------------------------------
    @app.context_processor
    def inject_globals():
        from catalog import format_rupees, size_availability

        return {
            "csrf_token": csrf_token,
            "money": format_rupees,
            "availability": size_availability,
            "sizes": ("XS", "S", "M", "L", "XL", "2XL"),
            "category_labels": {
                "cricket": "Cricket",
                "superheroes": "Superheroes",
                "football": "Football",
            },
            "category_taglines": {
                "cricket": "Sixes, centuries and the last-over jitters.",
                "superheroes": "For the ones who save the city before breakfast.",
                "football": "Champions League mornings, all day every day.",
            },
            "site_name": app.config["STORE_NAME"],
            "site_tagline": app.config["STORE_TAGLINE"],
            "site_email": app.config["STORE_EMAIL"],
            "site_phone": app.config["STORE_PHONE"],
            "site_address": app.config["STORE_ADDRESS"],
            "currency": "₹",
            "shipping_fee": app.config["SHIPPING_FEE"],
            "free_shipping_threshold": app.config["FREE_SHIPPING_THRESHOLD"],
            "current_path": request.path,
        }

    # ------------------------------------------------------------------
    # Error pages
    # ------------------------------------------------------------------
    @app.errorhandler(404)
    def not_found(error):
        return render_template("404.html", code=404, message="We could not find that page."), 404

    @app.errorhandler(400)
    def bad_request(error):
        return (
            render_template(
                "404.html",
                code=400,
                message=getattr(error, "description", "That request was not valid."),
            ),
            400,
        )

    @app.errorhandler(413)
    def too_large(error):
        return (
            render_template(
                "404.html",
                code=413,
                message="That upload is too large. Please use an image under 8 MB.",
            ),
            413,
        )

    @app.errorhandler(500)
    def server_error(error):  # pragma: no cover - defensive
        return (
            render_template(
                "404.html",
                code=500,
                message="Something went wrong on our side. Please try again.",
            ),
            500,
        )

    with app.app_context():
        db.init_db()

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "1") not in ("0", "false", "False")
    print(f"\n  {app.config['STORE_NAME']} running at http://127.0.0.1:{port}\n")
    app.run(host="127.0.0.1", port=port, debug=debug)
