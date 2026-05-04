"""Durable background-job tracking.

Replaces the per-route in-memory job dicts (the original `_DSPY_JOBS`
in `app/routes/dspy.py`) with a single SQLite-backed table. Every job
carries:

  * a stable `id` (uuid hex tail)
  * a `kind` discriminator ('dspy_compile' today; reserved for future
    'brain_reindex' / 'auditor_run' unification)
  * a free-form `metadata` dict (kind-specific input — agent slug,
    auto-promote flag, …)
  * a `summary` dict written when the job lands successfully
  * `error` text on failure

Status transitions are one-way: `running` → (`ok` | `error` |
`cancelled`). Re-using a job id on insert is rejected by the PRIMARY
KEY constraint.

The helpers do NOT spawn threads — that's the caller's responsibility
(it knows whether to use `threading.Thread`, `asyncio.create_task`, or
a worker pool). All this module guarantees is that the job row is
visible in SQLite, survives backend restart, and is queryable by id /
kind / status.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.spine.db import conn

VALID_STATUSES = {"running", "ok", "error", "cancelled"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def create_job(
    job_id: str,
    kind: str,
    title: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert a `running` row. Caller's responsibility to use a unique id."""
    started_at = _now_iso()
    payload = json.dumps(metadata or {})
    with conn() as c:
        c.execute(
            "INSERT INTO background_jobs (id, kind, title, status, started_at, metadata_json) "
            "VALUES (?, ?, ?, 'running', ?, ?)",
            (job_id, kind, title, started_at, payload),
        )
    return _read(job_id)  # type: ignore[return-value]


def complete_job(
    job_id: str,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    """Mark a job terminal. Pass `error` to flip to 'error'; otherwise 'ok'.

    No-op if the job is already terminal (e.g. cancelled while the
    worker was still running). The one-way transition contract
    (`running` → `ok` / `error` / `cancelled`) is enforced via the
    `status = 'running'` guard so a late-finishing worker can't
    overwrite a `cancelled` row.
    """
    status = "error" if error else "ok"
    summary_json = json.dumps(summary or {})
    with conn() as c:
        c.execute(
            "UPDATE background_jobs SET status = ?, ended_at = ?, "
            "summary_json = ?, error = ? WHERE id = ? AND status = 'running'",
            (status, _now_iso(), summary_json, error, job_id),
        )
    return _read(job_id)


def cancel_job(job_id: str, reason: str | None = None) -> dict[str, Any] | None:
    """Flip a running job to 'cancelled'. No-op for already-terminal jobs."""
    with conn() as c:
        c.execute(
            "UPDATE background_jobs SET status = 'cancelled', ended_at = ?, "
            "error = ? WHERE id = ? AND status = 'running'",
            (_now_iso(), reason, job_id),
        )
    return _read(job_id)


def get_job(job_id: str) -> dict[str, Any] | None:
    return _read(job_id)


def list_jobs(
    kind: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, kind, title, status, started_at, ended_at, "
        "metadata_json, summary_json, error FROM background_jobs"
    )
    args: tuple[Any, ...] = ()
    if kind:
        sql += " WHERE kind = ?"
        args = (kind,)
    sql += " ORDER BY started_at DESC LIMIT ?"
    args = (*args, limit)
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [_row_to_dict(r) for r in rows]


def running_count(kind: str) -> int:
    """How many jobs of `kind` are still running. Caller can use this to
    impose a concurrency cap before spawning a new worker."""
    with conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM background_jobs WHERE kind = ? AND status = 'running'",
            (kind,),
        ).fetchone()
    return int(row["n"])


def _read(job_id: str) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, kind, title, status, started_at, ended_at, "
            "metadata_json, summary_json, error FROM background_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "status": row["status"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "metadata": _safe_json(row["metadata_json"]),
        "summary": _safe_json(row["summary_json"]),
        "error": row["error"],
    }


def _safe_json(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
