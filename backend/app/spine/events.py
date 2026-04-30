import json
from datetime import datetime, timezone
from app.spine.db import conn


def append_event(
    agent: str,
    kind: str,
    payload: dict,
    sku: str | None = None,
    artifact_id: str | None = None,
) -> int:
    ts = datetime.now(timezone.utc).isoformat()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO events (ts, agent, kind, sku, payload_json, artifact_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, agent, kind, sku, json.dumps(payload), artifact_id),
        )
        return cur.lastrowid


def list_events(since_id: int = 0, limit: int = 200) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, ts, agent, kind, sku, payload_json, artifact_id "
            "FROM events WHERE id > ? ORDER BY id DESC LIMIT ?",
            (since_id, limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "ts": r["ts"],
            "agent": r["agent"],
            "kind": r["kind"],
            "sku": r["sku"],
            "payload": json.loads(r["payload_json"]),
            "artifact_id": r["artifact_id"],
        }
        for r in rows
    ]


def events_since_ts(ts: str) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, ts, agent, kind, sku, payload_json, artifact_id "
            "FROM events WHERE ts >= ? ORDER BY id ASC",
            (ts,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "ts": r["ts"],
            "agent": r["agent"],
            "kind": r["kind"],
            "sku": r["sku"],
            "payload": json.loads(r["payload_json"]),
            "artifact_id": r["artifact_id"],
        }
        for r in rows
    ]
