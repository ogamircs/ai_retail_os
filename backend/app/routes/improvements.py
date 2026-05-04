"""Improvement Auditor surface — kick off audits, poll runs, accept /
dismiss suggestions, expose the deterministic signals snapshot. The
auditor is read-only — it never proposes outbox actions or edits
substrate; it walks structured signals and records suggestions for
operator review."""

from __future__ import annotations

import threading
import uuid

from fastapi import APIRouter, HTTPException

from app.agents import improvement_auditor as ia
from app.llm import get_provider
from app.schemas import ImprovementRunsResponse, ImprovementSuggestionsResponse

router = APIRouter()


@router.post("/api/improvements/run")
def improvements_run():
    """Kick off an audit. Background thread; returns `{run_id}`. The
    cockpit polls `/api/improvements/runs/{run_id}` for terminal
    status and pulls suggestions from `/api/improvements/suggestions`.
    """
    try:
        llm = get_provider()
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e

    run_id = uuid.uuid4().hex[:12]
    ia.create_run(run_id)

    def _worker():
        try:
            ia.run_audit(run_id, llm)
        except Exception as exc:  # noqa: BLE001
            ia.complete_run(run_id, {"phase": "worker"}, error=str(exc))

    threading.Thread(
        target=_worker, daemon=True, name=f"improvement-audit-{run_id}"
    ).start()
    return {"run_id": run_id, "status": "running"}


@router.get("/api/improvements/runs/{run_id}")
def improvements_run_status(run_id: str):
    run = ia.get_run(run_id)
    if not run:
        raise HTTPException(404, f"unknown run '{run_id}'")
    return run


@router.get("/api/improvements/runs", response_model=ImprovementRunsResponse)
def improvements_runs_list(limit: int = 20):
    return {"runs": ia.list_runs(limit=limit)}


@router.get("/api/improvements/suggestions", response_model=ImprovementSuggestionsResponse)
def improvements_suggestions(
    status: str | None = "open",
    run_id: str | None = None,
    limit: int = 100,
):
    # Treat empty / 'all' as "no filter" so the cockpit can switch
    # between open / accepted / dismissed via a single dropdown.
    eff_status: str | None = status
    if not status or status == "all":
        eff_status = None
    return {
        "suggestions": ia.list_suggestions(
            run_id=run_id, status=eff_status, limit=limit
        )
    }


@router.post("/api/improvements/suggestions/{suggestion_id}/accept")
def improvements_accept(suggestion_id: int):
    out = ia.update_suggestion_status(suggestion_id, "accepted")
    if not out:
        raise HTTPException(404, f"unknown suggestion {suggestion_id}")
    return out


@router.post("/api/improvements/suggestions/{suggestion_id}/dismiss")
def improvements_dismiss(suggestion_id: int):
    out = ia.update_suggestion_status(suggestion_id, "dismissed")
    if not out:
        raise HTTPException(404, f"unknown suggestion {suggestion_id}")
    return out


@router.get("/api/improvements/signals")
def improvements_signals():
    """Debug endpoint — exposes the deterministic signals snapshot the
    audit agent sees, without invoking the LLM. Useful when tweaking
    `gather_signals` to verify it surfaces what you expect."""
    return ia.gather_signals()
