import os
import sqlite3
import ssl
from contextlib import contextmanager
from urllib.parse import unquote, urlparse

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_PATH = os.environ.get("DATABASE_PATH", "subscriptions.db")

USE_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

_schema_ready = False


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def _parse_database_url(url: str) -> dict:
    parsed = urlparse(_normalize_database_url(url))
    database = (parsed.path or "/").lstrip("/")
    if "?" in database:
        database = database.split("?", 1)[0]

    return {
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": database,
    }


def _adapt_sql(query: str) -> str:
    if USE_POSTGRES:
        return query.replace("?", "%s")
    return query


def _connect_postgres():
    import pg8000.dbapi

    cfg = _parse_database_url(DATABASE_URL)
    base_kwargs = {
        "user": cfg["user"],
        "password": cfg["password"],
        "host": cfg["host"],
        "port": cfg["port"],
        "database": cfg["database"],
    }

    try:
        return pg8000.dbapi.connect(
            **base_kwargs,
            ssl_context=ssl.create_default_context(),
        )
    except Exception:
        return pg8000.dbapi.connect(**base_kwargs)


@contextmanager
def get_db():
    init_db()
    if USE_POSTGRES:
        conn = _connect_postgres()
        try:
            yield conn
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def db_execute(conn, query: str, params=()):
    sql = _adapt_sql(query)
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur
    return conn.execute(sql, params)


def row_get(row, key: str, index: int = 0):
    if row is None:
        return None
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _ensure_schema(conn):
    db_execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            uid TEXT PRIMARY KEY,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            note TEXT DEFAULT ''
        )
        """,
    )
    conn.commit()


def init_db():
    global _schema_ready
    if _schema_ready:
        return

    if USE_POSTGRES:
        conn = _connect_postgres()
        try:
            _ensure_schema(conn)
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            _ensure_schema(conn)
        finally:
            conn.close()

    _schema_ready = True


def storage_backend() -> str:
    return "postgresql" if USE_POSTGRES else "sqlite"


def storage_warning() -> str | None:
    if USE_POSTGRES:
        return None
    return (
        "Using local SQLite file. On Render free tier this data is wiped when "
        "the service sleeps. Set DATABASE_URL to a PostgreSQL database."
    )