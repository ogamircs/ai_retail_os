"""Analyst DSPy signature (Track 7 D1).

The Analyst is the canary agent for DSPy because it's:
  * read-only (no outbox actions — failures are cheap)
  * deterministic-ish (queries the spine, not the world)
  * scorable by the existing eval harness (4 dimensions per scenario)

Signature shape mirrors the analyst's actual job: take an operator
question + a snapshot of the spine, produce three markdown sections
(findings, evidence, open_questions). The downstream Module composes
these back into one body that lands as a `diagnostic` artifact —
matching exactly what the in-code Analyst writes today, so the Chief's
review loop and the Reports tab stay unchanged.

Lazy `import dspy` — the cockpit demo path runs without `dspy-ai`
installed; the optional `[dspy]` extra is the contract.
"""

from __future__ import annotations

from typing import Any


def _require_dspy() -> Any:
    """Lazy importer so missing `dspy-ai` only matters at compile time.
    Raises a clean error message that points at the optional extra
    instead of letting the operator hit ModuleNotFoundError mid-turn."""
    try:
        import dspy  # type: ignore

        return dspy
    except ImportError as exc:  # pragma: no cover — env-gated
        raise ImportError(
            "dspy-ai is not installed. Activate the optional extra with "
            "`pip install -e .[dspy]` before invoking the optimizer."
        ) from exc


def make_signature():
    """Build the Analyst Signature class. Wrapped in a factory because
    `dspy.Signature` only exists after `import dspy` succeeds — module-
    level class definitions would crash on import in cockpits without
    the extra installed."""
    dspy = _require_dspy()

    class AnalystSignature(dspy.Signature):
        """Answer the operator's diagnostic question by reading the
        spine snapshot and producing a structured Analyst report.

        Output sections must be terse markdown — the cockpit's Reports
        tab renders the body verbatim. Cite numbers from the snapshot;
        do not invent metrics. Open questions are for the Chief's next
        turn, not for the operator to chase."""

        operator_question: str = dspy.InputField(
            desc="The operator's chat input — a diagnostic question "
            "about the business (e.g. 'did the markdown campaign land?')."
        )
        spine_snapshot: str = dspy.InputField(
            desc="Compact JSON snapshot of the relevant spine state — "
            "category KPIs, recent events, sales aggregates the Chief "
            "fetched before delegating to the Analyst."
        )
        findings_md: str = dspy.OutputField(
            desc="2-5 bullet markdown findings. Each bullet starts with "
            "a category or SKU and includes a concrete number from the "
            "snapshot. No speculation."
        )
        evidence_md: str = dspy.OutputField(
            desc="Markdown table or bulleted list of the spine rows the "
            "findings rely on (event id, ts, kind, payload excerpt). "
            "Used by the Critic to verify the report didn't hallucinate."
        )
        open_questions_md: str = dspy.OutputField(
            desc="0-3 bullet markdown questions the Analyst could not "
            "answer with the snapshot at hand — passed to the Chief so "
            "the next turn can fetch the missing data."
        )

    return AnalystSignature


def make_module():
    """Compose the Analyst Signature into a `dspy.Module`.

    Uses `dspy.ChainOfThought` so the optimizer (D3 BootstrapFewShot)
    has a reasoning trace to bootstrap demos from. The compiled module
    serializes back to a markdown prompt via
    `app.agents.dspy_compile.compiled_to_markdown` — that's what the
    prompt registry consumes."""
    dspy = _require_dspy()
    sig = make_signature()

    class AnalystModule(dspy.Module):
        def __init__(self):
            super().__init__()
            self.predict = dspy.ChainOfThought(sig)

        def forward(self, operator_question: str, spine_snapshot: str):
            return self.predict(
                operator_question=operator_question,
                spine_snapshot=spine_snapshot,
            )

    return AnalystModule()
