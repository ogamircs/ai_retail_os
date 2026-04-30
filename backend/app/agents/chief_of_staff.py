"""Chief of Staff orchestrator. Single surface to operator. Delegates to specialists."""

import json
from collections.abc import Iterator
from app.agents.base import Agent, AgentEvent
from app.llm.base import LLMProvider, Tool, Message
from app.spine.events import append_event
from app.spine.artifacts import write_artifact
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

Pattern for an operator request:
1. Decide which specialist(s) to involve. For complex requests, sequence them: usually Analyst first to diagnose,
   then Marketing/Merchandiser/Pricing/Fulfillment/Replenishment/Store Manager to act.
2. Delegate with a clear, scoped task description.
3. Synthesize specialist outputs into a single concise reply for the operator.
4. Reference the artifact IDs the specialists produced so the operator can drill in.

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


def build_orchestrator(llm: LLMProvider, event_sink: _EventBuffer) -> Agent:
    pricing_agent = pricing.build_agent()
    replen_agent = replenishment.build_agent()
    analyst_agent = analyst.build_agent()
    marketing_agent = marketing.build_agent()
    merch_agent = merchandiser.build_agent()
    fulfillment_agent = fulfillment.build_agent()
    store_agent = store_manager.build_agent()
    critic_agent = critic.build_agent()

    def _run_delegate(specialist: Agent, task: str) -> dict:
        final_text = ""
        artifacts_seen: list[str] = []
        last_tool_result = None
        for ev in specialist.run(task, llm):
            event_sink.add(ev)
            if ev.kind == "agent_end":
                final_text = ev.data.get("text", "")
            elif ev.kind == "tool_result":
                result = ev.data.get("result", {})
                last_tool_result = result
                if isinstance(result, dict) and "artifact_id" in result:
                    artifacts_seen.append(result["artifact_id"])
        return {
            "specialist": specialist.name,
            "summary": final_text or "(no final text — see tool results)",
            "artifacts": artifacts_seen,
        }

    def _delegate_pricing(args: dict) -> dict:
        return _run_delegate(pricing_agent, args["task"])

    def _delegate_replen(args: dict) -> dict:
        return _run_delegate(replen_agent, args["task"])

    def _delegate_analyst(args: dict) -> dict:
        return _run_delegate(analyst_agent, args["task"])

    def _delegate_marketing(args: dict) -> dict:
        return _run_delegate(marketing_agent, args["task"])

    def _delegate_merchandiser(args: dict) -> dict:
        return _run_delegate(merch_agent, args["task"])

    def _delegate_fulfillment(args: dict) -> dict:
        return _run_delegate(fulfillment_agent, args["task"])

    def _delegate_store_manager(args: dict) -> dict:
        return _run_delegate(store_agent, args["task"])

    def _delegate_critic(args: dict) -> dict:
        # Fail fast if artifact_id missing — runtime tool calls aren't schema-
        # validated, so a malformed call would otherwise launch the Critic on
        # an empty id and produce a low-value critique. Returning an explicit
        # error lets the orchestrator see and recover from it.
        artifact_id = (args.get("artifact_id") or "").strip()
        if not artifact_id:
            return {"error": "delegate_to_critic requires a non-empty artifact_id"}
        scope = args.get("task", "Audit the draft for facts, gaps, risks, and overclaim.")
        task = (
            f"Audit artifact_id={artifact_id}. {scope}\n\n"
            "Start by calling read_artifact with that id. Then run any spine "
            "queries you need to verify or contradict the draft. End with one "
            "write_artifact call carrying the four required headings."
        )
        return _run_delegate(critic_agent, task)

    def _log_decision(args: dict) -> dict:
        eid = append_event(
            agent=NAME,
            kind="decision",
            payload={"summary": args.get("summary", ""), "refs": args.get("refs", [])},
        )
        return {"event_id": eid}

    def _write_artifact(args: dict) -> dict:
        aid = write_artifact(
            agent=NAME,
            kind=args.get("kind", "summary"),
            title=args.get("title", "CoS summary"),
            body_md=args.get("body_md", ""),
            refs=args.get("refs", []),
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
                "as a ref."
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
            description="Optionally write a top-level summary artifact for the operator.",
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
        "log_decision": _log_decision,
        "write_summary_artifact": _write_artifact,
    }
    return Agent(name=NAME, system_prompt=SYSTEM, tools=tools, tool_impls=impls, max_iters=16)


def run_chief(user_input: str, llm: LLMProvider) -> Iterator[AgentEvent]:
    """Run the orchestrator and stream events from CoS *and* delegated specialists."""
    sink = _EventBuffer()
    chief = build_orchestrator(llm, sink)
    for ev in chief.run(user_input, llm):
        # Drain any specialist events buffered before this CoS event
        for spec_ev in sink.drain():
            yield spec_ev
        yield ev
    for spec_ev in sink.drain():
        yield spec_ev
