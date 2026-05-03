"""Pricing & Promo DSPy signature (Track 7 D5).

Pricing is the first action specialist DSPy compiles for. Unlike the
Analyst (read-only, single output body), Pricing's job has TWO things
to optimize together:

  1. The artifact body — a markdown markdown plan with margin
     justification, tier breakdown, alternatives.
  2. The tool calls — when Pricing decides to *also* propose an
     outbox action (e.g. `propose_promotion`), the tool selection +
     payload should match the policy floor (no >40% off, no margin
     under 0.20).

DSPy's multi-step optimization (MIPROv2) lets us compile both at once.
The Signature exposes `tool_calls` as an output field; the optimizer
selects demos where the body AND the tool call collectively scored
high in the eval harness.

Lazy `import dspy` — same pattern as the Analyst signature so the
cockpit doesn't need the `[dspy]` extra to start.
"""

from __future__ import annotations

from typing import Any


def _require_dspy() -> Any:
    try:
        import dspy  # type: ignore

        return dspy
    except ImportError as exc:  # pragma: no cover — env-gated
        raise ImportError(
            "dspy-ai is not installed. Activate the optional extra with "
            "`pip install -e .[dspy]` before invoking the optimizer."
        ) from exc


def make_signature():
    """Build the Pricing Signature class."""
    dspy = _require_dspy()

    class PricingSignature(dspy.Signature):
        """Produce a markdown plan that defends margin floors AND
        decides whether to emit a `propose_promotion` outbox action.

        Hard rules — never compile demos that violate either:
          * `discount_pct` <= 40 (cockpit's policy floor)
          * `min_margin` >= 0.20 (cockpit's policy floor)
          * `body_md` cites the spine numbers it relied on (no bare
            recommendations)
        """

        operator_question: str = dspy.InputField(
            desc="Operator's chat input (e.g. 'summer apparel margin "
            "is collapsing — build a markdown plan')."
        )
        category_snapshot: str = dspy.InputField(
            desc="JSON snapshot of the relevant category state — "
            "sell_through, on_hand, margin, price floor, top SKUs."
        )
        body_md: str = dspy.OutputField(
            desc="Markdown plan with three sections: ## Plan, "
            "## Margin justification, ## Alternatives. Each section "
            "cites concrete numbers from the snapshot."
        )
        tool_calls: str = dspy.OutputField(
            desc="JSON list of outbox-action tool calls to emit. "
            "Empty list `[]` is valid when the recommendation is "
            "'do nothing'. Each call must specify `tool` (e.g. "
            "`propose_promotion`) + `payload`. The payload's "
            "`discount_pct` MUST be <= 40 and the resulting margin "
            "MUST stay >= 0.20."
        )

    return PricingSignature


def make_module():
    """Compose the Pricing Signature into a `dspy.Module`.

    Uses `ChainOfThought` so MIPROv2 can bootstrap from reasoning
    traces. The compiled module renders to `prompts/pricing_promo/
    v<n+1>.md` via `app.agents.dspy_compile.compiled_to_markdown` —
    same pipeline as the Analyst."""
    dspy = _require_dspy()
    sig = make_signature()

    class PricingModule(dspy.Module):
        def __init__(self):
            super().__init__()
            self.predict = dspy.ChainOfThought(sig)

        def forward(self, operator_question: str, category_snapshot: str):
            return self.predict(
                operator_question=operator_question,
                category_snapshot=category_snapshot,
            )

    return PricingModule()
