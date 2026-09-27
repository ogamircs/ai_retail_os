"""Agent run-loop. Tools are Python callables paired with JSON schemas; agents
loop call_llm → execute tool calls → feed results back until stop."""

import json
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from app.llm.base import LLMProvider, Message, Tool


@dataclass
class AgentEvent:
    """Streamed to the SSE client as the agent works."""

    kind: str  # "agent_start" | "text" | "tool_call" | "tool_result" | "agent_end" | "error"
    agent: str
    data: dict


def _classify_stop_reason(raw: str) -> str:
    """Collapse a provider-native `stop_reason` into a canonical category.

    Each provider speaks a different dialect — Anthropic ("max_tokens",
    "refusal", "model_context_window_exceeded"), OpenAI ("length",
    "content_filter"), Google ("FinishReason.MAX_TOKENS", "SAFETY"). We only
    care about three buckets when the model finishes WITHOUT tool calls:

      "end_turn"   — a clean, complete answer
      "max_tokens" — the answer was truncated / the context was exhausted
      "refusal"    — the model declined (refusal / safety / content filter)
      "other"      — a recognised but non-terminal value (e.g. "tool_use")
      ""           — provider reported nothing

    Anything in the truncation/refusal buckets means the final text is NOT a
    trustworthy complete answer and the loop flags it as incomplete.
    """
    s = (raw or "").strip().lower()
    if not s:
        return ""
    if "max_token" in s or s == "length" or "model_context_window" in s:
        return "max_tokens"
    if "refus" in s or "safety" in s or "content_filter" in s or "recitation" in s:
        return "refusal"
    if "end_turn" in s or "stop_sequence" in s or s == "stop" or s.endswith(".stop"):
        return "end_turn"
    return "other"


class Agent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[Tool],
        tool_impls: dict[str, Callable[[dict], dict]],
        max_iters: int = 10,
        repeat_limit: int = 3,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools
        self.tool_impls = tool_impls
        self.max_iters = max_iters
        # Stuck-loop guard: a single (tool name + identical input) signature is
        # allowed to run at most `repeat_limit` times before the loop bails out
        # instead of burning every remaining iteration on a no-progress cycle.
        self.repeat_limit = repeat_limit

    def run(
        self,
        user_input: str,
        llm: LLMProvider,
        cancel: threading.Event | None = None,
    ) -> Iterator[AgentEvent]:
        """Run the loop. Every terminal event (`agent_end` / `error`) carries
        `usage` — provider-reported tokens summed over this run's LLM calls.
        If `cancel` is set (e.g. the SSE client disconnected), the run stops
        before its next LLM call instead of spending tokens for nobody."""
        yield AgentEvent("agent_start", self.name, {"input": user_input})
        messages: list[Message] = [Message(role="user", content=user_input)]
        final_text = ""
        call_counts: Counter[str] = Counter()
        usage = {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0}
        for _ in range(self.max_iters):
            if cancel is not None and cancel.is_set():
                yield AgentEvent(
                    "agent_end",
                    self.name,
                    {
                        "text": final_text,
                        "note": "stopped: cancelled (client disconnected)",
                        "incomplete": True,
                        "cancelled": True,
                        "usage": usage,
                    },
                )
                return
            try:
                turn = llm.chat(self.system_prompt, messages, self.tools)
            except Exception as e:
                yield AgentEvent("error", self.name, {"error": str(e), "usage": usage})
                return
            usage["input_tokens"] += turn.input_tokens
            usage["output_tokens"] += turn.output_tokens
            usage["llm_calls"] += 1

            if turn.text:
                yield AgentEvent("text", self.name, {"text": turn.text})
                final_text = turn.text

            if not turn.tool_calls:
                # The model is done calling tools. Whether this is a *complete*
                # answer depends on why it stopped — a truncated or refused turn
                # must not be presented as a clean final answer.
                data: dict = {"text": final_text, "usage": usage}
                reason = _classify_stop_reason(turn.stop_reason)
                if reason == "max_tokens":
                    data["note"] = "stopped: response truncated (max tokens / context limit reached)"
                    data["incomplete"] = True
                elif reason == "refusal":
                    data["note"] = "stopped: model declined to respond (refusal / safety filter)"
                    data["incomplete"] = True
                yield AgentEvent("agent_end", self.name, data)
                return

            messages.append(Message(role="assistant", content=turn.raw_blocks))
            tool_result_blocks: list[dict] = []
            stuck_tool: str | None = None
            for tc in turn.tool_calls:
                sig = f"{tc.name}:{json.dumps(tc.input, sort_keys=True, default=str)}"
                call_counts[sig] += 1
                yield AgentEvent("tool_call", self.name, {"tool": tc.name, "input": tc.input, "id": tc.id})
                impl = self.tool_impls.get(tc.name)
                if call_counts[sig] > self.repeat_limit:
                    # Don't re-execute — feed back an error block so the model
                    # sees the suppression, then end the run after this turn.
                    stuck_tool = tc.name
                    result = {
                        "error": (
                            f"stuck-loop guard: '{tc.name}' already called "
                            f"{self.repeat_limit} times with identical input; not re-executed"
                        )
                    }
                elif impl is None:
                    result = {"error": f"unknown tool: {tc.name}"}
                else:
                    try:
                        result = impl(tc.input)
                    except Exception as e:
                        result = {"error": str(e)}
                yield AgentEvent("tool_result", self.name, {"tool": tc.name, "result": result, "id": tc.id})
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "name": tc.name,
                        "content": json.dumps(result),
                    }
                )
            messages.append(Message(role="tool", content=tool_result_blocks))

            if stuck_tool is not None:
                yield AgentEvent(
                    "agent_end",
                    self.name,
                    {
                        "text": final_text,
                        "note": f"stopped: stuck loop (repeated identical call to '{stuck_tool}')",
                        "usage": usage,
                    },
                )
                return

        yield AgentEvent(
            "agent_end",
            self.name,
            {"text": final_text, "note": "max iterations reached", "usage": usage},
        )
