"""Improvement Auditor agent (Track 8).

Operator-triggered audit loop. The cockpit's [IMPROVE] tab has an
`audit now` button that POSTs `/api/improvements/run`; this module
gathers structured signals across the cockpit, runs an LLM agent to
synthesise improvement suggestions, and writes them to the
`improvement_suggestions` table for the operator to review.

The agent is **read-only**: it never proposes outbox actions, never
edits substrate, never publishes wiki pages. Its only output is
suggestions — operator decides what to do with each one (accept /
dismiss).

Architecture:

  1. `gather_signals()` — pure-Python, deterministic. Walks substrate
     KPIs, integration sync state, action-queue depth, wiki coverage,
     recent agent decisions. Returns a structured dict the LLM
     consumes as evidence. Keeps the agent grounded — the LLM does
     wording / prioritisation, not the data extraction.

  2. `build_agent()` — a small `Agent` with a single tool:
     `record_suggestion`. The agent's job is to walk the signals,
     pick 3-8 improvements worth surfacing, and call
     `record_suggestion` once per suggestion. The tool persists the
     suggestion row directly so we don't depend on the LLM emitting
     well-formed JSON in a final reply.

  3. `run_audit(run_id, llm)` — driver. Creates the run row, fires the
     agent, flips status to ok/error when done. Designed to be called
     from a daemon thread inside the FastAPI route handler so the
     POST returns immediately.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from app.agents.base import Agent, AgentEvent
from app.llm.base import LLMProvider, Tool
from app.llm.prompts import resolve_prompt
from app.spine import db, wiki as wiki_store
from app.spine.events import append_event, list_events
from app.substrate import omnichannel, pos


NAME = "Improvement Auditor"

SYSTEM = """You are the Improvement Auditor in the AI Retail OS.

You run when the operator clicks `audit now`. Your job: walk the
provided cockpit signals snapshot and surface 3-8 concrete improvement
suggestions the operator should act on. Be terse, specific, and cite
numbers from the snapshot.

Hard rules:
  1. **NEVER** propose anything you can't ground in the snapshot.
     Hallucinated numbers are worse than no suggestion.
  2. **NEVER** propose more than 8 suggestions per run.
  3. Each suggestion MUST be emitted via the `record_suggestion` tool.
     Do NOT write a summary as text — the tool writes the rows.
  4. After all `record_suggestion` calls, reply with one line naming
     the count.

Severity rubric:
  - `high`: a clear margin / stockout / customer-impact risk that the
    operator will want to act on this week.
  - `medium`: a tradeoff worth raising; reasonable to defer.
  - `low`: hygiene, documentation, or coverage gaps.

Areas:
  - `pricing` · `marketing` · `replenishment` · `merchandising`
  - `fulfillment` · `store_ops` · `supplier`
  - `inventory` · `wiki_coverage` · `cockpit_health`

Action hints (set `action_hint` on every suggestion):
  - `propose_outbox`  — there's a concrete outbox action shape
                        (markdown, PO hold, transfer) the operator
                        could trigger. Mention which specialist would
                        own it.
  - `wiki_edit`       — a durable lesson worth committing.
  - `operator_review` — needs human judgement; not a clean dispatch.
  - `no_action`       — pure observation; recorded for trend tracking.

Output exactly:
  - 3-8 record_suggestion tool calls
  - then ONE line text reply: `recorded N suggestions`
"""


# ---------------------------------------------------------------------------
# Signal gathering — deterministic; the LLM consumes the dict it returns.
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def gather_signals(
    recent_events_limit: int = 25,
    weak_sell_through_threshold: float = 0.5,
) -> dict[str, Any]:
    """Walk the cockpit's read surfaces and return a structured signals
    dict. Pure-Python — no LLM. Used by `run_audit` and exposed via
    `/api/improvements/signals` for debugging."""
    signals: dict[str, Any] = {"generated_ts": _now_iso()}

    # --- KPIs ---
    try:
        kpis = omnichannel.executive_kpis()
        signals["kpis"] = kpis
    except Exception as exc:
        signals["kpis_error"] = str(exc)

    # --- Categories — flag weak sell-through + overstock ---
    try:
        cats = omnichannel.list_categories()
        flagged_cats = []
        for c in cats:
            sell_through = c.get("sales_units", 0) / max(c.get("on_hand", 1), 1)
            margin_rate = c.get("margin_rate", 0)
            if sell_through < weak_sell_through_threshold or margin_rate < c.get(
                "margin_target", 0.30
            ):
                flagged_cats.append(
                    {
                        "category": c.get("category"),
                        "display_name": c.get("display_name"),
                        "sell_through": round(sell_through, 3),
                        "on_hand": c.get("on_hand"),
                        "margin_rate": margin_rate,
                        "margin_target": c.get("margin_target"),
                        "marketing_priority": c.get("marketing_priority"),
                        "weather_sensitivity": c.get("weather_sensitivity"),
                        "active_campaigns": c.get("active_campaigns"),
                    }
                )
        signals["categories_flagged"] = flagged_cats
        signals["categories_total"] = len(cats)
    except Exception as exc:
        signals["categories_error"] = str(exc)

    # --- Inventory health: vendor risk + inbound POs ---
    try:
        inv = omnichannel.inventory_health()
        signals["inventory_summary"] = {
            "categories_at_risk": inv.get("categories_at_risk", []),
            "supplier_risks": inv.get("supplier_risks", []),
            "inbound_pos": inv.get("inbound_pos", []),
        }
    except Exception as exc:
        signals["inventory_error"] = str(exc)

    # --- Action queue — pending approvals ---
    try:
        actions = omnichannel.list_action_queue()
        pending = [a for a in actions if a.get("status") in ("pending", "approval_required")]
        signals["pending_actions"] = [
            {
                "id": a.get("id"),
                "action_type": a.get("action_type"),
                "agent": a.get("agent"),
                "title": a.get("title"),
                "status": a.get("status"),
                "ts": a.get("ts"),
            }
            for a in pending[:20]
        ]
        signals["pending_actions_count"] = len(pending)
    except Exception as exc:
        signals["actions_error"] = str(exc)

    # --- Stores — capacity / labor exceptions ---
    try:
        stores = omnichannel.list_stores()
        flagged_stores = [
            {
                "store_id": s.get("store_id"),
                "name": s.get("name"),
                "labor_pressure": s.get("labor_pressure"),
                "local_demand_signal": s.get("local_demand_signal"),
            }
            for s in stores
            if s.get("labor_pressure", 0) > 0.7 or s.get("local_demand_signal", 0) > 0.7
        ]
        signals["stores_flagged"] = flagged_stores
        signals["stores_total"] = len(stores)
    except Exception as exc:
        signals["stores_error"] = str(exc)

    # --- Wiki coverage — which categories lack a policy/playbook page? ---
    try:
        all_pages = wiki_store.list_pages(status="published", limit=500)
        slugs = [p.slug for p in all_pages]
        cat_names = [c.get("category") for c in (signals.get("categories_flagged") or [])]
        coverage_gaps: list[str] = []
        for cat in cat_names:
            if not cat:
                continue
            if not any(cat in s for s in slugs):
                coverage_gaps.append(cat)
        signals["wiki"] = {
            "published_pages": len(all_pages),
            "coverage_gaps": coverage_gaps,
        }
    except Exception as exc:
        signals["wiki_error"] = str(exc)

    # --- Recent agent decisions — narrative for the LLM ---
    try:
        events = list_events(limit=recent_events_limit)
        signals["recent_events"] = [
            {
                "ts": e.get("ts"),
                "agent": e.get("agent"),
                "kind": e.get("kind"),
                "sku": e.get("sku"),
                "payload_excerpt": str(e.get("payload") or "")[:200],
            }
            for e in events
        ]
    except Exception as exc:
        signals["events_error"] = str(exc)

    # --- Integration mode ---
    try:
        from app.integrations import registry as ireg

        systems = ireg.list_systems()
        signals["integrations"] = [
            {
                "system_id": s.get("system_id"),
                "mode": s.get("mode"),
                "configured": s.get("configured"),
                "last_sync": s.get("last_sync_ts"),
            }
            for s in systems
        ]
    except Exception as exc:
        signals["integrations_error"] = str(exc)

    return signals


# ---------------------------------------------------------------------------
# Run lifecycle — store rows in improvement_runs / improvement_suggestions.
# ---------------------------------------------------------------------------


def create_run(run_id: str) -> dict:
    """Insert a `running` row in improvement_runs; returns the row."""
    now = _now_iso()
    with db.conn() as c:
        c.execute(
            "INSERT INTO improvement_runs (id, started_ts, status, summary_json) "
            "VALUES (?, ?, 'running', '{}')",
            (run_id, now),
        )
    return get_run(run_id)


def complete_run(run_id: str, summary: dict, error: str | None = None) -> dict:
    """Flip a run to ok/error + stamp summary."""
    status = "error" if error else "ok"
    with db.conn() as c:
        c.execute(
            "UPDATE improvement_runs SET status = ?, ended_ts = ?, "
            "summary_json = ?, error = ? WHERE id = ?",
            (status, _now_iso(), json.dumps(summary), error, run_id),
        )
    return get_run(run_id)


def get_run(run_id: str) -> dict | None:
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM improvement_runs WHERE id = ?", (run_id,)
        ).fetchone()
    return _row_to_run(row) if row else None


def list_runs(limit: int = 20) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM improvement_runs ORDER BY started_ts DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [_row_to_run(r) for r in rows]


def _row_to_run(row) -> dict:
    return {
        "id": row["id"],
        "started_ts": row["started_ts"],
        "ended_ts": row["ended_ts"],
        "status": row["status"],
        "summary": json.loads(row["summary_json"] or "{}"),
        "error": row["error"],
    }


def record_suggestion(
    run_id: str,
    area: str,
    severity: str,
    title: str,
    body_md: str,
    action_hint: str = "operator_review",
    refs: list[str] | None = None,
) -> dict:
    """Persist one suggestion. Called by the agent's tool impl AND by
    direct callers (tests, manual fixtures)."""
    refs = list(refs or [])
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO improvement_suggestions (run_id, area, severity, title, "
            "body_md, action_hint, status, refs_json, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (
                run_id,
                area,
                severity,
                title,
                body_md,
                action_hint,
                json.dumps(refs),
                _now_iso(),
            ),
        )
        sid = cur.lastrowid
    return get_suggestion(int(sid))


def get_suggestion(suggestion_id: int) -> dict | None:
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM improvement_suggestions WHERE id = ?", (suggestion_id,)
        ).fetchone()
    return _row_to_suggestion(row) if row else None


def list_suggestions(
    run_id: str | None = None,
    status: str | None = "open",
    limit: int = 100,
) -> list[dict]:
    sql = "SELECT * FROM improvement_suggestions"
    args: list[Any] = []
    where: list[str] = []
    if run_id is not None:
        where.append("run_id = ?")
        args.append(run_id)
    if status is not None:
        where.append("status = ?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(int(limit))
    with db.conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [_row_to_suggestion(r) for r in rows]


def update_suggestion_status(suggestion_id: int, status: str) -> dict | None:
    """Operator action: 'accepted' or 'dismissed'."""
    if status not in ("open", "accepted", "dismissed"):
        raise ValueError(f"unknown status: {status}")
    with db.conn() as c:
        c.execute(
            "UPDATE improvement_suggestions SET status = ? WHERE id = ?",
            (status, suggestion_id),
        )
    return get_suggestion(suggestion_id)


def _row_to_suggestion(row) -> dict:
    return {
        "id": int(row["id"]),
        "run_id": row["run_id"],
        "area": row["area"],
        "severity": row["severity"],
        "title": row["title"],
        "body_md": row["body_md"],
        "action_hint": row["action_hint"],
        "status": row["status"],
        "refs": json.loads(row["refs_json"] or "[]"),
        "ts": row["ts"],
    }


# ---------------------------------------------------------------------------
# Agent — tool builder + prompt + driver.
# ---------------------------------------------------------------------------


_VALID_SEVERITIES = ("high", "medium", "low")
_VALID_AREAS = (
    "pricing",
    "marketing",
    "replenishment",
    "merchandising",
    "fulfillment",
    "store_ops",
    "supplier",
    "inventory",
    "wiki_coverage",
    "cockpit_health",
)
_VALID_ACTION_HINTS = (
    "propose_outbox",
    "wiki_edit",
    "operator_review",
    "no_action",
)
MAX_SUGGESTIONS_PER_RUN = 8


def _build_record_suggestion_tool(run_id: str):
    """Construct the agent's only tool. Closes over `run_id` so the
    agent doesn't have to remember it on each call."""
    counter = {"n": 0}

    def _impl(args: dict) -> dict:
        if counter["n"] >= MAX_SUGGESTIONS_PER_RUN:
            return {
                "error": (
                    f"max {MAX_SUGGESTIONS_PER_RUN} suggestions per run reached"
                )
            }
        area = (args.get("area") or "").strip()
        severity = (args.get("severity") or "").strip().lower()
        title = (args.get("title") or "").strip()
        body_md = (args.get("body_md") or "").strip()
        action_hint = (args.get("action_hint") or "operator_review").strip()
        refs = args.get("refs") or []

        if area not in _VALID_AREAS:
            return {"error": f"area must be one of {_VALID_AREAS}, got '{area}'"}
        if severity not in _VALID_SEVERITIES:
            return {
                "error": f"severity must be one of {_VALID_SEVERITIES}, got '{severity}'"
            }
        if action_hint not in _VALID_ACTION_HINTS:
            return {
                "error": f"action_hint must be one of {_VALID_ACTION_HINTS}, "
                f"got '{action_hint}'"
            }
        if not title or not body_md:
            return {"error": "title and body_md are required"}

        suggestion = record_suggestion(
            run_id=run_id,
            area=area,
            severity=severity,
            title=title,
            body_md=body_md,
            action_hint=action_hint,
            refs=[str(r) for r in refs if isinstance(r, (str, int))],
        )
        counter["n"] += 1
        return {
            "suggestion_id": suggestion["id"],
            "count_so_far": counter["n"],
            "remaining_quota": MAX_SUGGESTIONS_PER_RUN - counter["n"],
        }

    tool = Tool(
        name="record_suggestion",
        description=(
            "Persist one improvement suggestion for the current audit "
            "run. The cockpit's [IMPROVE] tab will surface it for the "
            "operator. Title is one line; body_md is 3-6 bullets with "
            "concrete numbers from the signals snapshot. `refs` is an "
            "optional list of spine event ids / artifact ids that "
            "ground the suggestion."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "area": {"type": "string", "enum": list(_VALID_AREAS)},
                "severity": {"type": "string", "enum": list(_VALID_SEVERITIES)},
                "title": {"type": "string"},
                "body_md": {"type": "string"},
                "action_hint": {
                    "type": "string",
                    "enum": list(_VALID_ACTION_HINTS),
                },
                "refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["area", "severity", "title", "body_md"],
        },
    )
    return tool, _impl, counter


def build_agent(run_id: str) -> tuple[Agent, dict]:
    """Build the audit agent. Returns the agent + the counter dict so
    the driver can read final emission count without parsing the LLM
    output."""
    tool, impl, counter = _build_record_suggestion_tool(run_id)
    agent = Agent(
        name=NAME,
        system_prompt=resolve_prompt(NAME, SYSTEM),
        tools=[tool],
        tool_impls={"record_suggestion": impl},
        max_iters=12,
    )
    return agent, counter


def run_audit(run_id: str, llm: LLMProvider) -> dict:
    """Drive one audit run. Designed to be called from a daemon thread
    inside the FastAPI handler — caller doesn't await this. Returns
    the final summary; caller may ignore it (the run row carries the
    same data via `complete_run`)."""
    t0 = time.monotonic()
    try:
        signals = gather_signals()
    except Exception as exc:
        complete_run(run_id, {"phase": "gather_signals"}, error=str(exc))
        return {"status": "error", "error": str(exc)}

    agent, counter = build_agent(run_id)
    task = (
        "The cockpit just collected the following signals. Walk them, "
        "pick 3-8 concrete improvements worth surfacing, and emit one "
        "`record_suggestion` tool call per suggestion. Cite numbers from "
        "the snapshot in each body_md.\n\n"
        f"```json\n{json.dumps(signals, indent=2, default=str)[:12000]}\n```"
    )

    final_text = ""
    error: str | None = None
    try:
        for ev in agent.run(task, llm):
            if ev.kind == "agent_end":
                final_text = ev.data.get("text", "")
            elif ev.kind == "error":
                error = ev.data.get("error")
                break
    except Exception as exc:
        error = str(exc)

    elapsed_s = round(time.monotonic() - t0, 2)
    summary = {
        "suggestions": counter["n"],
        "elapsed_s": elapsed_s,
        "agent_final_text": final_text[:200],
        "signals_keys": sorted(signals.keys()),
    }
    complete_run(run_id, summary, error=error)
    append_event(
        agent=NAME,
        kind="observation",
        payload={
            "stage": "audit_summary",
            "run_id": run_id,
            **summary,
            "error": error,
        },
    )
    return {"status": "error" if error else "ok", "summary": summary, "error": error}
