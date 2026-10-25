"""Persistence layer.

The shop keeps its data in one of two databases:

* **SQLite** (the default) - a single file at ``instance/store.db``. Nothing to
  install, perfect for local work and for the test suites.
* **PostgreSQL** - set ``DATABASE_URL`` in ``.env`` and the same app talks to
  a PostgreSQL server instead. This is the one to use for a real shop: real
  transactions, row locking so two people cannot buy the last tee, real
  constraints, and backups you can point a replica at.

Both speak the same small SQL dialect (``?`` placeholders, ``cursor.lastrowid``
after an insert) so every other module in the app is database agnostic. The
translation to PostgreSQL lives in :mod:`db_postgres`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional, Sequence, Set

import click
from flask import current_app, g

import db_postgres

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    slug               TEXT    NOT NULL UNIQUE,
    name               TEXT    NOT NULL,
    description        TEXT    NOT NULL DEFAULT '',
    category           TEXT    NOT NULL DEFAULT 'cricket',
    price_cents        INTEGER NOT NULL DEFAULT 0,
    compare_at_cents   INTEGER NOT NULL DEFAULT 0,
    status             TEXT    NOT NULL DEFAULT 'draft',
    stock_mode         TEXT    NOT NULL DEFAULT 'made_to_order',
    is_featured        INTEGER NOT NULL DEFAULT 0,
    needs_review       INTEGER NOT NULL DEFAULT 0,
    review_note        TEXT    NOT NULL DEFAULT '',
    supplier_sku       TEXT    NOT NULL DEFAULT '',
    meta_title         TEXT    NOT NULL DEFAULT '',
    meta_description   TEXT    NOT NULL DEFAULT '',
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS product_images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    filename    TEXT    NOT NULL,
    alt_text    TEXT    NOT NULL DEFAULT '',
    position    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS product_variants (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    size        TEXT    NOT NULL,
    stock       INTEGER NOT NULL DEFAULT 0,
    sku         TEXT    NOT NULL DEFAULT '',
    price_cents INTEGER NOT NULL DEFAULT 0,
    position    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (product_id, size)
);

CREATE TABLE IF NOT EXISTS cart_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cart_token  TEXT    NOT NULL,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    variant_id  INTEGER NOT NULL REFERENCES product_variants(id) ON DELETE CASCADE,
    quantity    INTEGER NOT NULL DEFAULT 1,
    custom_front TEXT   NOT NULL DEFAULT '',
    custom_back  TEXT   NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cart_token ON cart_items(cart_token);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number    TEXT    NOT NULL UNIQUE,
    customer_name   TEXT    NOT NULL,
    phone           TEXT    NOT NULL,
    email           TEXT    NOT NULL,
    address_line1   TEXT    NOT NULL,
    address_line2   TEXT    NOT NULL DEFAULT '',
    city            TEXT    NOT NULL,
    state           TEXT    NOT NULL,
    pincode         TEXT    NOT NULL,
    subtotal_cents  INTEGER NOT NULL,
    shipping_cents  INTEGER NOT NULL,
    total_cents     INTEGER NOT NULL,
    payment_status  TEXT    NOT NULL DEFAULT 'pending',
    payment_method  TEXT    NOT NULL DEFAULT '',
    payment_ref     TEXT    NOT NULL DEFAULT '',
    razorpay_order_id TEXT  NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'new',
    tracking_number TEXT    NOT NULL DEFAULT '',
    carrier         TEXT    NOT NULL DEFAULT '',
    supplier_ref    TEXT    NOT NULL DEFAULT '',
    notes           TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id         INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id       INTEGER REFERENCES products(id) ON DELETE SET NULL,
    variant_id       INTEGER REFERENCES product_variants(id) ON DELETE SET NULL,
    product_name     TEXT    NOT NULL,
    size             TEXT    NOT NULL,
    sku              TEXT    NOT NULL DEFAULT '',
    image_filename   TEXT    NOT NULL DEFAULT '',
    custom_front     TEXT    NOT NULL DEFAULT '',
    custom_back      TEXT    NOT NULL DEFAULT '',
    unit_price_cents INTEGER NOT NULL,
    quantity         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS order_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    status      TEXT    NOT NULL,
    message     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id);

CREATE TABLE IF NOT EXISTS coupons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL UNIQUE,
    kind            TEXT    NOT NULL DEFAULT 'percent',
    value           INTEGER NOT NULL DEFAULT 0,
    min_order_cents INTEGER NOT NULL DEFAULT 0,
    max_uses        INTEGER NOT NULL DEFAULT 0,
    used_count      INTEGER NOT NULL DEFAULT 0,
    expires_at      TEXT    NOT NULL DEFAULT '',
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS coupon_redemptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    coupon_id   INTEGER NOT NULL REFERENCES coupons(id) ON DELETE CASCADE,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    order_id    INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    name        TEXT    NOT NULL,
    rating      INTEGER NOT NULL DEFAULT 5,
    title       TEXT    NOT NULL DEFAULT '',
    body        TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'pending',
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id);

CREATE TABLE IF NOT EXISTS wishlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cart_token  TEXT    NOT NULL,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    created_at  TEXT    NOT NULL,
    UNIQUE (cart_token, product_id)
);

CREATE TABLE IF NOT EXISTS otp_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phone       TEXT    NOT NULL,
    code_hash   TEXT    NOT NULL,
    purpose     TEXT    NOT NULL DEFAULT 'checkout',
    attempts    INTEGER NOT NULL DEFAULT 0,
    expires_at  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_otp_phone ON otp_codes(phone);

CREATE TABLE IF NOT EXISTS returns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    reason      TEXT    NOT NULL DEFAULT '',
    details     TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'requested',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
"""

#: The same schema for PostgreSQL: identities instead of AUTOINCREMENT, no
#: PRAGMA, and timestamps stay TEXT so the ISO strings keep working unchanged.
POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id                 INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    slug               TEXT    NOT NULL UNIQUE,
    name               TEXT    NOT NULL,
    description        TEXT    NOT NULL DEFAULT '',
    category           TEXT    NOT NULL DEFAULT 'cricket',
    price_cents        INTEGER NOT NULL DEFAULT 0,
    compare_at_cents   INTEGER NOT NULL DEFAULT 0,
    status             TEXT    NOT NULL DEFAULT 'draft',
    stock_mode         TEXT    NOT NULL DEFAULT 'made_to_order',
    is_featured        INTEGER NOT NULL DEFAULT 0,
    needs_review       INTEGER NOT NULL DEFAULT 0,
    review_note        TEXT    NOT NULL DEFAULT '',
    supplier_sku       TEXT    NOT NULL DEFAULT '',
    meta_title         TEXT    NOT NULL DEFAULT '',
    meta_description   TEXT    NOT NULL DEFAULT '',
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS product_images (
    id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    filename    TEXT    NOT NULL,
    alt_text    TEXT    NOT NULL DEFAULT '',
    position    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS product_variants (
    id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    size        TEXT    NOT NULL,
    stock       INTEGER NOT NULL DEFAULT 0,
    sku         TEXT    NOT NULL DEFAULT '',
    price_cents INTEGER NOT NULL DEFAULT 0,
    position    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (product_id, size)
);

CREATE TABLE IF NOT EXISTS cart_items (
    id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    cart_token  TEXT    NOT NULL,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    variant_id  INTEGER NOT NULL REFERENCES product_variants(id) ON DELETE CASCADE,
    quantity    INTEGER NOT NULL DEFAULT 1,
    custom_front TEXT   NOT NULL DEFAULT '',
    custom_back  TEXT   NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cart_token ON cart_items(cart_token);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    order_number    TEXT    NOT NULL UNIQUE,
    customer_name   TEXT    NOT NULL,
    phone           TEXT    NOT NULL,
    email           TEXT    NOT NULL,
    address_line1   TEXT    NOT NULL,
    address_line2   TEXT    NOT NULL DEFAULT '',
    city            TEXT    NOT NULL,
    state           TEXT    NOT NULL,
    pincode         TEXT    NOT NULL,
    subtotal_cents  INTEGER NOT NULL,
    shipping_cents  INTEGER NOT NULL,
    total_cents     INTEGER NOT NULL,
    payment_status  TEXT    NOT NULL DEFAULT 'pending',
    payment_method  TEXT    NOT NULL DEFAULT '',
    payment_ref     TEXT    NOT NULL DEFAULT '',
    razorpay_order_id TEXT  NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'new',
    tracking_number TEXT    NOT NULL DEFAULT '',
    carrier         TEXT    NOT NULL DEFAULT '',
    supplier_ref    TEXT    NOT NULL DEFAULT '',
    notes           TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id               INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    order_id         INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id       INTEGER REFERENCES products(id) ON DELETE SET NULL,
    variant_id       INTEGER REFERENCES product_variants(id) ON DELETE SET NULL,
    product_name     TEXT    NOT NULL,
    size             TEXT    NOT NULL,
    sku              TEXT    NOT NULL DEFAULT '',
    image_filename   TEXT    NOT NULL DEFAULT '',
    custom_front     TEXT    NOT NULL DEFAULT '',
    custom_back      TEXT    NOT NULL DEFAULT '',
    unit_price_cents INTEGER NOT NULL,
    quantity         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS order_events (
    id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    status      TEXT    NOT NULL,
    message     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id);

CREATE TABLE IF NOT EXISTS coupons (
    id              INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    code            TEXT    NOT NULL UNIQUE,
    kind            TEXT    NOT NULL DEFAULT 'percent',
    value           INTEGER NOT NULL DEFAULT 0,
    min_order_cents INTEGER NOT NULL DEFAULT 0,
    max_uses        INTEGER NOT NULL DEFAULT 0,
    used_count      INTEGER NOT NULL DEFAULT 0,
    expires_at      TEXT    NOT NULL DEFAULT '',
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS coupon_redemptions (
    id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    coupon_id   INTEGER NOT NULL REFERENCES coupons(id) ON DELETE CASCADE,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    order_id    INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    name        TEXT    NOT NULL,
    rating      INTEGER NOT NULL DEFAULT 5,
    title       TEXT    NOT NULL DEFAULT '',
    body        TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'pending',
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id);

CREATE TABLE IF NOT EXISTS wishlist (
    id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    cart_token  TEXT    NOT NULL,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    created_at  TEXT    NOT NULL,
    UNIQUE (cart_token, product_id)
);

CREATE TABLE IF NOT EXISTS otp_codes (
    id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    phone       TEXT    NOT NULL,
    code_hash   TEXT    NOT NULL,
    purpose     TEXT    NOT NULL DEFAULT 'checkout',
    attempts    INTEGER NOT NULL DEFAULT 0,
    expires_at  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_otp_phone ON otp_codes(phone);

CREATE TABLE IF NOT EXISTS returns (
    id          INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    reason      TEXT    NOT NULL DEFAULT '',
    details     TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'requested',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
"""

#: Columns that older database files may be missing.
MIGRATIONS = (
    ("orders", "razorpay_order_id", "TEXT NOT NULL DEFAULT ''"),
    ("orders", "coupon_code", "TEXT NOT NULL DEFAULT ''"),
    ("orders", "discount_cents", "INTEGER NOT NULL DEFAULT 0"),
    ("orders", "cod_fee_cents", "INTEGER NOT NULL DEFAULT 0"),
    ("orders", "cart_token", "TEXT NOT NULL DEFAULT ''"),
    ("orders", "otp_verified", "INTEGER NOT NULL DEFAULT 0"),
    ("orders", "delivery_estimate", "TEXT NOT NULL DEFAULT ''"),
    ("product_variants", "price_cents", "INTEGER NOT NULL DEFAULT 0"),
    ("cart_items", "custom_front", "TEXT NOT NULL DEFAULT ''"),
    ("cart_items", "custom_back", "TEXT NOT NULL DEFAULT ''"),
    ("order_items", "custom_front", "TEXT NOT NULL DEFAULT ''"),
    ("order_items", "custom_back", "TEXT NOT NULL DEFAULT ''"),
)


def utcnow() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class SqliteBackend:
    """The default backend: one file, no server, no extra dependency."""

    name = "sqlite"

    def __init__(self, path: str) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, detect_types=sqlite3.PARSE_DECLTYPES)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def close(self, connection: sqlite3.Connection) -> None:
        connection.close()

    def query_all(self, connection, sql: str, params: Sequence[Any] = ()) -> List[Any]:
        return connection.execute(sql, params).fetchall()

    def query_one(self, connection, sql: str, params: Sequence[Any] = ()):
        return connection.execute(sql, params).fetchone()

    def execute(self, connection, sql: str, params: Sequence[Any] = ()):
        cursor = connection.execute(sql, params)
        connection.commit()
        return cursor

    def execute_many(self, connection, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        connection.executemany(sql, seq)
        connection.commit()

    def init_schema(self, connection, schema_sql: str) -> None:
        connection.executescript(schema_sql)
        self.migrate(connection)
        connection.commit()

    def migrate(self, connection) -> None:
        for table, column, definition in MIGRATIONS:
            columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def table_columns(self, connection, table: str) -> Set[str]:
        return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def get_backend():
    """The backend this app is configured to use."""
    url = (current_app.config.get("DATABASE_URL") or "").strip()
    path = current_app.config.get("DATABASE_PATH") or ""
    key = f"{url}|{path}"

    backend = current_app.extensions.get("db_backend")
    if backend is None or current_app.extensions.get("db_backend_key") != key:
        if url.startswith(("postgres://", "postgresql://")):
            backend = db_postgres.PostgresBackend(
                url, int(current_app.config.get("DATABASE_POOL_SIZE") or 0)
            )
        else:
            backend = SqliteBackend(path)
        current_app.extensions["db_backend"] = backend
        current_app.extensions["db_backend_key"] = key
    return backend


def database_label() -> str:
    """A short description of the database in use, for the admin and the CLI."""
    backend = get_backend()
    if backend.name == "postgres":
        return "PostgreSQL"
    return "SQLite"


def get_db():
    """Return the request-scoped database connection."""
    if "db" not in g:
        g.db = get_backend().connect()
    return g.db


def close_db(_exception=None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        get_backend().close(connection)


def init_db() -> None:
    """Create every table if it does not exist yet."""
    backend = get_backend()
    connection = get_db()
    schema = POSTGRES_SCHEMA if backend.name == "postgres" else SCHEMA
    backend.init_schema(connection, schema)
    if backend.name == "postgres":
        migrate_postgres(connection)


def migrate_postgres(connection) -> None:
    """Add columns that an older PostgreSQL database is missing."""
    backend = get_backend()
    for table, column, definition in MIGRATIONS:
        if column not in backend.table_columns(connection, table):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    connection.commit()


def query_all(sql: str, params: Sequence[Any] = ()) -> list:
    return get_backend().query_all(get_db(), sql, params)


def query_one(sql: str, params: Sequence[Any] = ()):
    return get_backend().query_one(get_db(), sql, params)


def execute(sql: str, params: Sequence[Any] = ()):
    return get_backend().execute(get_db(), sql, params)


def execute_many(sql: str, seq: Iterable[Sequence[Any]]) -> None:
    get_backend().execute_many(get_db(), sql, seq)


@click.command("init-db")
def init_db_command():
    """Create the database tables."""
    init_db()
    click.echo(f"Database ready ({database_label()})")


def register(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
