import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH
from app.spine.migrations import apply_migrations

# How long a connection waits on a lock held by another writer before
# raising "database is locked". Chat turns, the improvement auditor, brain
# ingest and FastAPI's threadpool all write concurrently.
BUSY_TIMEOUT_MS = 10_000


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_MS / 1000)
    c.row_factory = sqlite3.Row
    # WAL lets readers and a writer proceed concurrently (the default
    # rollback journal blocks all readers during a write and can return
    # SQLITE_BUSY without waiting on a read→write lock upgrade). The mode
    # persists in the file; re-issuing it per connection is a cheap no-op.
    c.execute("PRAGMA journal_mode=WAL")
    c.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    """Open `spine.db` and apply any pending migrations.

    Idempotent — re-running on an already-current DB is a no-op. Safe
    against pre-migrations databases (every v1 statement uses
    `IF NOT EXISTS`).
    """
    with conn() as c:
        apply_migrations(c)
