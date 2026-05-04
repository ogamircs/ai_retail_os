"""Agent-mesh status — guardrail downgrades + active config so the cockpit
status strip can render a chip without polling the events endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from app.schemas import MeshStatus
from app.spine import events as ev_store

router = APIRouter()


@router.get("/api/mesh/status", response_model=MeshStatus)
def mesh_status(window_seconds: int = 300):
    """Track 2 A6 — surface recent guardrail downgrades to the cockpit.

    Returns the most recent `mesh_downgrade` event in the last
    `window_seconds`, plus the active mesh config so the status strip
    can render a chip + tooltip without hitting the events endpoint
    directly.
    """
    from app.config import mesh as mesh_settings

    cutoff = (datetime.now(UTC) - timedelta(seconds=window_seconds)).isoformat()
    recent = [e for e in ev_store.events_since_ts(cutoff) if e["kind"] == "mesh_downgrade"]
    last = recent[-1] if recent else None
    return {
        "enabled": mesh_settings.enabled,
        "config": {
            "max_revision_rounds": mesh_settings.max_revision_rounds,
            "max_critic_per_draft": mesh_settings.max_critic_per_draft,
            "turn_token_budget": mesh_settings.turn_token_budget,
            "turn_wallclock_seconds": mesh_settings.turn_wallclock_seconds,
        },
        "recent_downgrade": last,
        "downgrade_count_window": len(recent),
        "window_seconds": window_seconds,
    }
