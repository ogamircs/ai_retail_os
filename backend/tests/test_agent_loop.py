"""Run-loop hardening unit tests for `app.agents.base.Agent`.

These exercise the generic agent run-loop directly with a scripted fake LLM
(no real provider). They cover two research-backed behaviours added to the
loop:

  * stop-reason awareness — when the model finishes WITHOUT tool calls, the
    loop classifies the provider's native `stop_reason` so a truncated
    (max-tokens / context-exhausted) or refused answer is flagged as
    incomplete rather than silently presented as a clean final answer.
    (Anthropic tool-use docs: the loop "exits on any other stop reason —
    end_turn, max_tokens, stop_sequence, or refusal" — each must be handled.)

  * stuck-loop detection — if the agent calls the same tool with identical
    input more than `repeat_limit` times, the loop stops instead of burning
    every iteration on a no-progress cycle. (Inngest / Anthropic agent-loop
    guidance: "break the loop when the agent repeatedly calls the same tool".)
"""

from __future__ import annotations

import unittest

from app.agents.base import Agent, _classify_stop_reason
from app.llm.base import AssistantTurn, Tool, ToolCall


class _ScriptedLLM:
    """Returns a pre-baked AssistantTurn per `chat()` call, in order.

    If the script is exhausted it keeps returning the last turn — handy for
    'the model never stops calling this tool' scenarios.
    """

    def __init__(self, turns: list[AssistantTurn]):
        self.name = "scripted"
        self.model = "scripted-1"
        self._turns = turns
        self.calls = 0

    def chat(self, system, messages, tools) -> AssistantTurn:  # noqa: ARG002
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        return self._turns[idx]


def _tool(name: str = "noop") -> Tool:
    return Tool(name=name, description="d", input_schema={"type": "object"})


def _events(agent: Agent, llm) -> list:
    return list(agent.run("go", llm))


def _ends(events: list) -> dict:
    end = [e for e in events if e.kind == "agent_end"]
    assert end, f"expected an agent_end event, got kinds {[e.kind for e in events]}"
    return end[-1].data


class ClassifyStopReasonTest(unittest.TestCase):
    """The normalizer must collapse the three provider vocabularies
    (Anthropic / OpenAI / Google) into a small canonical set."""

    def test_clean_stops(self):
        for raw in ("end_turn", "stop_sequence", "stop", "FinishReason.STOP"):
            self.assertEqual(_classify_stop_reason(raw), "end_turn", raw)

    def test_truncation(self):
        # Anthropic max_tokens + model_context_window_exceeded, OpenAI length,
        # Google FinishReason.MAX_TOKENS.
        for raw in (
            "max_tokens",
            "model_context_window_exceeded",
            "length",
            "FinishReason.MAX_TOKENS",
        ):
            self.assertEqual(_classify_stop_reason(raw), "max_tokens", raw)

    def test_refusal(self):
        for raw in ("refusal", "content_filter", "FinishReason.SAFETY", "RECITATION"):
            self.assertEqual(_classify_stop_reason(raw), "refusal", raw)

    def test_empty_and_unknown(self):
        self.assertEqual(_classify_stop_reason(""), "")
        self.assertEqual(_classify_stop_reason("tool_use"), "other")


class StopReasonHandlingTest(unittest.TestCase):
    def test_clean_end_turn_has_no_incomplete_flag(self):
        llm = _ScriptedLLM([AssistantTurn(text="done", stop_reason="end_turn")])
        agent = Agent("A", "sys", [_tool()], {})
        data = _ends(_events(agent, llm))
        self.assertEqual(data["text"], "done")
        self.assertNotIn("note", data)
        self.assertNotIn("incomplete", data)

    def test_max_tokens_truncation_is_flagged(self):
        llm = _ScriptedLLM([AssistantTurn(text="partial", stop_reason="max_tokens")])
        agent = Agent("A", "sys", [_tool()], {})
        data = _ends(_events(agent, llm))
        self.assertTrue(data.get("incomplete"))
        self.assertIn("truncat", data.get("note", "").lower())

    def test_openai_length_truncation_is_flagged(self):
        llm = _ScriptedLLM([AssistantTurn(text="partial", stop_reason="length")])
        agent = Agent("A", "sys", [_tool()], {})
        self.assertTrue(_ends(_events(agent, llm)).get("incomplete"))

    def test_refusal_is_flagged(self):
        llm = _ScriptedLLM([AssistantTurn(text="", stop_reason="refusal")])
        agent = Agent("A", "sys", [_tool()], {})
        data = _ends(_events(agent, llm))
        self.assertTrue(data.get("incomplete"))
        self.assertIn("declin", data.get("note", "").lower())

    def test_unknown_stop_reason_is_not_flagged(self):
        # Absence of a recognised terminal reason should not raise a false alarm.
        llm = _ScriptedLLM([AssistantTurn(text="ok", stop_reason="")])
        agent = Agent("A", "sys", [_tool()], {})
        self.assertNotIn("incomplete", _ends(_events(agent, llm)))


class ToolThenFinishTest(unittest.TestCase):
    def test_tool_call_then_clean_finish(self):
        turns = [
            AssistantTurn(
                tool_calls=[ToolCall(id="t1", name="noop", input={"x": 1})],
                raw_blocks=[{"type": "tool_use", "id": "t1", "name": "noop", "input": {"x": 1}}],
                stop_reason="tool_use",
            ),
            AssistantTurn(text="finished", stop_reason="end_turn"),
        ]
        calls: list = []
        agent = Agent("A", "sys", [_tool()], {"noop": lambda i: calls.append(i) or {"ok": True}})
        events = _events(agent, _ScriptedLLM(turns))
        self.assertEqual(len(calls), 1)
        self.assertEqual(_ends(events)["text"], "finished")
        self.assertNotIn("note", _ends(events))


class StuckLoopTest(unittest.TestCase):
    def _repeating_turn(self, inp: dict) -> AssistantTurn:
        return AssistantTurn(
            tool_calls=[ToolCall(id="t", name="noop", input=inp)],
            raw_blocks=[{"type": "tool_use", "id": "t", "name": "noop", "input": inp}],
            stop_reason="tool_use",
        )

    def test_identical_tool_calls_break_the_loop(self):
        # The model keeps asking for the exact same call forever.
        llm = _ScriptedLLM([self._repeating_turn({"x": 1})])
        exec_count = {"n": 0}

        def impl(_inp):
            exec_count["n"] += 1
            return {"ok": True}

        agent = Agent("A", "sys", [_tool()], {"noop": impl}, max_iters=50, repeat_limit=3)
        data = _ends(_events(agent, llm))
        # Guard stops well before max_iters and does NOT keep re-executing.
        self.assertLessEqual(exec_count["n"], 3)
        self.assertIn("stuck", data.get("note", "").lower())

    def test_distinct_inputs_do_not_trip_the_guard(self):
        # Same tool, different input each call — legitimate progress, capped
        # only by max_iters.
        turns = [self._repeating_turn({"x": i}) for i in range(8)]
        agent = Agent("A", "sys", [_tool()], {"noop": lambda i: {"ok": True}}, max_iters=5, repeat_limit=3)
        data = _ends(_events(agent, _ScriptedLLM(turns)))
        self.assertIn("max iterations", data.get("note", "").lower())
        self.assertNotIn("stuck", data.get("note", "").lower())


class MaxItersTest(unittest.TestCase):
    def test_max_iterations_note_when_never_finishing(self):
        turns = [
            AssistantTurn(
                tool_calls=[ToolCall(id=f"t{i}", name="noop", input={"i": i})],
                raw_blocks=[{"type": "tool_use", "id": f"t{i}", "name": "noop", "input": {"i": i}}],
                stop_reason="tool_use",
            )
            for i in range(10)
        ]
        agent = Agent("A", "sys", [_tool()], {"noop": lambda i: {"ok": True}}, max_iters=3)
        data = _ends(_events(agent, _ScriptedLLM(turns)))
        self.assertIn("max iterations", data.get("note", "").lower())


if __name__ == "__main__":
    unittest.main()
