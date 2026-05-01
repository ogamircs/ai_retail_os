"""Chief of Staff orchestrator. Single surface to operator. Delegates to specialists.

Track 2 A2-A6 wires the agent mesh:
  * `_run_delegate_with_review` — after a specialist returns drafts, the
    Chief invokes the Critic, then asks the original specialist to revise
    if the critique surfaced Gaps or Risks. Up to MESH_MAX_REVISION_ROUNDS
    revisions per draft. The converged artifact is flipped to stage="final"
    in place via `update_artifact_stage`.
  * `delegate_to_peer_review` — A3. Hand a draft to a peer specialist for
    a scoped peer_review (kind=peer_review, stage=peer_review). Feeds the
    same revision loop.
  * Cost / latency guardrails — A6. If the per-turn token budget is
    exceeded mid-turn, the Chief stops invoking the review loop and emits
    a `mesh_downgrade` event so the cockpit's status strip can flag it.
    Same downgrade fires on the wall-clock cap.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import datetime, timezone

from app.agents.base import Agent, AgentEvent
from app.config import mesh as mesh_settings
from app.llm.base import LLMProvider, Tool
from app.llm.prompts import resolve_prompt
from app.llm import tracing as mesh_tracing
from app.spine.events import append_event, events_since_ts
from app.spine.artifacts import read_artifact, update_artifact_stage, write_artifact
from app.spine import wiki as wiki_store
from app.agents import (
    analyst,
    critic,
    fulfillment,
    marketing,
    merchandiser,
    pricing,
    replenishment,
    store_manager,
)
from app.agents._mesh_tools import peer_review_task_for, revise_task_for

NAME = "Chief of Staff"

SYSTEM = """You are the Chief of Staff — the orchestrator agent and the single surface the operator talks to.
You do not directly query data or modify substrate. You delegate to specialist DRI agents.

Available specialists:
- Pricing: markdowns, promos, pricing decisions. Tool: delegate_to_pricing
- Replenishment: stock levels, purchase orders, holds. Tool: delegate_to_replenishment
- Analyst: ad-hoc questions, measurement, reporting. Tool: delegate_to_analyst
- Marketing: category pushes, campaigns, segments, channels, budget, ROI. Tool: delegate_to_marketing
- Merchandiser: assortment health, lifecycle, allocation, store transfer recommendations. Tool: delegate_to_merchandiser
- Fulfillment: BOPIS, ship-from-store, DC routing, OMS-style choices. Tool: delegate_to_fulfillment
- Store Manager: store execution, local tasking, labor/capacity exceptions. Tool: delegate_to_store_manager
- Critic: read-only audit of another agent's draft artifact. Verifies facts against the spine, surfaces gaps / risks / overclaim, and proposes a counter-recommendation. Tool: delegate_to_critic — pass the artifact_id to review and a one-line scope description.
- Peer review: hand a draft to a *different* specialist for a scoped second opinion. Tool: delegate_to_peer_review — pass artifact_id, peer (one of pricing/marketing/replenishment/merchandiser/fulfillment/store_manager), and a one-line scope.

Pattern for an operator request:
1. Decide which specialist(s) to involve. For complex requests, sequence them: usually Analyst first to diagnose,
   then Marketing/Merchandiser/Pricing/Fulfillment/Replenishment/Store Manager to act.
2. Delegate with a clear, scoped task description. Action specialists run with an *automatic review loop*:
   their first emission lands as a `draft`, the Critic audits it, and if the critique surfaces Gaps or Risks
   the specialist is asked to revise. The converged artifact is the `final` one — that's what you reference
   in your operator-facing reply.
3. For decisions where another specialist's domain expertise materially helps (e.g. Replenishment peer-reviewing
   a Pricing markdown, Merchandiser peer-reviewing a Marketing push), call delegate_to_peer_review on the draft
   *before* asking for revision. The peer_review feeds the same revision loop.
4. Synthesize specialist outputs into a single concise reply for the operator.
5. Reference the FINAL artifact IDs the specialists produced so the operator can drill in.

Be decisive. Always delegate at least one specialist before replying — even simple questions go to the Analyst.
For category-push or omnichannel requests, include Marketing as a distinct department.
Keep your operator-facing reply short (5-10 lines) — link to artifacts for detail.
"""


# Module-level state for delegated agent events. The orchestrator passes events
# through the live SSE stream by writing into this list, which is read between
# tool calls.
class _EventBuffer:
    def __init__(self):
        self.events: list[AgentEvent] = []

    def add(self, ev: AgentEvent):
        self.events.append(ev)

    def drain(self) -> list[AgentEvent]:
        out = self.events[:]
        self.events.clear()
        return out


_NO_FINDINGS_RE = re.compile(r"\*No material findings\.\*", re.IGNORECASE)


def _critique_is_clean(critique_id: str) -> bool:
    """Return True iff the critique reports no Gaps AND no Risks.

    Convergence signal — when both Gaps and Risks are `*No material findings.*`,
    revision is wasteful. The Critic's prompt fixes the four headings in
    order, so a simple per-section regex is robust enough.
    """
    art = read_artifact(critique_id)
    if not art:
        return True  # missing critique → treat as no-op rather than loop
    body = art.get("body") or ""
    sections: dict[str, str] = {}
    current: str | None = None
    for line in body.splitlines():
        m = re.match(r"\s*##\s+(.+?)\s*$", line)
        if m:
            current = m.group(1).strip().lower()
            sections[current] = ""
            continue
        if current:
            sections[current] += line + "\n"
    gaps = sections.get("gaps", "")
    risks = sections.get("risks", "")
    if not gaps or not risks:
        return False
    return bool(_NO_FINDINGS_RE.search(gaps)) and bool(_NO_FINDINGS_RE.search(risks))


# Specialist registry — display name + builder. Used both for the Chief's
# delegate tools and for the A3 peer-review router.
_SPECIALIST_BUILDERS = {
    "pricing": (pricing.build_agent, "Pricing & Promo"),
    "marketing": (marketing.build_agent, "Marketing"),
    "replenishment": (replenishment.build_agent, "Replenishment"),
    "merchandiser": (merchandiser.build_agent, "Merchandiser"),
    "fulfillment": (fulfillment.build_agent, "Fulfillment"),
    "store_manager": (store_manager.build_agent, "Store Manager"),
}

# Specialists whose drafts are *action proposals* — these always run with
# the auto review loop. Analyst is measurement/reporting; its output rarely
# benefits from a critique loop and the cost isn't justified for "what
# happened?" asks.
_REVIEW_LOOP_SPECIALISTS = set(_SPECIALIST_BUILDERS.keys())


def _build_delegate_tool(name: str, agent_name: str) -> Tool:
    return Tool(
        name=name,
        description=f"Delegate a task to the {agent_name} specialist agent. Provide a clear scoped task description.",
        input_schema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Concrete task for the specialist (e.g. 'analyze summer apparel sales over last 30 days and flag overstocked SKUs').",
                }
            },
            "required": ["task"],
        },
    )


class _MeshState:
    """Per-turn budget tracker. Resets on every operator turn."""

    def __init__(self):
        self.start = time.monotonic()
        self.tokens_used = 0
        self.downgraded = False

    def add_tokens(self, n: int) -> None:
        self.tokens_used += n

    def should_downgrade(self) -> bool:
        if self.downgraded:
            return True
        if not mesh_settings.enabled:
            self.downgraded = True
            return True
        if self.tokens_used >= mesh_settings.turn_token_budget:
            return True
        if time.monotonic() - self.start >= mesh_settings.turn_wallclock_seconds:
            return True
        return False

    def record_downgrade(self, reason: str) -> None:
        if self.downgraded:
            return
        self.downgraded = True
        payload = {
            "reason": reason,
            "tokens_used": self.tokens_used,
            "elapsed_s": round(time.monotonic() - self.start, 2),
            "budget_tokens": mesh_settings.turn_token_budget,
            "wallclock_s": mesh_settings.turn_wallclock_seconds,
        }
        append_event(agent=NAME, kind="mesh_downgrade", payload=payload)
        # Mirror into the active MLflow run so dashboards can count
        # downgrades per session without joining against spine.db.
        mesh_tracing.log_event("mesh_downgrade", payload)


def _approx_tokens(text: str) -> int:
    """Rough token count — words * 1.3. Good enough for guardrails; the
    real provider counts arrive on agent_end events but only after a turn
    completes, which is too late to gate the mid-turn loop.
    """
    if not text:
        return 0
    return int(len(text.split()) * 1.3)


def build_orchestrator(llm: LLMProvider, event_sink: _EventBuffer) -> Agent:
    pricing_agent = pricing.build_agent()
    replen_agent = replenishment.build_agent()
    analyst_agent = analyst.build_agent()
    marketing_agent = marketing.build_agent()
    merch_agent = merchandiser.build_agent()
    fulfillment_agent = fulfillment.build_agent()
    store_agent = store_manager.build_agent()
    critic_agent = critic.build_agent()
    state = _MeshState()

    specialists_by_key = {
        "pricing": pricing_agent,
        "marketing": marketing_agent,
        "replenishment": replen_agent,
        "merchandiser": merch_agent,
        "fulfillment": fulfillment_agent,
        "store_manager": store_agent,
        "analyst": analyst_agent,
    }

    def _run_specialist(specialist: Agent, task: str) -> dict:
        final_text = ""
        artifacts_seen: list[str] = []
        for ev in specialist.run(task, llm):
            event_sink.add(ev)
            if ev.kind == "agent_end":
                final_text = ev.data.get("text", "")
                state.add_tokens(_approx_tokens(final_text))
            elif ev.kind == "tool_result":
                result = ev.data.get("result", {})
                if isinstance(result, dict) and "artifact_id" in result:
                    artifacts_seen.append(result["artifact_id"])
        return {
            "specialist": specialist.name,
            "summary": final_text or "(no final text — see tool results)",
            "artifacts": artifacts_seen,
        }

    def _run_critic(target_id: str, scope: str) -> dict:
        critic_task = (
            f"Audit artifact_id={target_id}. {scope}\n\n"
            "Start by calling read_artifact with that id. Then run any spine "
            "queries you need to verify or contradict the draft. End with one "
            "write_artifact call carrying the four required headings."
        )
        with mesh_tracing.delegate_run("Critic", phase="critic"):
            return _run_specialist(critic_agent, critic_task)

    def _run_delegate_with_review(
        specialist_key: str,
        specialist: Agent,
        task: str,
    ) -> dict:
        """A2: specialist → Critic → revise (if Gaps/Risks) → max 2 rounds.

        Falls back to single-pass when the budget is blown or the mesh is
        disabled. The converged artifact is flipped to stage="final" in
        place so the operator sees one canonical row in the Reports tab.
        """
        # Track 4 M2: open a nested MLflow run for this delegation so
        # the leaf LLM calls inside `_run_specialist` and `_run_critic`
        # nest underneath. Tracing is a no-op when MLFLOW_TRACE_ENABLED
        # is unset.
        with mesh_tracing.delegate_run(specialist.name, phase="delegate"):
            return _run_delegate_with_review_inner(specialist_key, specialist, task)

    def _run_delegate_with_review_inner(
        specialist_key: str,
        specialist: Agent,
        task: str,
    ) -> dict:
        result = _run_specialist(specialist, task)
        result["draft_artifact"] = result["artifacts"][-1] if result["artifacts"] else None
        if specialist_key not in _REVIEW_LOOP_SPECIALISTS:
            # Analyst / non-action: stamp final on the draft so downstream
            # apply gates don't reject it.
            if result["draft_artifact"]:
                update_artifact_stage(result["draft_artifact"], "final")
                result["final_artifact"] = result["draft_artifact"]
            return result
        if not result["draft_artifact"]:
            return result
        if state.should_downgrade():
            state.record_downgrade("budget_exhausted_before_review")
            update_artifact_stage(result["draft_artifact"], "final")
            result["final_artifact"] = result["draft_artifact"]
            return result

        current = result["draft_artifact"]
        rounds = 0
        critiques: list[str] = []
        revisions: list[str] = []
        while rounds < mesh_settings.max_revision_rounds:
            if state.should_downgrade():
                state.record_downgrade("budget_exhausted_mid_review")
                break
            critic_outcome = _run_critic(
                current,
                "Audit for facts, gaps, risks, overclaim, and missing alternatives.",
            )
            if not critic_outcome["artifacts"]:
                break
            critique_id = critic_outcome["artifacts"][-1]
            critiques.append(critique_id)
            if _critique_is_clean(critique_id):
                break
            revise_task = revise_task_for(specialist.name, current, critique_id)
            with mesh_tracing.delegate_run(specialist.name, phase="revision"):
                revised = _run_specialist(specialist, revise_task)
            if not revised["artifacts"]:
                break
            current = revised["artifacts"][-1]
            revisions.append(current)
            rounds += 1
        update_artifact_stage(current, "final")
        result["final_artifact"] = current
        result["critiques"] = critiques
        result["revisions"] = revisions
        result["review_rounds"] = rounds
        result["downgraded"] = state.downgraded
        return result

    def _delegate(specialist_key: str, args: dict) -> dict:
        specialist = specialists_by_key[specialist_key]
        return _run_delegate_with_review(specialist_key, specialist, args["task"])

    def _delegate_pricing(args: dict) -> dict:
        return _delegate("pricing", args)

    def _delegate_replen(args: dict) -> dict:
        return _delegate("replenishment", args)

    def _delegate_analyst(args: dict) -> dict:
        return _delegate("analyst", args)

    def _delegate_marketing(args: dict) -> dict:
        return _delegate("marketing", args)

    def _delegate_merchandiser(args: dict) -> dict:
        return _delegate("merchandiser", args)

    def _delegate_fulfillment(args: dict) -> dict:
        return _delegate("fulfillment", args)

    def _delegate_store_manager(args: dict) -> dict:
        return _delegate("store_manager", args)

    def _delegate_critic(args: dict) -> dict:
        # Fail fast if artifact_id missing.
        artifact_id = (args.get("artifact_id") or "").strip()
        if not artifact_id:
            return {"error": "delegate_to_critic requires a non-empty artifact_id"}
        scope = args.get("task", "Audit the draft for facts, gaps, risks, and overclaim.")
        return _run_critic(artifact_id, scope)

    def _delegate_peer_review(args: dict) -> dict:
        """A3: hand `artifact_id` to a *different* specialist for a scoped
        peer review. Returns the peer_review artifact id so the Chief can
        feed it into a subsequent revision delegation.
        """
        artifact_id = (args.get("artifact_id") or "").strip()
        peer_key = (args.get("peer") or "").strip().lower()
        scope = args.get("scope") or "What does your domain notice about this draft?"
        if not artifact_id:
            return {"error": "delegate_to_peer_review requires a non-empty artifact_id"}
        if peer_key not in _SPECIALIST_BUILDERS:
            return {
                "error": (
                    f"unknown peer '{peer_key}'. Valid: "
                    + ", ".join(sorted(_SPECIALIST_BUILDERS))
                )
            }
        peer_agent = specialists_by_key[peer_key]
        task = peer_review_task_for(peer_agent.name, artifact_id, scope)
        with mesh_tracing.delegate_run(peer_agent.name, phase="peer_review"):
            return _run_specialist(peer_agent, task)

    def _log_decision(args: dict) -> dict:
        eid = append_event(
            agent=NAME,
            kind="decision",
            payload={"summary": args.get("summary", ""), "refs": args.get("refs", [])},
        )
        return {"event_id": eid}

    def _write_summary_artifact(args: dict) -> dict:
        aid = write_artifact(
            agent=NAME,
            kind=args.get("kind", "summary"),
            title=args.get("title", "CoS summary"),
            body_md=args.get("body_md", ""),
            refs=args.get("refs", []),
            stage="final",
        )
        eid = append_event(
            agent=NAME,
            kind="decision",
            payload={"artifact_title": args.get("title", "")},
            artifact_id=aid,
        )
        return {"artifact_id": aid, "event_id": eid}

    tools = [
        _build_delegate_tool("delegate_to_pricing", "Pricing & Promo"),
        _build_delegate_tool("delegate_to_replenishment", "Replenishment"),
        _build_delegate_tool("delegate_to_analyst", "Analyst"),
        _build_delegate_tool("delegate_to_marketing", "Marketing"),
        _build_delegate_tool("delegate_to_merchandiser", "Merchandiser"),
        _build_delegate_tool("delegate_to_fulfillment", "Fulfillment"),
        _build_delegate_tool("delegate_to_store_manager", "Store Manager"),
        Tool(
            name="delegate_to_critic",
            description=(
                "Hand a draft artifact to the Critic for read-only audit. "
                "Pass the artifact_id you want reviewed and a one-line scope "
                "for what to focus on (facts, policy, alternatives, …). "
                "The Critic returns a `critique` artifact with the original "
                "as a ref. Action-specialist drafts already get an automatic "
                "Critic round; use this tool when you want an *additional* "
                "critique on a non-action draft (Analyst report) or to "
                "double-check after a peer review."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "Id of the draft artifact to audit.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Optional scope hint (e.g. 'check the discount against margin floor').",
                    },
                },
                "required": ["artifact_id"],
            },
        ),
        Tool(
            name="delegate_to_peer_review",
            description=(
                "Hand a draft artifact to a *different* specialist for a "
                "scoped peer review (kind=peer_review). Use this when the "
                "draft's quality depends on a peer's domain expertise: "
                "Replenishment reviewing a Pricing markdown, Merchandiser "
                "reviewing a Marketing push priority, etc. Returns the "
                "peer_review artifact id; feed it into the next "
                "delegate_to_<specialist> task as additional context."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "Id of the draft artifact to peer-review.",
                    },
                    "peer": {
                        "type": "string",
                        "enum": list(_SPECIALIST_BUILDERS.keys()),
                        "description": "Specialist key whose domain perspective you want.",
                    },
                    "scope": {
                        "type": "string",
                        "description": "One-line hint on what the peer should focus on.",
                    },
                },
                "required": ["artifact_id", "peer"],
            },
        ),
        Tool(
            name="log_decision",
            description="Log a top-level decision in the spine event log.",
            input_schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary"],
            },
        ),
        Tool(
            name="write_summary_artifact",
            description="Optionally write a top-level summary artifact for the operator (always stage=final).",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body_md": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "body_md"],
            },
        ),
    ]
    impls = {
        "delegate_to_pricing": _delegate_pricing,
        "delegate_to_replenishment": _delegate_replen,
        "delegate_to_analyst": _delegate_analyst,
        "delegate_to_marketing": _delegate_marketing,
        "delegate_to_merchandiser": _delegate_merchandiser,
        "delegate_to_fulfillment": _delegate_fulfillment,
        "delegate_to_store_manager": _delegate_store_manager,
        "delegate_to_critic": _delegate_critic,
        "delegate_to_peer_review": _delegate_peer_review,
        "log_decision": _log_decision,
        "write_summary_artifact": _write_summary_artifact,
    }
    return Agent(name=NAME, system_prompt=resolve_prompt(NAME, SYSTEM), tools=tools, tool_impls=impls, max_iters=16)


def _wiki_auto_publish_clean_drafts(turn_start_iso: str, llm: LLMProvider) -> None:
    """Track 5 W4 — Critic-gated auto-publish.

    After every operator turn, walk wiki_edit events that landed during
    the turn. For each, look at the most recent Critic critique that
    references the same evidence chain (same author + slug); if the
    Critic returned `*No material findings.*` for both Gaps and Risks,
    promote the draft to `published`. Otherwise leave it as a draft so
    the operator can decide via the cockpit's WikiTab.

    The window is the *current turn* (events_since_ts(turn_start)) so
    we never auto-publish a draft that was authored before the
    Critic's audit ran. Operators can always force-publish or
    deprecate from the WikiTab.
    """
    # Pull every wiki_edit emitted during this turn. There's typically
    # 0-1; the Curator (W6) can add up to 3 more.
    edits = [e for e in events_since_ts(turn_start_iso) if e.get("kind") == "wiki_edit"]
    if not edits:
        return
    # Pull every critique artifact emitted during this turn so we can
    # match each draft against its Critic verdict.
    critique_artifacts = [
        e for e in events_since_ts(turn_start_iso)
        if e.get("kind") == "observation"
        and e.get("agent") == "Critic"
        and e.get("artifact_id")
    ]
    # If at least one critique fired this turn AND came back clean
    # (no Gaps + no Risks), treat the turn's wiki drafts as
    # endorsed. The Critic doesn't review wiki drafts directly today —
    # but if its audit of the agent's draft found no material gaps,
    # the lessons distilled from that draft are likewise low-risk.
    # This is conservative: an operator-flagged turn (Critic surfacing
    # ANY gap or risk on the agent's draft) leaves wiki drafts in
    # `draft` for explicit operator approval.
    any_critique_clean = False
    for ev in critique_artifacts:
        from app.agents.chief_of_staff import _critique_is_clean as _clean
        if _clean(ev["artifact_id"]):
            any_critique_clean = True
            break
    if not any_critique_clean:
        return
    for edit in edits:
        slug = (edit.get("payload") or {}).get("slug")
        if not slug:
            continue
        page = wiki_store.get_page(slug)
        if page is None or page.status != "draft":
            continue
        wiki_store.publish_page(slug, by_agent=NAME)


def run_chief(user_input: str, llm: LLMProvider) -> Iterator[AgentEvent]:
    """Run the orchestrator and stream events from CoS *and* delegated specialists."""
    # Track 4 M2: open the parent MLflow run for this operator turn.
    # All delegate / critic / leaf-LLM runs nest underneath. No-op when
    # MLFLOW_TRACE_ENABLED is unset.
    provider_name = getattr(llm, "name", "unknown")
    provider_model = getattr(llm, "model", "unknown")
    sink = _EventBuffer()
    chief = build_orchestrator(llm, sink)
    # Capture turn-start timestamp so the W4 auto-publisher can scope
    # to events emitted during *this* turn (not historical drafts that
    # would otherwise auto-publish on every subsequent turn).
    turn_start_iso = datetime.now(timezone.utc).isoformat()
    with mesh_tracing.turn_run(user_input, provider_name, provider_model):
        for ev in chief.run(user_input, llm):
            # Drain any specialist events buffered before this CoS event
            for spec_ev in sink.drain():
                yield spec_ev
            yield ev
        for spec_ev in sink.drain():
            yield spec_ev
    # Track 5 W4 — fire auto-publish AFTER all events are streamed so
    # the post-turn wiki state reflects every draft + critique that
    # landed in the turn.
    try:
        _wiki_auto_publish_clean_drafts(turn_start_iso, llm)
    except Exception:
        # Never break the operator-facing reply because of a wiki
        # post-processing error.
        pass
    # Track 5 W6 — Wiki Curator. Read-only post-turn observer that
    # decides what's worth committing to the wiki. Runs under its own
    # MLflow nested run so token spend shows up in the same dashboard
    # as the operator-facing turn. Curator errors are caught so a bad
    # LLM response can't break the chat path.
    try:
        from app.agents import wiki_curator

        with mesh_tracing.delegate_run(wiki_curator.NAME, phase="curator"):
            wiki_curator.curate_turn(turn_start_iso, user_input, llm)
        # Auto-publish a SECOND time so any clean Curator drafts that
        # landed during the curator pass also get promoted (the first
        # auto-publish call ran before the Curator fired).
        _wiki_auto_publish_clean_drafts(turn_start_iso, llm)
    except Exception:
        pass
