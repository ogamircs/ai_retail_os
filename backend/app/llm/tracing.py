"""MLflow tracing wrapper for the LLM providers (Track 4 M2).

Goals:
  * One MLflow run per operator turn.
  * Nested runs per specialist delegate (Pricing draft, Marketing draft, …).
  * Further nesting per critic round, per peer-review, per revision.
  * Tags `agent`, `phase` (draft/critique/revision/final), `provider`,
    `model` so the tracking UI can slice/dice without a custom view.

Implementation notes:
  * MLflow is an *optional* dependency. When `MLFLOW_TRACE_ENABLED` is
    unset OR when `import mlflow` fails, every helper here becomes a
    no-op so the cockpit demo path runs identically to before. The
    cost of "import mlflow" the first time it's hit is ~150ms; we
    delay the import to the first run-open so a pure-LLM call without
    a turn context never pays it.
  * The mesh state is stored in a `contextvars.ContextVar` so the
    chief_of_staff orchestrator can open / close runs without
    threading per-turn IDs through every delegate signature. Async
    boundaries (the SSE chat handler, asyncio.to_thread) inherit the
    context.
  * Each LLM call (`TracingProvider.chat`) auto-opens a leaf run
    nested under whatever the caller has on the stack. The leaf
    captures prompt size, tool count, response text length, stop
    reason, wall-clock latency. Token counts arrive as `metrics`
    when the provider exposes them (Anthropic + OpenAI do; Google's
    SDK doesn't).
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from app.llm.base import AssistantTurn, LLMProvider, Message, Tool

# ----------------------------------------------------------------------
# Optional mlflow loader
# ----------------------------------------------------------------------

_mlflow_module: Any | None = None
_mlflow_load_attempted: bool = False


def _try_import_mlflow() -> Any | None:
    """Lazy-import. Caches the module (or None) after the *first
    successful* import attempt so the import cost is paid once.

    Deliberately *does not* cache the disabled state. A long-lived
    process (a worker, a test harness) can flip MLFLOW_TRACE_ENABLED
    on at runtime — if we cached `None` the first time tracing was
    off, every subsequent turn would silently stay un-traced even
    after the operator turned it on. Cheap to re-check the env var on
    every chat() call.
    """
    global _mlflow_module, _mlflow_load_attempted
    if _mlflow_load_attempted:
        return _mlflow_module
    if not is_tracing_enabled():
        # Don't poison the cache — when the operator flips the flag,
        # the next call should retry the import.
        return None
    try:
        import mlflow  # type: ignore

        # Only honour env-var-driven config so we never clobber state
        # the *caller* has already set (e.g. run_eval.py calls
        # `mlflow.set_experiment(f"eval/{scenario}/{sha}")` before any
        # turn fires; if we then unconditionally set the experiment
        # back to a default, eval runs land in the wrong namespace).
        # URI: set only when MLFLOW_TRACKING_URI is in env. Experiment:
        # set only when MLFLOW_EXPERIMENT_NAME is in env. Without
        # either, we fall through to whatever mlflow's globals already
        # carry (the caller's choice, or MLflow's "Default" experiment).
        uri = os.getenv("MLFLOW_TRACKING_URI")
        if uri:
            mlflow.set_tracking_uri(uri)
        exp = os.getenv("MLFLOW_EXPERIMENT_NAME")
        if exp:
            mlflow.set_experiment(exp)
        _mlflow_module = mlflow
        _mlflow_load_attempted = True
    except Exception:
        # ImportError (mlflow extra not installed) or any setup error
        # — cache the failure so we don't re-pay the import cost on
        # every subsequent chat() call. Operators can recover by
        # `pip install -e .[mlflow]` and restarting the process; the
        # cache is per-process.
        _mlflow_module = None
        _mlflow_load_attempted = True
    return _mlflow_module


def is_tracing_enabled() -> bool:
    return os.getenv("MLFLOW_TRACE_ENABLED", "0").strip().lower() in ("1", "true", "yes")


# ----------------------------------------------------------------------
# Run stack — a ContextVar holding the current ancestor of MLflow runs
# ----------------------------------------------------------------------


@dataclass
class _RunFrame:
    run_id: str
    name: str
    phase: str  # "turn" | "delegate" | "critic" | "revision" | "peer_review" | "leaf"
    agent: str | None
    # Per-frame counters for `log_event` so the metric shows the
    # *running total* across the run rather than a constant 1. Mlflow's
    # log_metric tracks history, but the run summary takes the latest
    # value — so writing the running total each time gives operators
    # the count they expect on the run row.
    event_counters: dict[str, int] = field(default_factory=dict)


_stack: contextvars.ContextVar[tuple[_RunFrame, ...]] = contextvars.ContextVar(
    "_mlflow_run_stack", default=()
)


@contextlib.contextmanager
def _open_nested_run(name: str, phase: str, agent: str | None, tags: dict[str, str]) -> Iterator[_RunFrame | None]:
    mlflow = _try_import_mlflow()
    if mlflow is None:
        # No-op path — still yield None so consumers can branch.
        yield None
        return
    parent_stack = _stack.get()
    is_root = len(parent_stack) == 0
    try:
        run = mlflow.start_run(run_name=name, nested=not is_root)
    except Exception:
        # Tracking server unreachable / auth bad — degrade silently.
        yield None
        return
    frame = _RunFrame(run_id=run.info.run_id, name=name, phase=phase, agent=agent)
    token = _stack.set(parent_stack + (frame,))
    try:
        try:
            mlflow.set_tags({"phase": phase, **tags})
            if agent:
                mlflow.set_tag("agent", agent)
        except Exception:
            pass
        yield frame
    finally:
        try:
            mlflow.end_run()
        except Exception:
            pass
        _stack.reset(token)


# ----------------------------------------------------------------------
# Public context managers used by chief_of_staff
# ----------------------------------------------------------------------


@contextlib.contextmanager
def turn_run(operator_input: str, provider: str, model: str) -> Iterator[None]:
    """Open the parent run for a single operator turn. Inside the
    `with` block, all delegate / critic nested runs pile underneath."""
    snippet = (operator_input or "").strip().replace("\n", " ")
    name = f"turn::{snippet[:48]}"
    tags = {"provider": provider, "model": model}
    with _open_nested_run(name=name, phase="turn", agent="Chief of Staff", tags=tags) as frame:
        mlflow = _try_import_mlflow()
        if mlflow is not None and frame is not None:
            try:
                mlflow.log_text(operator_input or "", "operator_prompt.txt")
            except Exception:
                pass
        yield


@contextlib.contextmanager
def delegate_run(specialist: str, phase: str = "delegate") -> Iterator[None]:
    """Open a nested run scoped to one specialist delegation.
    `phase` lets the chief discriminate between a fresh delegation
    (`delegate`), a Critic invocation (`critic`), a peer review
    (`peer_review`), and a revision pass (`revision`)."""
    name = f"{phase}::{specialist}"
    with _open_nested_run(name=name, phase=phase, agent=specialist, tags={}):
        yield


def log_artifact_link(artifact_id: str, kind: str, stage: str) -> None:
    """Drop a small JSON artifact under the current run that links the
    MLflow trace to the spine.db artifact id. Lets an operator clicking
    through a flagged eval run jump back to the cockpit's own audit."""
    mlflow = _try_import_mlflow()
    if mlflow is None or not _stack.get():
        return
    try:
        mlflow.log_dict(
            {"artifact_id": artifact_id, "kind": kind, "stage": stage},
            f"spine_artifact_{artifact_id}.json",
        )
    except Exception:
        pass


def log_event(kind: str, payload: dict[str, Any]) -> None:
    """Log a structured spine-event-equivalent into the active run.
    Used by chief_of_staff to mirror `mesh_downgrade` events into
    MLflow so the metric "downgrades per session" is queryable from
    the tracking UI without a separate ETL."""
    mlflow = _try_import_mlflow()
    if mlflow is None:
        return
    stack = _stack.get()
    if not stack:
        return
    try:
        mlflow.log_dict({"kind": kind, **payload}, f"event_{kind}_{int(time.time() * 1000)}.json")
        # Walk every active frame and bump the running total — that way
        # multiple events of the same kind in a single run accumulate
        # into a usable counter (mlflow's run-summary value is the
        # *latest* logged metric, so we have to write the cumulative
        # total each time, not a constant 1). Updating every ancestor
        # ensures parent runs (turn / delegate) also reflect events
        # that fire inside their nested children.
        metric_key = f"event.{kind}"
        for frame in stack:
            frame.event_counters[kind] = frame.event_counters.get(kind, 0) + 1
            mlflow.log_metric(
                metric_key,
                float(frame.event_counters[kind]),
                run_id=frame.run_id,
            )
    except Exception:
        pass


# ----------------------------------------------------------------------
# Provider wrapper
# ----------------------------------------------------------------------


class TracingProvider:
    """LLMProvider decorator that opens a leaf run per chat() call.

    Wraps any provider conforming to LLMProvider Protocol. When tracing
    is disabled the wrapper is still constructed but every chat() call
    short-circuits to the underlying provider with no overhead.
    """

    def __init__(self, inner: LLMProvider):
        self._inner = inner
        self.name = getattr(inner, "name", "unknown")
        self.model = getattr(inner, "model", "unknown")

    def chat(
        self,
        system: str,
        messages: list[Message],
        tools: list[Tool],
    ) -> AssistantTurn:
        if not is_tracing_enabled() or _try_import_mlflow() is None:
            return self._inner.chat(system, messages, tools)
        return self._chat_traced(system, messages, tools)

    def _chat_traced(
        self,
        system: str,
        messages: list[Message],
        tools: list[Tool],
    ) -> AssistantTurn:
        mlflow = _try_import_mlflow()
        assert mlflow is not None  # is_tracing_enabled gated above
        # Compose a brief leaf-run name. Use the most recent user-facing
        # message to anchor the run in the UI.
        last_text = ""
        for m in reversed(messages):
            if isinstance(m.content, str) and m.content.strip():
                last_text = m.content
                break
        name = f"llm::{self.name}::{(last_text or '').strip()[:48]}"
        with _open_nested_run(name=name, phase="leaf", agent=None, tags={
            "provider": self.name,
            "model": self.model,
            "tool_count": str(len(tools)),
            "message_count": str(len(messages)),
        }):
            t0 = time.monotonic()
            err: BaseException | None = None
            turn: AssistantTurn | None = None
            try:
                turn = self._inner.chat(system, messages, tools)
                return turn
            except BaseException as e:  # pragma: no cover - rare
                err = e
                raise
            finally:
                wall_ms = int((time.monotonic() - t0) * 1000)
                try:
                    mlflow.log_metric("latency_ms", wall_ms)
                except Exception:
                    pass
                if turn is not None:
                    try:
                        mlflow.log_metric("response_chars", len(turn.text or ""))
                        mlflow.log_metric("tool_calls", len(turn.tool_calls))
                        mlflow.log_text(turn.text or "", "response_text.txt")
                        mlflow.log_dict(
                            {
                                "stop_reason": turn.stop_reason,
                                "tool_calls": [
                                    {"name": tc.name, "input": tc.input}
                                    for tc in turn.tool_calls
                                ],
                            },
                            "turn_summary.json",
                        )
                    except Exception:
                        pass
                if err is not None:
                    try:
                        mlflow.set_tag("error", type(err).__name__)
                        mlflow.log_text(repr(err), "error.txt")
                    except Exception:
                        pass


def wrap_provider(provider: LLMProvider) -> LLMProvider:
    """Return either the bare provider (tracing off) or the tracing
    decorator. Either way the result satisfies the LLMProvider
    Protocol so callers don't need to branch."""
    if not is_tracing_enabled():
        return provider
    return TracingProvider(provider)  # type: ignore[return-value]


def serialize_messages_for_log(messages: list[Message]) -> str:
    """Best-effort JSON view of a message list — used by the eval
    harness when logging full prompts as run artifacts. Never raises."""
    try:
        return json.dumps(
            [
                {"role": m.role, "content": m.content if isinstance(m.content, str) else m.content}
                for m in messages
            ],
            default=str,
            ensure_ascii=False,
        )
    except Exception:
        return "[serialization_failed]"
