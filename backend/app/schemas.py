from typing import Any, Literal

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


class EventOut(BaseModel):
    id: int
    ts: str
    agent: str
    kind: str
    sku: str | None
    payload: dict
    artifact_id: str | None


class ArtifactMeta(BaseModel):
    id: str
    agent: str
    ts: str
    kind: str
    title: str
    refs: list[str] = []


class ConfigOut(BaseModel):
    provider: str
    model: str
    has_key: bool


class ConfigSet(BaseModel):
    provider: Literal["anthropic", "openai", "google"]


class StreamEvent(BaseModel):
    type: str
    data: dict[str, Any]
