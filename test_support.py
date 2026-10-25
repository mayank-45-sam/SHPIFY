"""Shared helpers for the test suites.

Every suite runs against a throwaway database so your real orders are never
touched. The suites work with either backend:

* **SQLite** (the default) - a temporary file in the system temp folder.
* **PostgreSQL** - set ``DATABASE_URL`` and the suite wipes and uses that
  database instead. Handy for checking the shop on the real engine:

  .. code-block:: bash

      $env:DATABASE_URL = "postgresql://shopify:password@localhost:5432/shopify_test"
      python test_storefront.py
"""

from __future__ import annotations

import os
from typing import Optional

TABLES = (
    "order_events",
    "order_items",
    "orders",
    "cart_items",
    "product_variants",
    "product_images",
    "products",
)


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def is_postgres() -> bool:
    return database_url().startswith(("postgres://", "postgresql://"))


def wipe_postgres() -> None:
    """Empty every table in the PostgreSQL test database."""
    import psycopg

    with psycopg.connect(database_url(), autocommit=True) as connection:
        connection.execute("SET search_path TO public")
        connection.execute(
            f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"
        )


def prepare(app, sqlite_path: str, drop_sqlite_file: Optional[str] = None) -> str:
    """Point the app at a clean test database.

    Returns a short name for the database in use, for the test output.
    """
    if is_postgres():
        wipe_postgres()
        app.config["DATABASE_URL"] = database_url()
        app.config["DATABASE_PATH"] = sqlite_path
        return "PostgreSQL"

    app.config["DATABASE_URL"] = ""
    app.config["DATABASE_PATH"] = sqlite_path
    return "SQLite"
