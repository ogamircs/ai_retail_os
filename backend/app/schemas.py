from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


# Pydantic V2 default: ignore unknown keys. Routes return raw dicts
# that may carry adapter-specific extras; we want the response model
# to document the *contract* without rejecting valid additions.
class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ChatRequest(BaseModel):
    message: str


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


# ---- Integration surface --------------------------------------------------

class IntegrationSystem(_Lenient):
    system_id: str
    display_name: str
    domain: str
    enabled: bool
    configured: bool
    mode: Literal["mock", "connected"]
    last_status: str | None = None
    last_sync_ts: str | None = None
    last_error: str | None = None
    docs_url: str
    metadata: dict[str, Any] = {}


class IntegrationSystemsResponse(_Lenient):
    systems: list[IntegrationSystem]


class SyncRun(_Lenient):
    id: int
    system_id: str
    started_at: str
    finished_at: str | None = None
    status: str
    records_read: int = 0
    records_written: int = 0
    error: str | None = None
    summary: dict[str, Any] = {}


class SyncRunsResponse(_Lenient):
    sync_runs: list[SyncRun]


class OutboxAction(_Lenient):
    id: int
    ts: str
    system_id: str
    action_queue_id: int | None = None
    agent: str
    action_type: str
    title: str
    status: str
    external_domain: str
    external_id: str | None = None
    payload: dict[str, Any] = {}
    result: dict[str, Any] = {}
    requires_approval: bool = True


# ---- Improvement Auditor --------------------------------------------------

class ImprovementRun(_Lenient):
    id: str
    started_ts: str
    ended_ts: str | None = None
    status: Literal["running", "ok", "error"]
    summary: dict[str, Any] = {}
    error: str | None = None


class ImprovementRunsResponse(_Lenient):
    runs: list[ImprovementRun]


class ImprovementSuggestion(_Lenient):
    id: int
    run_id: str
    area: str
    severity: Literal["high", "medium", "low"]
    title: str
    body_md: str
    action_hint: str
    status: Literal["open", "accepted", "dismissed"]
    refs: list[str] = []
    ts: str


class ImprovementSuggestionsResponse(_Lenient):
    suggestions: list[ImprovementSuggestion]


# ---- Wiki -----------------------------------------------------------------

class WikiPage(_Lenient):
    slug: str
    title: str
    body_md: str
    owner_agent: str
    status: Literal["draft", "published", "deprecated"]
    version: int
    updated_ts: str
    refs: list[str] = []
    pinned: bool = False


class WikiPagesResponse(_Lenient):
    pages: list[WikiPage]


# ---- Brain (GBrain MCP) ---------------------------------------------------

class BrainStatus(_Lenient):
    configured: bool
    reachable: bool
    mock: bool
    pages_count: int
    endpoint: str | None = None
    error: str | None = None


# ---- DSPy job tracking ----------------------------------------------------

class DspyAgentEntry(_Lenient):
    slug: str
    prod: str | None = None
    staging: str | None = None


class DspyAgentsResponse(_Lenient):
    agents: list[DspyAgentEntry]


class DspyJob(_Lenient):
    id: str
    agent: str | None = None
    auto_promote: bool = False
    status: Literal["running", "ok", "error", "cancelled"]
    started_at: str
    ended_at: str | None = None
    summary: dict[str, Any] | None = None
    error: str | None = None


class DspyJobsResponse(_Lenient):
    jobs: list[DspyJob]


# ---- Mesh status ----------------------------------------------------------

class MeshConfig(_Lenient):
    max_revision_rounds: int
    max_critic_per_draft: int
    turn_token_budget: int
    turn_wallclock_seconds: int


class MeshStatus(_Lenient):
    enabled: bool
    config: MeshConfig
    recent_downgrade: dict[str, Any] | None = None
    downgrade_count_window: int = 0
    window_seconds: int
