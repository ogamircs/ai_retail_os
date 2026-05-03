"""Provider-agnostic LLM types + Protocol.

Agents construct unified Tool / Message objects; each provider impl translates to
its native tool-use shape (Anthropic blocks, OpenAI tool_calls, Google function_calls).
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict  # JSON Schema


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Message:
    """Unified message in normalized form.

    role: "user" | "assistant" | "tool"
    content: text string OR list of blocks
    Blocks (dicts):
      - {"type": "text", "text": "..."}
      - {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
      - {"type": "tool_result", "tool_use_id": "...", "content": "..."}
    """

    role: Literal["user", "assistant", "tool"]
    content: str | list[dict]


@dataclass
class AssistantTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_blocks: list[dict] = field(default_factory=list)  # for echoing assistant turn back
    stop_reason: str = ""


class LLMProvider(Protocol):
    name: str
    model: str

    def chat(
        self,
        system: str,
        messages: list[Message],
        tools: list[Tool],
    ) -> AssistantTurn: ...
