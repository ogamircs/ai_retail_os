"""Agent run-loop. Tools are Python callables paired with JSON schemas; agents
loop call_llm → execute tool calls → feed results back until stop."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Callable
from app.llm.base import LLMProvider, Tool, Message, ToolCall


@dataclass
class AgentEvent:
    """Streamed to the SSE client as the agent works."""

    kind: str  # "agent_start" | "text" | "tool_call" | "tool_result" | "agent_end" | "error"
    agent: str
    data: dict


class Agent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[Tool],
        tool_impls: dict[str, Callable[[dict], dict]],
        max_iters: int = 10,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools
        self.tool_impls = tool_impls
        self.max_iters = max_iters

    def run(self, user_input: str, llm: LLMProvider) -> Iterator[AgentEvent]:
        yield AgentEvent("agent_start", self.name, {"input": user_input})
        messages: list[Message] = [Message(role="user", content=user_input)]
        final_text = ""
        for _ in range(self.max_iters):
            try:
                turn = llm.chat(self.system_prompt, messages, self.tools)
            except Exception as e:
                yield AgentEvent("error", self.name, {"error": str(e)})
                return

            if turn.text:
                yield AgentEvent("text", self.name, {"text": turn.text})
                final_text = turn.text

            if not turn.tool_calls:
                yield AgentEvent("agent_end", self.name, {"text": final_text})
                return

            messages.append(Message(role="assistant", content=turn.raw_blocks))
            tool_result_blocks: list[dict] = []
            for tc in turn.tool_calls:
                impl = self.tool_impls.get(tc.name)
                yield AgentEvent("tool_call", self.name, {"tool": tc.name, "input": tc.input, "id": tc.id})
                if impl is None:
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

        yield AgentEvent("agent_end", self.name, {"text": final_text, "note": "max iterations reached"})
