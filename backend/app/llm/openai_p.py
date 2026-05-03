import json

from openai import OpenAI

from app.llm.base import AssistantTurn, Message, Tool, ToolCall


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def _to_native_messages(self, system: str, messages: list[Message]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "user":
                content = m.content if isinstance(m.content, str) else self._stringify(m.content)
                out.append({"role": "user", "content": content})
            elif m.role == "assistant":
                if isinstance(m.content, list):
                    text_parts = []
                    tool_calls = []
                    for b in m.content:
                        if b.get("type") == "text":
                            text_parts.append(b.get("text", ""))
                        elif b.get("type") == "tool_use":
                            tool_calls.append(
                                {
                                    "id": b["id"],
                                    "type": "function",
                                    "function": {
                                        "name": b["name"],
                                        "arguments": json.dumps(b.get("input", {})),
                                    },
                                }
                            )
                    msg: dict = {"role": "assistant", "content": "\n".join(text_parts) or None}
                    if tool_calls:
                        msg["tool_calls"] = tool_calls
                    out.append(msg)
                else:
                    out.append({"role": "assistant", "content": m.content})
            elif m.role == "tool":
                blocks = m.content if isinstance(m.content, list) else []
                for b in blocks:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        content = b.get("content", "")
                        if not isinstance(content, str):
                            content = json.dumps(content)
                        out.append(
                            {
                                "role": "tool",
                                "tool_call_id": b["tool_use_id"],
                                "content": content,
                            }
                        )
        return out

    @staticmethod
    def _stringify(content: list[dict]) -> str:
        parts = []
        for b in content:
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
        return "\n".join(parts)

    def chat(
        self, system: str, messages: list[Message], tools: list[Tool]
    ) -> AssistantTurn:
        native_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]
        kwargs: dict = {
            "model": self.model,
            "messages": self._to_native_messages(system, messages),
            "max_tokens": 4096,
        }
        if native_tools:
            kwargs["tools"] = native_tools

        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message
        text = msg.content or ""
        tool_calls: list[ToolCall] = []
        raw_blocks: list[dict] = []
        if text:
            raw_blocks.append({"type": "text", "text": text})
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))
                raw_blocks.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.function.name, "input": args}
                )
        return AssistantTurn(
            text=text,
            tool_calls=tool_calls,
            raw_blocks=raw_blocks,
            stop_reason=choice.finish_reason or "",
        )
