import contextvars
import json
from datetime import UTC, datetime

from app.spine.db import conn

# Per-turn id propagated through every append_event call inside a
# `with current_turn_id_ctx(...)` block. Used by Track 5 W4/W6 to scope
# event walks to the *current* operator turn instead of a wall-clock
# window, which would cross-contaminate when multiple chat requests
# overlap (FastAPI runs them concurrently in the same process). Empty
# string when no turn is active — append_event simply omits the tag.
current_turn_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_turn_id", default=""
)


def append_event(
    agent: str,
    kind: str,
    payload: dict,
    sku: str | None = None,
    artifact_id: str | None = None,
) -> int:
    ts = datetime.now(UTC).isoformat()
    # Auto-stamp the active turn id into the payload so per-turn
    # walkers (auto-publish, curator) can filter their event windows
    # without disturbing the rest of the runtime.
    tid = current_turn_id.get()
    if tid and isinstance(payload, dict) and "turn_id" not in payload:
        payload = {**payload, "turn_id": tid}
    with conn() as c:
        cur = c.execute(
            "INSERT INTO events (ts, agent, kind, sku, payload_json, artifact_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, agent, kind, sku, json.dumps(payload), artifact_id),
        )
        return cur.lastrowid


def events_for_turn(turn_id: str, since_ts: str | None = None) -> list[dict]:
    """Return every event tagged with `turn_id` in payload.turn_id.

    `since_ts` is an optional cheap pre-filter at the SQL layer when
    the caller knows the turn started after a given ISO timestamp —
    keeps the scan small in long-running deployments. The Python-side
    payload check is the authoritative filter.
    """
    if not turn_id:
        return []
    with conn() as c:
        if since_ts:
            rows = c.execute(
                "SELECT id, ts, agent, kind, sku, payload_json, artifact_id "
                "FROM events WHERE ts >= ? ORDER BY id ASC",
                (since_ts,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, ts, agent, kind, sku, payload_json, artifact_id "
                "FROM events ORDER BY id ASC"
            ).fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("turn_id") != turn_id:
            continue
        out.append(
            {
                "id": r["id"],
                "ts": r["ts"],
                "agent": r["agent"],
                "kind": r["kind"],
                "sku": r["sku"],
                "payload": payload,
                "artifact_id": r["artifact_id"],
            }
        )
    return out


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
