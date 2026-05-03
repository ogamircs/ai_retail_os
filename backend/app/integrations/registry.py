from __future__ import annotations

from typing import Any

from app.integrations import store
from app.integrations.adapters import ADAPTERS
from app.spine.events import append_event

ADAPTER_BY_ID = {adapter.definition.system_id: adapter for adapter in ADAPTERS}
ADAPTER_ORDER = {adapter.definition.system_id: index for index, adapter in enumerate(ADAPTERS)}


def refresh_systems() -> list[dict[str, Any]]:
    rows = []
    for adapter in ADAPTERS:
        health = adapter.healthcheck()
        row = adapter.system_row()
        rows.append(store.upsert_system(row, status=health.status, error=health.error))
    return _ordered(store.list_systems())


def list_systems() -> list[dict[str, Any]]:
    refresh_systems()
    return _ordered(store.list_systems())


def _ordered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: ADAPTER_ORDER.get(row["system_id"], 999))


def list_sync_runs(limit: int = 30, system_id: str | None = None) -> list[dict[str, Any]]:
    return store.list_sync_runs(limit=limit, system_id=system_id)


def sync_system(system_id: str) -> dict[str, Any]:
    adapter = ADAPTER_BY_ID.get(system_id)
    if not adapter:
        return {"error": f"unknown integration system: {system_id}"}
    refresh_systems()
    run_id = store.start_sync_run(system_id)
    result = adapter.sync_inbound()
    run = store.finish_sync_run(
        run_id=run_id,
        system_id=system_id,
        status=result.status,
        records_read=result.records_read,
        records_written=result.records_written,
        summary=result.summary,
        error=result.error,
    )
    append_event(
        agent="Integration",
        kind="measurement" if result.status == "success" else "rollback",
        payload={
            "action": "sync_inbound",
            "system_id": system_id,
            "status": result.status,
            "records_read": result.records_read,
            "records_written": result.records_written,
            "summary": result.summary,
            "error": result.error,
        },
    )
    return {"sync_run": run, "result": result.to_dict()}


def sync_all() -> list[dict[str, Any]]:
    return [sync_system(adapter.definition.system_id) for adapter in ADAPTERS]


def list_records(
    system_id: str | None = None,
    domain: str | None = None,
    local_id: str | None = None,
    limit: int = 50,
) -> dict[str, list[dict[str, Any]]]:
    return store.list_records(system_id=system_id, domain=domain, local_id=local_id, limit=limit)


def propose_outbound(
    system_id: str,
    action_queue_id: int | None,
    agent: str,
    action_type: str,
    title: str,
    payload: dict[str, Any],
    external_domain: str | None = None,
) -> dict[str, Any]:
    adapter = ADAPTER_BY_ID.get(system_id)
    if not adapter:
        return {"error": f"unknown integration system: {system_id}"}
    refresh_systems()
    action = adapter.propose_outbound(
        action_queue_id=action_queue_id,
        agent=agent,
        action_type=action_type,
        title=title,
        payload=payload,
        external_domain=external_domain,
    )
    append_event(
        agent="Integration",
        kind="approval_required",
        payload={
            "action": "outbox_proposal",
            "system_id": system_id,
            "action_queue_id": action_queue_id,
            "outbox_action_id": action.get("id"),
            "outbox_status": action.get("status"),
            "external_domain": action.get("external_domain"),
            "external_id": action.get("external_id"),
        },
    )
    return action


def apply_outbound(system_id: str, action_id: int) -> dict[str, Any]:
    adapter = ADAPTER_BY_ID.get(system_id)
    if not adapter:
        return {"error": f"unknown integration system: {system_id}"}
    action = adapter.apply_outbound(action_id)
    if "error" not in action:
        append_event(
            agent="Integration",
            kind="action",
            payload={
                "action": "apply_outbound",
                "system_id": system_id,
                "outbox_action_id": action_id,
                "status": action.get("status"),
                "external_domain": action.get("external_domain"),
                "external_id": action.get("external_id"),
            },
        )
    return action


def handle_mautic_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    import uuid

    event_id = f"mautic-webhook-{uuid.uuid4().hex[:8]}"
    store.cache_record("mautic", "Webhook Event", event_id, payload, local_id=payload.get("campaign_id"))
    append_event(
        agent="Marketing",
        kind="measurement",
        payload={
            "action": "mautic_webhook",
            "system_id": "mautic",
            "event_id": event_id,
            "payload": payload,
        },
    )
    return {"status": "accepted", "event_id": event_id}


def outbox_actions(
    action_queue_id: int | None = None,
    system_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return store.list_outbox_actions(action_queue_id=action_queue_id, system_id=system_id, limit=limit)
