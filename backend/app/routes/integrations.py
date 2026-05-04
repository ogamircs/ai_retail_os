"""Integration adapter surface — sync runs, record cache reads, outbound
apply, Mautic webhook intake. Every write goes through the registry, which
gates on per-action approval state."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from app.integrations import registry as integration_registry
from app.schemas import IntegrationSystemsResponse, SyncRunsResponse

router = APIRouter()


@router.get("/api/integrations/systems", response_model=IntegrationSystemsResponse)
def get_integration_systems():
    return {"systems": integration_registry.list_systems()}


@router.post("/api/integrations/{system}/sync")
def sync_integration(system: str):
    result = integration_registry.sync_system(system)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@router.get("/api/integrations/sync-runs", response_model=SyncRunsResponse)
def get_integration_sync_runs(limit: int = 30, system: str | None = None):
    return {"sync_runs": integration_registry.list_sync_runs(limit=limit, system_id=system)}


@router.get("/api/integrations/records")
def get_integration_records(
    system: str | None = None,
    domain: str | None = None,
    local_id: str | None = None,
    limit: int = 50,
):
    return integration_registry.list_records(
        system_id=system,
        domain=domain,
        local_id=local_id,
        limit=limit,
    )


@router.post("/api/integrations/{system}/actions/{action_id}/apply")
def apply_integration_action(system: str, action_id: int):
    result = integration_registry.apply_outbound(system, action_id)
    # Top-level "error" key = registry-level rejection (unknown system_id, etc.).
    if "error" in result:
        raise HTTPException(404, result["error"])
    # `status == "error"` = adapter ran but the external write didn't land
    # (no matching ERPNext row, empty payload, ERPNext rejected the doc).
    # The outbox row is already updated to status=error in the DB; we surface
    # it as 422 so the cockpit drawer's apply path catches and renders the
    # red chip instead of the green "applied" chip on a no-op.
    if result.get("status") == "error":
        nested = result.get("result") or {}
        message = (
            nested.get("error")
            or nested.get("message")
            or "external apply failed; outbox left in error state"
        )
        raise HTTPException(422, message)
    return result


@router.post("/api/integrations/mautic/webhook")
def mautic_webhook(payload: dict = Body(default_factory=dict)):
    return integration_registry.handle_mautic_webhook(payload)
