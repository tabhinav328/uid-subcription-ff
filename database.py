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


def _adapt_sql(query: str) -> str:
    if USE_POSTGRES:
        return query.replace("?", "%s")
    return query


@contextmanager
def get_db():
    if USE_POSTGRES:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(
            _normalize_database_url(DATABASE_URL),
            cursor_factory=RealDictCursor,
        )
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


def init_db():
    upsert_conflict = "EXCLUDED" if USE_POSTGRES else "excluded"
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