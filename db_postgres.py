"""PostgreSQL backend for the persistence layer.

The app speaks one small dialect of SQL: ``?`` placeholders, plain
``INSERT`` / ``UPDATE`` / ``SELECT`` statements and ``cursor.lastrowid`` after
an insert. This module teaches that dialect to psycopg (the PostgreSQL driver)
so the rest of the shop does not have to care which database it is talking to.

What is translated:

* ``?`` placeholders become ``%s`` (and a literal ``%`` becomes ``%%`` so
  psycopg does not read it as a placeholder).
* ``cursor.lastrowid`` becomes ``SELECT lastval()``.
* Rows come back as plain dictionaries, so ``row["name"]`` and ``dict(row)``
  work exactly like they do on SQLite.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

#: ``?`` outside a string literal becomes ``%s``.
_PLACEHOLDER = re.compile(r"\?")

#: Tables in the order they must be filled when copying data across.
TABLE_ORDER = (
    "products",
    "product_images",
    "product_variants",
    "cart_items",
    "orders",
    "order_items",
    "order_events",
)


class PostgresCursor:
    """A tiny cursor wrapper that also answers ``lastrowid``."""

    def __init__(self, cursor, connection) -> None:
        self._cursor = cursor
        self._connection = connection
        self.lastrowid: Optional[int] = None

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchall(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self._cursor.fetchall()]

    def fetchone(self) -> Optional[Dict[str, Any]]:
        row = self._cursor.fetchone()
        return dict(row) if row is not None else None

    def _last_insert_id(self) -> int:
        cursor = self._connection.execute("SELECT lastval() AS id")
        row = cursor.fetchone()
        cursor.close()
        return int(row["id"])
    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


def to_postgres_sql(sql: str) -> str:
    """Rewrite the shop's SQL for psycopg.

    ``?`` becomes ``%s`` and every other ``%`` is doubled so that patterns
    like ``LIKE '%tee%'`` survive the trip.
    """
    if "%" not in sql:
        return sql.replace("?", "%s")

    out: List[str] = []
    for index, char in enumerate(sql):
        if char == "?":
            out.append("%s")
        elif char == "%":
            # %s is a placeholder only when we wrote it ourselves; the app
            # never uses psycopg placeholders directly, so every % is literal.
            out.append("%%")
        else:
            out.append(char)
    return "".join(out)


def to_postgres_params(params: Sequence[Any]) -> tuple:
    """Adapt the app's values for PostgreSQL.

    The shop stores its flags (is_featured, needs_review) as 0/1 integers, so
    a Python True/False has to become 1/0 - PostgreSQL will not put a boolean
    into an integer column.
    """
    return tuple(int(value) if isinstance(value, bool) else value for value in params)


class PostgresBackend:
    """Everything the persistence layer needs from a PostgreSQL server."""

    name = "postgres"

    def __init__(self, url: str, pool_size: int = 0) -> None:
        self.url = url
        self.pool_size = pool_size
        self._pool = None

    # -- connection ---------------------------------------------------------
    def connect(self):
        import psycopg
        from psycopg.rows import dict_row

        if self._pool is None and self.pool_size:
            from psycopg_pool import ConnectionPool

            self._pool = ConnectionPool(
                self.url,
                min_size=1,
                max_size=self.pool_size,
                kwargs={"row_factory": dict_row},
                open=True,
            )

        if self._pool is not None:
            # The pool hands out a connection through a context manager; it
            # goes back to the pool when that context is closed.
            context = self._pool.connection()
            connection = context.__enter__()
            connection._shopify_pool_context = context
        else:
            connection = psycopg.connect(self.url, row_factory=dict_row)
            connection.autocommit = False

        # Always talk to the public schema, whatever the role's default is.
        try:
            connection.execute("SET search_path TO public")
        except Exception:
            pass
        return connection

    def close(self, connection) -> None:
        context = getattr(connection, "_shopify_pool_context", None)
        if context is not None:
            try:
                context.__exit__(None, None, None)
            except Exception:
                pass
            return
        try:
            connection.rollback()
        except Exception:
            pass
        connection.close()

    # -- queries ------------------------------------------------------------
    def query_all(self, connection, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        cursor = connection.execute(to_postgres_sql(sql), to_postgres_params(params))
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def query_one(
        self, connection, sql: str, params: Sequence[Any] = ()
    ) -> Optional[Dict[str, Any]]:
        cursor = connection.execute(to_postgres_sql(sql), to_postgres_params(params))
        row = cursor.fetchone()
        cursor.close()
        return dict(row) if row is not None else None

    def execute(self, connection, sql: str, params: Sequence[Any] = ()) -> PostgresCursor:
        statement = to_postgres_sql(sql)
        wrapper = PostgresCursor(None, connection)

        if sql.lstrip().upper().startswith("INSERT") and "returning" not in sql.lower():
            # Ask PostgreSQL for the new id instead of guessing it.
            cursor = connection.execute(statement + " RETURNING id", to_postgres_params(params))
            row = cursor.fetchone()
            connection.commit()
            wrapper.lastrowid = int(row["id"]) if row else None
            wrapper._cursor = cursor
            return wrapper

        cursor = connection.execute(statement, to_postgres_params(params))
        connection.commit()
        wrapper._cursor = cursor
        return wrapper

    def execute_many(self, connection, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        rows = [to_postgres_params(params) for params in seq]
        if not rows:
            return
        with connection.cursor() as cursor:
            cursor.executemany(to_postgres_sql(sql), rows)
        connection.commit()

    # -- schema -------------------------------------------------------------
    def init_schema(self, connection, schema_sql: str) -> None:
        for statement in _split_statements(schema_sql):
            connection.execute(to_postgres_sql(statement))
        connection.commit()

    def table_columns(self, connection, table: str) -> set:
        cursor = connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
        columns = {row["column_name"] for row in cursor.fetchall()}
        cursor.close()
        return columns


def _split_statements(sql: str) -> List[str]:
    """Split a schema script into single statements."""
    statements = []
    for chunk in sql.split(";"):
        cleaned = "\n".join(
            line for line in chunk.splitlines() if not line.strip().startswith("--")
        ).strip()
        if cleaned:
            statements.append(cleaned)
    return statements
