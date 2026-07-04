import os
import sqlite3
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_PATH = os.environ.get("DATABASE_PATH", "subscriptions.db")

USE_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def _postgres_connect_url() -> str:
    url = _normalize_database_url(DATABASE_URL)
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url


def _adapt_sql(query: str) -> str:
    if USE_POSTGRES:
        return query.replace("?", "%s")
    return query


def _connect_postgres():
    import psycopg2

    url = _postgres_connect_url()
    try:
        return psycopg2.connect(url)
    except Exception:
        # Some Render internal URLs work without forced SSL.
        fallback = url.replace("sslmode=require", "sslmode=prefer")
        if fallback != url:
            return psycopg2.connect(fallback)
        raise


@contextmanager
def get_db():
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
        from psycopg2.extras import RealDictCursor

        cur = conn.cursor(cursor_factory=RealDictCursor)
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


def init_db():
    with get_db() as conn:
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


def storage_backend() -> str:
    return "postgresql" if USE_POSTGRES else "sqlite"


def storage_warning() -> str | None:
    if USE_POSTGRES:
        return None
    return (
        "Using local SQLite file. On Render free tier this data is wiped when "
        "the service sleeps. Set DATABASE_URL to a PostgreSQL database."
    )