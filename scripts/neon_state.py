import os
import sqlite3
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
        remove_sqlite_sidecars(db_path)
        return True


def persist_sqlite(db_path):
    if not enabled():
        return False

    db_path = Path(db_path)
    if not db_path.exists():
        return False

    checkpoint_sqlite(db_path)
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


def checkpoint_sqlite(db_path):
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def remove_sqlite_sidecars(db_path):
    for suffix in ("-wal", "-shm"):
        db_path.with_name(f"{db_path.name}{suffix}").unlink(missing_ok=True)


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
