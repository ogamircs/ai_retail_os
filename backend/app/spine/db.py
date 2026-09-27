import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH
from app.spine.migrations import apply_migrations


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
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
