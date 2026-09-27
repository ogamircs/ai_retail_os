"""Real token usage feeding the mesh budget, and turn cancellation when the
SSE client disconnects."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app.agents.base import Agent, AgentEvent
from app.agents.chief_of_staff import _MeshState, build_orchestrator
from app.config import mesh as mesh_settings
from app.llm.base import AssistantTurn, Tool, ToolCall
from app.spine import db
from app.spine import events as ev_store
from app.substrate import seed


class _ScriptedLLM:
    """Returns one pre-baked AssistantTurn per call, in order. `on_call`
    runs before each return (used to flip the cancel flag mid-run)."""

    def __init__(self, turns: list[AssistantTurn], on_call=None):
        self.name = "scripted"
        self.model = "scripted-1"
        self._turns = turns
        self._on_call = on_call
        self.calls = 0

    def chat(self, system, messages, tools) -> AssistantTurn:  # noqa: ARG002
        if self.calls >= len(self._turns):
            raise AssertionError(f"unexpected LLM call #{self.calls + 1}")
        turn = self._turns[self.calls]
        self.calls += 1
        if self._on_call:
            self._on_call(self.calls)
        return turn


class _RaisingLLM:
    def __init__(self, first: AssistantTurn):
        self.name, self.model = "raising", "raising-1"
        self._first = first
        self.calls = 0

    def chat(self, *_a, **_kw) -> AssistantTurn:
        self.calls += 1
        if self.calls == 1:
            return self._first
        raise RuntimeError("provider exploded")


def _tool_turn(name: str, args: dict | None = None, *, inp: int = 0, out: int = 0) -> AssistantTurn:
    tc = ToolCall(id=f"c-{name}", name=name, input=args or {})
    return AssistantTurn(
        tool_calls=[tc],
        raw_blocks=[{"type": "tool_use", "id": tc.id, "name": name, "input": tc.input}],
        stop_reason="tool_use",
        input_tokens=inp,
        output_tokens=out,
    )


def _end_turn(text: str = "done", *, inp: int = 0, out: int = 0) -> AssistantTurn:
    return AssistantTurn(
        text=text, stop_reason="end_turn", input_tokens=inp, output_tokens=out
    )


def _agent() -> Agent:
    tool = Tool(name="noop", description="d", input_schema={"type": "object"})
    return Agent(
        name="T", system_prompt="s", tools=[tool], tool_impls={"noop": lambda _a: {"ok": True}}
    )


def _last(events: list[AgentEvent], kind: str) -> dict:
    matches = [e for e in events if e.kind == kind]
    assert matches, f"no {kind} event in {[e.kind for e in events]}"
    return matches[-1].data


class AgentUsageTest(unittest.TestCase):
    def test_usage_is_summed_across_calls(self):
        llm = _ScriptedLLM(
            [_tool_turn("noop", inp=100, out=20), _end_turn(inp=150, out=30)]
        )
        end = _last(list(_agent().run("go", llm)), "agent_end")
        self.assertEqual(
            end["usage"], {"input_tokens": 250, "output_tokens": 50, "llm_calls": 2}
        )

    def test_error_event_carries_usage_so_far(self):
        llm = _RaisingLLM(_tool_turn("noop", inp=40, out=7))
        err = _last(list(_agent().run("go", llm)), "error")
        self.assertIn("provider exploded", err["error"])
        self.assertEqual(err["usage"]["output_tokens"], 7)
        self.assertEqual(err["usage"]["llm_calls"], 1)


class AgentCancelTest(unittest.TestCase):
    def test_preset_cancel_makes_no_llm_call(self):
        cancel = threading.Event()
        cancel.set()
        llm = _ScriptedLLM([])
        end = _last(list(_agent().run("go", llm, cancel=cancel)), "agent_end")
        self.assertEqual(llm.calls, 0)
        self.assertTrue(end["cancelled"])
        self.assertTrue(end["incomplete"])

    def test_cancel_mid_run_stops_before_next_llm_call(self):
        cancel = threading.Event()
        # Client disconnects while the first LLM call is in flight.
        llm = _ScriptedLLM(
            [_tool_turn("noop", out=5), _end_turn()],
            on_call=lambda n: cancel.set() if n == 1 else None,
        )
        events = list(_agent().run("go", llm, cancel=cancel))
        self.assertEqual(llm.calls, 1)
        end = _last(events, "agent_end")
        self.assertTrue(end["cancelled"])
        self.assertEqual(end["usage"]["output_tokens"], 5)


class ProviderUsageMappingTest(unittest.TestCase):
    """Each provider maps its SDK's usage metadata onto AssistantTurn.
    The SDK call is stubbed with a fake response; no network."""

    def test_anthropic_counts_cache_tokens_as_input(self):
        from types import SimpleNamespace as NS

        from app.llm.anthropic_p import AnthropicProvider

        p = AnthropicProvider("k", "m")
        resp = NS(
            content=[NS(type="text", text="hi")],
            stop_reason="end_turn",
            usage=NS(
                input_tokens=10,
                cache_creation_input_tokens=200,
                cache_read_input_tokens=3000,
                output_tokens=42,
            ),
        )
        with mock.patch.object(p.client.messages, "create", return_value=resp):
            turn = p.chat("s", [], [])
        self.assertEqual((turn.input_tokens, turn.output_tokens), (3210, 42))

    def test_openai_maps_prompt_and_completion_tokens(self):
        from types import SimpleNamespace as NS

        from app.llm.openai_p import OpenAIProvider

        p = OpenAIProvider("k", "m")
        resp = NS(
            choices=[NS(message=NS(content="hi", tool_calls=None), finish_reason="stop")],
            usage=NS(prompt_tokens=77, completion_tokens=9),
        )
        with mock.patch.object(p.client.chat.completions, "create", return_value=resp):
            turn = p.chat("s", [], [])
        self.assertEqual((turn.input_tokens, turn.output_tokens), (77, 9))

    def test_google_counts_thinking_and_tool_prompt_tokens(self):
        from types import SimpleNamespace as NS

        from app.llm.google_p import GoogleProvider

        p = GoogleProvider("k", "m")
        resp = NS(
            candidates=[NS(content=NS(parts=[NS(text="hi", function_call=None)]), finish_reason="STOP")],
            usage_metadata=NS(
                prompt_token_count=100,
                tool_use_prompt_token_count=5,
                candidates_token_count=20,
                thoughts_token_count=300,
            ),
        )
        with mock.patch.object(p.client.models, "generate_content", return_value=resp):
            turn = p.chat("s", [], [])
        self.assertEqual((turn.input_tokens, turn.output_tokens), (105, 320))

    def test_missing_usage_reports_zero(self):
        from types import SimpleNamespace as NS

        from app.llm.openai_p import OpenAIProvider

        p = OpenAIProvider("k", "m")
        resp = NS(
            choices=[NS(message=NS(content="hi", tool_calls=None), finish_reason="stop")],
            usage=None,
        )
        with mock.patch.object(p.client.chat.completions, "create", return_value=resp):
            turn = p.chat("s", [], [])
        self.assertEqual((turn.input_tokens, turn.output_tokens), (0, 0))


class MeshUsageBudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_add_usage_prefers_reported_tokens_over_estimate(self):
        s = _MeshState()
        s.add_usage({"input_tokens": 900, "output_tokens": 400}, "two words")
        self.assertEqual(s.tokens_used, 400)
        self.assertEqual(s.input_tokens_used, 900)

    def test_add_usage_falls_back_to_estimate_without_reported_tokens(self):
        s = _MeshState()
        s.add_usage({"input_tokens": 0, "output_tokens": 0}, "one two three four five six seven eight nine ten")
        self.assertEqual(s.tokens_used, 13)  # 10 words * 1.3

    def test_heavy_specialist_with_short_answer_trips_budget(self):
        """Regression for the word-count estimate: an Analyst run whose real
        output is over budget but whose final text is two words must still
        downgrade the next reviewed delegation. Under the old estimate this
        counted as ~2 tokens and never tripped."""
        over = mesh_settings.turn_token_budget + 1
        llm = _ScriptedLLM(
            [
                # Analyst: one call, tiny final text, huge real output.
                _end_turn("all good", inp=5000, out=over),
                # Pricing: writes a draft, then finishes.
                _tool_turn(
                    "write_artifact",
                    {"kind": "plan", "title": "Markdown plan", "body_md": "Mark SKU-001 down 10%."},
                    out=50,
                ),
                _end_turn("drafted", out=10),
            ]
        )
        sink_events: list = []

        class _Sink:
            def add(self, ev):
                sink_events.append(ev)

            def drain(self):
                return []

        orch = build_orchestrator(llm, _Sink())  # type: ignore[arg-type]
        orch.tool_impls["delegate_to_analyst"]({"task": "check sales"})
        result = orch.tool_impls["delegate_to_pricing"]({"task": "plan a markdown"})

        # Budget tripped before the Critic ran: no further LLM calls.
        self.assertEqual(llm.calls, 3)
        self.assertEqual(result.get("final_artifact"), result.get("draft_artifact"))
        downgrades = [e for e in ev_store.list_events(limit=50) if e["kind"] == "mesh_downgrade"]
        self.assertEqual(len(downgrades), 1)
        payload = downgrades[0]["payload"]
        self.assertEqual(payload["reason"], "budget_exhausted_before_review")
        self.assertEqual(payload["tokens_used"], over + 60)
        self.assertEqual(payload["input_tokens_used"], 5000)


class RunChiefCancelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "spine.db"
        seed.seed()

    def tearDown(self):
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_cancelled_turn_skips_post_turn_processing(self):
        from app.agents import chief_of_staff

        cancel = threading.Event()
        cancel.set()
        with (
            mock.patch.object(chief_of_staff, "_wiki_auto_publish_clean_drafts") as publish,
            mock.patch.object(chief_of_staff, "_fire_brain_ingest") as ingest,
            mock.patch("app.agents.wiki_curator.curate_turn") as curate,
        ):
            events = list(chief_of_staff.run_chief("hi", _ScriptedLLM([]), cancel=cancel))
        self.assertTrue(_last(events, "agent_end")["cancelled"])
        publish.assert_not_called()
        ingest.assert_not_called()
        curate.assert_not_called()


class ChatRouteDisconnectTest(unittest.TestCase):
    def test_closing_the_stream_sets_cancel(self):
        """sse-starlette closes the body generator on client disconnect; the
        route must flag the producer thread so the turn stops."""
        from app.routes import chat as chat_route
        from app.schemas import ChatRequest

        seen: dict = {}

        def _fake_run_chief(message, llm, cancel=None):
            seen["cancel"] = cancel
            for i in range(200):
                if cancel is not None and cancel.is_set():
                    seen["stopped_at"] = i
                    return
                yield AgentEvent("text", "Chief of Staff", {"text": f"chunk {i}"})
                cancel.wait(0.01)  # type: ignore[union-attr]

        async def _drive():
            resp = await chat_route.chat(ChatRequest(message="hi"))
            gen = resp.body_iterator
            first = await gen.__anext__()  # type: ignore[attr-defined]
            await gen.aclose()  # type: ignore[attr-defined]
            return first

        with (
            mock.patch.object(chat_route, "get_provider", return_value=object()),
            mock.patch.object(chat_route, "run_chief", _fake_run_chief),
        ):
            first = asyncio.run(_drive())

        assert isinstance(first, dict)
        self.assertEqual(first["event"], "agent")
        self.assertTrue(seen["cancel"].is_set())
        # Stopped early rather than running all 200 chunks.
        self.assertLess(seen["stopped_at"], 200)


if __name__ == "__main__":
    unittest.main()
