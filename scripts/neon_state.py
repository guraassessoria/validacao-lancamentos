import os
import threading
from pathlib import Path

import psycopg


STATE_KEY = "ct2_sqlite"
_LOCK = threading.Lock()


def enabled():
    return bool(os.getenv("DATABASE_URL"))


def hydrate_sqlite(db_path):
    if not enabled():
        return False

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with _LOCK, psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        ensure_schema(conn)
        row = conn.execute("SELECT payload FROM app_state WHERE key = %s", (STATE_KEY,)).fetchone()
        if not row:
            return False
        db_path.write_bytes(row[0])
        return True


def persist_sqlite(db_path):
    if not enabled():
        return False

    db_path = Path(db_path)
    if not db_path.exists():
        return False

    payload = db_path.read_bytes()
    with _LOCK, psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO app_state (key, payload, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (key)
            DO UPDATE SET payload = EXCLUDED.payload, updated_at = now()
            """,
            (STATE_KEY, payload),
        )
        conn.commit()
        return True


def ensure_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_state (
          key TEXT PRIMARY KEY,
          payload BYTEA NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()
