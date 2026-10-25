"""Seed the catalogue once when the database is empty.

The Render start command runs this before Gunicorn. It is safe to run on every
cold start: existing products are never touched.
"""

from __future__ import annotations

from app import create_app
from db import query_one


def main() -> None:
    app = create_app()
    with app.app_context():
        if query_one("SELECT id FROM products LIMIT 1"):
            print("Products already exist; skipping seed.")
            return

        from seed import seed_app

        seed_app(app)
        print("Seeded the initial product catalogue.")


if __name__ == "__main__":
    main()
