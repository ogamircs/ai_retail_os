from anthropic import Anthropic
from app.llm.base import LLMProvider, Tool, Message, AssistantTurn, ToolCall


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def _to_native_messages(self, messages: list[Message]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m.role == "tool":
                # tool result goes into a user message with tool_result block
                blocks = m.content if isinstance(m.content, list) else [m.content]
                tr = []
                for b in blocks:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tr.append(b)
                if tr:
                    out.append({"role": "user", "content": tr})
                continue
            content = m.content
            if isinstance(content, list):
                out.append({"role": m.role, "content": content})
            else:
                out.append({"role": m.role, "content": content})
        return out

    def chat(
        self, system: str, messages: list[Message], tools: list[Tool]
    ) -> AssistantTurn:
        native_tools = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        kwargs = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system,
            "messages": self._to_native_messages(messages),
        }
        if native_tools:
            kwargs["tools"] = native_tools

        resp = self.client.messages.create(**kwargs)

        text = ""
        tool_calls: list[ToolCall] = []
        raw_blocks: list[dict] = []
        for block in resp.content:
            if block.type == "text":
                text += block.text
                raw_blocks.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input)))
                raw_blocks.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": dict(block.input),
                    }
                )
        return AssistantTurn(
            text=text,
            tool_calls=tool_calls,
            raw_blocks=raw_blocks,
            stop_reason=resp.stop_reason or "",
        )
