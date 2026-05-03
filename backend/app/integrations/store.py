from __future__ import annotations

import hashlib
import json
from typing import Any

from app.integrations.base import utc_now
from app.spine.db import conn


def _json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, default=str)


def _hash(data: Any) -> str:
    return hashlib.sha256(_json(data).encode("utf-8")).hexdigest()


def _loads(value: str | None, default):
    if not value:
        return default
    return json.loads(value)


def upsert_system(row: dict[str, Any], status: str | None = None, error: str | None = None) -> dict[str, Any]:
    status = status or ("connected" if row["configured"] else "mock")
    metadata = row.get("metadata", {})
    with conn() as c:
        c.execute(
            "INSERT INTO integration_systems "
            "(system_id, display_name, domain, enabled, configured, mode, last_status, "
            "last_sync_ts, last_error, docs_url, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, "
            "COALESCE((SELECT last_sync_ts FROM integration_systems WHERE system_id = ?), NULL), "
            "?, ?, ?) "
            "ON CONFLICT(system_id) DO UPDATE SET "
            "display_name = excluded.display_name, domain = excluded.domain, "
            "enabled = excluded.enabled, configured = excluded.configured, mode = excluded.mode, "
            "last_status = excluded.last_status, last_error = excluded.last_error, "
            "docs_url = excluded.docs_url, metadata_json = excluded.metadata_json",
            (
                row["system_id"],
                row["display_name"],
                row["domain"],
                1 if row.get("enabled", True) else 0,
                1 if row.get("configured", False) else 0,
                row.get("mode", "mock"),
                status,
                row["system_id"],
                error,
                row.get("docs_url", ""),
                _json(metadata),
            ),
        )
    return get_system(row["system_id"]) or {}


def list_systems() -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute(
            "SELECT s.*, "
            "COALESCE(p.pending_actions, 0) AS pending_actions, "
            "COALESCE(p.applied_actions, 0) AS applied_actions "
            "FROM integration_systems s "
            "LEFT JOIN ("
            "  SELECT system_id, "
            "  SUM(CASE WHEN status IN ('mock_only', 'approval_required') THEN 1 ELSE 0 END) AS pending_actions, "
            "  SUM(CASE WHEN status IN ('applied', 'applied_mock', 'draft_created') THEN 1 ELSE 0 END) AS applied_actions "
            "  FROM outbox_actions GROUP BY system_id"
            ") p ON p.system_id = s.system_id "
            "ORDER BY s.domain, s.display_name"
        ).fetchall()
    return [_system_dict(r) for r in rows]


def get_system(system_id: str) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute("SELECT * FROM integration_systems WHERE system_id = ?", (system_id,)).fetchone()
    return _system_dict(row) if row else None


def _system_dict(row) -> dict[str, Any]:
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["configured"] = bool(item["configured"])
    item["metadata"] = _loads(item.pop("metadata_json"), {})
    item["pending_actions"] = int(item.get("pending_actions") or 0)
    item["applied_actions"] = int(item.get("applied_actions") or 0)
    return item


def start_sync_run(system_id: str) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO sync_runs "
            "(system_id, started_at, status, records_read, records_written, summary_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (system_id, utc_now(), "running", 0, 0, "{}"),
        )
        return cur.lastrowid


def finish_sync_run(
    run_id: int,
    system_id: str,
    status: str,
    records_read: int,
    records_written: int,
    summary: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    finished_at = utc_now()
    with conn() as c:
        c.execute(
            "UPDATE sync_runs SET finished_at = ?, status = ?, records_read = ?, "
            "records_written = ?, error = ?, summary_json = ? WHERE id = ?",
            (finished_at, status, records_read, records_written, error, _json(summary), run_id),
        )
        c.execute(
            "UPDATE integration_systems SET last_status = ?, last_sync_ts = ?, last_error = ? "
            "WHERE system_id = ?",
            (status, finished_at, error, system_id),
        )
    return get_sync_run(run_id) or {}


def get_sync_run(run_id: int) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute("SELECT * FROM sync_runs WHERE id = ?", (run_id,)).fetchone()
    return _sync_run_dict(row) if row else None


def list_sync_runs(limit: int = 30, system_id: str | None = None) -> list[dict[str, Any]]:
    with conn() as c:
        if system_id:
            rows = c.execute(
                "SELECT * FROM sync_runs WHERE system_id = ? ORDER BY id DESC LIMIT ?",
                (system_id, limit),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_sync_run_dict(r) for r in rows]


def _sync_run_dict(row) -> dict[str, Any]:
    item = dict(row)
    item["summary"] = _loads(item.pop("summary_json"), {})
    return item


def cache_record(
    system_id: str,
    domain: str,
    external_id: str,
    payload: dict[str, Any],
    local_id: str | None = None,
) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO record_cache "
            "(system_id, domain, external_id, local_id, payload_json, synced_at, record_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(system_id, domain, external_id) DO UPDATE SET "
            "local_id = excluded.local_id, payload_json = excluded.payload_json, "
            "synced_at = excluded.synced_at, record_hash = excluded.record_hash",
            (system_id, domain, external_id, local_id, _json(payload), utc_now(), _hash(payload)),
        )


def record_external_ref(
    system_id: str,
    domain: str,
    local_id: str,
    external_id: str,
    external_url: str | None = None,
    props: dict[str, Any] | None = None,
) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO external_refs "
            "(system_id, domain, local_id, external_id, external_url, synced_at, props_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(system_id, domain, local_id, external_id) DO UPDATE SET "
            "external_url = excluded.external_url, synced_at = excluded.synced_at, props_json = excluded.props_json",
            (system_id, domain, local_id, external_id, external_url, utc_now(), _json(props or {})),
        )


def list_records(
    system_id: str | None = None,
    domain: str | None = None,
    local_id: str | None = None,
    limit: int = 50,
) -> dict[str, list[dict[str, Any]]]:
    where = []
    params: list[Any] = []
    for field, value in (("system_id", system_id), ("domain", domain), ("local_id", local_id)):
        if value:
            where.append(f"{field} = ?")
            params.append(value)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with conn() as c:
        refs = c.execute(
            f"SELECT * FROM external_refs {clause} ORDER BY synced_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        cache = c.execute(
            f"SELECT * FROM record_cache {clause} ORDER BY synced_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return {
        "external_refs": [_ref_dict(r) for r in refs],
        "records": [_cache_dict(r) for r in cache],
    }


def _ref_dict(row) -> dict[str, Any]:
    item = dict(row)
    item["props"] = _loads(item.pop("props_json"), {})
    return item


def _cache_dict(row) -> dict[str, Any]:
    item = dict(row)
    item["payload"] = _loads(item.pop("payload_json"), {})
    return item


def create_outbox_action(
    system_id: str,
    action_queue_id: int | None,
    agent: str,
    action_type: str,
    title: str,
    external_domain: str,
    payload: dict[str, Any],
    configured: bool,
) -> dict[str, Any]:
    ts = utc_now()
    external_id = f"draft-{system_id}-{action_queue_id or hashlib.sha1(_json(payload).encode()).hexdigest()[:8]}"
    status = "approval_required" if configured else "mock_only"
    result = {
        "write_mode": "approval_gated",
        "message": "Awaiting explicit apply call before any external write.",
    }
    with conn() as c:
        cur = c.execute(
            "INSERT INTO outbox_actions "
            "(ts, system_id, action_queue_id, agent, action_type, title, status, "
            "external_domain, external_id, payload_json, result_json, requires_approval) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                system_id,
                action_queue_id,
                agent,
                action_type,
                title,
                status,
                external_domain,
                external_id,
                _json(payload),
                _json(result),
                1,
            ),
        )
        outbox_id = cur.lastrowid
    return get_outbox_action(outbox_id) or {}


def list_outbox_actions(
    action_queue_id: int | None = None,
    system_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if action_queue_id is not None:
        where.append("action_queue_id = ?")
        params.append(action_queue_id)
    if system_id:
        where.append("system_id = ?")
        params.append(system_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with conn() as c:
        rows = c.execute(
            f"SELECT * FROM outbox_actions {clause} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [_outbox_dict(r) for r in rows]


def get_outbox_action(action_id: int, system_id: str | None = None) -> dict[str, Any] | None:
    with conn() as c:
        if system_id:
            row = c.execute(
                "SELECT * FROM outbox_actions WHERE id = ? AND system_id = ?",
                (action_id, system_id),
            ).fetchone()
        else:
            row = c.execute("SELECT * FROM outbox_actions WHERE id = ?", (action_id,)).fetchone()
    return _outbox_dict(row) if row else None


def update_outbox_action(
    action_id: int,
    status: str,
    result: dict[str, Any],
    external_id: str | None = None,
) -> dict[str, Any]:
    with conn() as c:
        if external_id:
            c.execute(
                "UPDATE outbox_actions SET status = ?, result_json = ?, external_id = ? WHERE id = ?",
                (status, _json(result), external_id, action_id),
            )
        else:
            c.execute(
                "UPDATE outbox_actions SET status = ?, result_json = ? WHERE id = ?",
                (status, _json(result), action_id),
            )
    return get_outbox_action(action_id) or {}


def _outbox_dict(row) -> dict[str, Any]:
    item = dict(row)
    item["payload"] = _loads(item.pop("payload_json"), {})
    item["result"] = _loads(item.pop("result_json"), {})
    item["requires_approval"] = bool(item["requires_approval"])
    return item
