"""ClickHouse client factory + schema bootstrap.

Same code path for a local clickhouse-server and ClickHouse Cloud — only the
connection settings (host/port/secure/password) differ. See app/config.py.
"""

from __future__ import annotations

import pathlib

import clickhouse_connect
from clickhouse_connect.driver import Client

from .config import settings

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


def get_client(database: str | None = None) -> Client:
    """Client bound to the seconds database (or `database` override)."""
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        secure=settings.clickhouse_secure,
        database=settings.clickhouse_database if database is None else database,
        connect_timeout=15,
        query_limit=0,  # no implicit row cap (needed for full exports)
    )


def _split_statements(sql: str) -> list[str]:
    # Strip line comments (-- ... EOL) BEFORE splitting on ';' so comment text
    # (which may itself contain ';' or apostrophes) can't corrupt a statement.
    no_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [stmt.strip() for stmt in no_comments.split(";") if stmt.strip()]


def bootstrap() -> None:
    """Create the database + tables if they don't exist. Idempotent."""
    # Connect WITHOUT a database first — `seconds` may not exist yet.
    admin = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        secure=settings.clickhouse_secure,
        connect_timeout=15,
    )
    try:
        for stmt in _split_statements(SCHEMA_PATH.read_text()):
            admin.command(stmt)
    finally:
        admin.close()
