"""Copy the shop from SQLite into PostgreSQL.

Run it once when you are ready to move a live store to a real database:

    python migrate_to_postgres.py

It creates the tables in PostgreSQL (if they are not there yet) and copies
every row across, keeping the ids so all the links between products, sizes,
orders and order items still point at the right things. After it finishes the
app uses PostgreSQL as soon as ``DATABASE_URL`` is set in ``.env``.

Nothing is deleted: the SQLite file stays exactly where it is, so you can go
back to it if you change your mind. Take a copy of it before you start.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

import db
import db_postgres

TABLES = db_postgres.TABLE_ORDER


def postgres_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit(
            "Set DATABASE_URL first, for example:\n"
            "  set DATABASE_URL=postgresql://shopify:shopify_dev_password@localhost:5432/shopify"
        )
    if not url.startswith(("postgres://", "postgresql://")):
        raise SystemExit(f"DATABASE_URL does not look like a PostgreSQL url: {url}")
    return url


def sqlite_path() -> Path:
    path = Path(os.environ.get("SQLITE_PATH", "instance/store.db")).resolve()
    if not path.is_file():
        raise SystemExit(f"No SQLite database at {path}")
    return path


def copy_tables(source: sqlite3.Connection, target) -> None:
    for table in TABLES:
        source.row_factory = sqlite3.Row
        cursor = source.execute(f"SELECT * FROM {table}")
        rows = cursor.fetchall()
        if not rows:
            print(f"  {table}: nothing to copy")
            continue

        columns = [description[0] for description in cursor.description]
        placeholders = ", ".join(["%s"] * len(columns))
        column_list = ", ".join(columns)
        sql = f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})"

        values = [tuple(row[column] for column in columns) for row in rows]
        with target.cursor() as cursor:
            cursor.executemany(sql, values)
        print(f"  {table}: copied {len(values)} rows")

    target.commit()


def fix_sequences(target) -> None:
    """Make the id counters continue after the ids that were copied."""
    for table in TABLES:
        target.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
        )
    target.commit()


def main() -> int:
    url = postgres_url()
    source_path = sqlite_path()

    print(f"Reading  : {source_path}")
    print(f"Writing  : {urlparse(url).hostname}/{urlparse(url).path.lstrip('/')}")

    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row

    import psycopg

    with psycopg.connect(url) as target:
        target.execute("SET search_path TO public")
        print("\nCreating the tables in PostgreSQL")
        db_postgres.PostgresBackend(url).init_schema(target, db.POSTGRES_SCHEMA)

        print("\nCopying the data")
        copy_tables(source, target)

        print("\nFixing the id counters")
        fix_sequences(target)

        target.execute(
            "SELECT table_name, (xpath('/row/c/text()', "
            "query_to_xml(format('SELECT count(*) AS c FROM %I', table_name), "
            "false, true, '')))[1]::text::int AS rows "
            "FROM information_schema.tables WHERE table_schema = 'public' "
            "AND table_type = 'BASE TABLE'"
        )
        counts = target.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        ).fetchall()
        for (table_name,) in counts:
            row_count = target.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0]
            print(f"  {table_name}: {row_count} rows")

    print("\nDone. Set DATABASE_URL in .env and restart the app.")
    print("The SQLite file is untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
