import json
import uuid

from google import genai
from google.genai import types as gtypes

from app.llm.base import AssistantTurn, Message, Tool, ToolCall


class GoogleProvider:
    name = "google"

    def __init__(self, api_key: str, model: str):
        self.client = genai.Client(api_key=api_key)
        self.model = model

    @staticmethod
    def _clean_schema(schema: dict) -> dict:
        """Strip unsupported JSON-schema fields for Gemini."""
        if not isinstance(schema, dict):
            return schema
        allowed = {"type", "properties", "required", "items", "enum", "description", "format"}
        out = {}
        for k, v in schema.items():
            if k not in allowed:
                continue
            if k == "properties" and isinstance(v, dict):
                out[k] = {pk: GoogleProvider._clean_schema(pv) for pk, pv in v.items()}
            elif k == "items":
                out[k] = GoogleProvider._clean_schema(v)
            else:
                out[k] = v
        return out

    def _build_contents(self, messages: list[Message]) -> list[gtypes.Content]:
        contents: list[gtypes.Content] = []
        for m in messages:
            if m.role == "user":
                if isinstance(m.content, str):
                    contents.append(
                        gtypes.Content(role="user", parts=[gtypes.Part(text=m.content)])
                    )
                else:
                    parts = []
                    for b in m.content:
                        if b.get("type") == "text":
                            parts.append(gtypes.Part(text=b.get("text", "")))
                    if parts:
                        contents.append(gtypes.Content(role="user", parts=parts))
            elif m.role == "assistant":
                parts = []
                if isinstance(m.content, list):
                    for b in m.content:
                        if b.get("type") == "text" and b.get("text"):
                            parts.append(gtypes.Part(text=b["text"]))
                        elif b.get("type") == "tool_use":
                            parts.append(
                                gtypes.Part(
                                    function_call=gtypes.FunctionCall(
                                        name=b["name"], args=b.get("input", {})
                                    )
                                )
                            )
                else:
                    if m.content:
                        parts.append(gtypes.Part(text=m.content))
                if parts:
                    contents.append(gtypes.Content(role="model", parts=parts))
            elif m.role == "tool":
                blocks = m.content if isinstance(m.content, list) else []
                parts = []
                for b in blocks:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        content = b.get("content", "")
                        if isinstance(content, str):
                            try:
                                response_obj = json.loads(content)
                            except json.JSONDecodeError:
                                response_obj = {"result": content}
                        else:
                            response_obj = content
                        if not isinstance(response_obj, dict):
                            response_obj = {"result": response_obj}
                        parts.append(
                            gtypes.Part(
                                function_response=gtypes.FunctionResponse(
                                    name=b.get("name", "tool"), response=response_obj
                                )
                            )
                        )
                if parts:
                    contents.append(gtypes.Content(role="user", parts=parts))
        return contents

    def chat(
        self, system: str, messages: list[Message], tools: list[Tool]
    ) -> AssistantTurn:
        function_decls = [
            gtypes.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=self._clean_schema(t.input_schema),
            )
            for t in tools
        ]
        config = gtypes.GenerateContentConfig(
            system_instruction=system,
            tools=[gtypes.Tool(function_declarations=function_decls)] if function_decls else None,
        )
        resp = self.client.models.generate_content(
            model=self.model,
            contents=self._build_contents(messages),
            config=config,
        )
        text = ""
        tool_calls: list[ToolCall] = []
        raw_blocks: list[dict] = []
        # Track tool name → id mapping so subsequent tool_result events can be paired
        if resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts:
            for part in resp.candidates[0].content.parts:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text:
                    text += part_text
                    raw_blocks.append({"type": "text", "text": part_text})
                fc = getattr(part, "function_call", None)
                if fc and fc.name:
                    call_id = f"call_{uuid.uuid4().hex[:12]}"
                    args = dict(fc.args) if fc.args else {}
                    tool_calls.append(ToolCall(id=call_id, name=fc.name, input=args))
                    raw_blocks.append(
                        {"type": "tool_use", "id": call_id, "name": fc.name, "input": args}
                    )
        return AssistantTurn(
            text=text,
            tool_calls=tool_calls,
            raw_blocks=raw_blocks,
            stop_reason=str(resp.candidates[0].finish_reason) if resp.candidates else "",
        )
