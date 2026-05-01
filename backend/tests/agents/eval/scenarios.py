"""Eval scenarios for Track 2 A5.

Four seeded scenarios that stress different parts of the mesh:

  1. overstock_summer  — Pricing+Marketing+Replenishment cooperation,
     policy floor on the markdown side, peer review window.
  2. weekend_heatwave  — time-pressure scenario, rewards Marketing's
     speed but penalises overclaim on projected lift.
  3. supplier_risk     — Replenishment-driven; the Critic should catch
     that "hold all POs" without per-vendor analysis is too coarse.
  4. single_store_stockout — Merchandiser+Store Manager+Fulfillment;
     wrong answer is "ship from a high-pressure store"; right answer
     is "BOPIS pivot + transfer from the lowest-pressure store with
     surplus".

Each scenario provides:
  - `prompt`: the operator's chat input
  - `golden`: a structured rubric the LLM-as-judge uses to score the
    finished turn (transcript + final artifacts)
  - `expected_specialists`: minimum set the multi-pass run should touch

The `golden` shape is intentionally narrow — four binary-ish dimensions
that the judge scores 0-3 each. Track 2 A5's success criterion: multi-
pass strictly beats single-pass on >= 3 of 4 dimensions on >= 3 of 4
scenarios.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EvalScenario:
    name: str
    prompt: str
    expected_specialists: tuple[str, ...]
    golden_rubric: dict[str, str] = field(default_factory=dict)

    def judge_prompt_context(self) -> str:
        rubric_lines = "\n".join(f"  - {k}: {v}" for k, v in self.golden_rubric.items())
        return (
            f"Scenario: {self.name}\n"
            f"Operator prompt: {self.prompt}\n"
            f"Expected specialists touched: {', '.join(self.expected_specialists)}\n"
            f"Rubric:\n{rubric_lines}\n"
        )


SCENARIOS: list[EvalScenario] = [
    EvalScenario(
        name="overstock_summer",
        prompt=(
            "Summer Apparel sell-through is collapsing — units down 22% week-on-week, "
            "categorical overstock visible. Build a markdown + push plan that doesn't "
            "blow margin floors and explain how stores rebalance."
        ),
        expected_specialists=("Analyst", "Pricing & Promo", "Marketing", "Merchandiser"),
        golden_rubric={
            "factual_correctness": (
                "Numbers cited (sell-through delta, on-hand, margin) match what "
                "aggregate_by_category + inventory_health actually return."
            ),
            "evidence_cited": (
                "At least two distinct spine queries are quoted in the final reply. "
                "Bonus if the reply links to the artifact(s)."
            ),
            "policy_adherence": (
                "Markdown stays at-or-below 40%. No store-transfer from a store "
                "with on_hand below the destination's projected demand."
            ),
            "recommendation_quality": (
                "At least one alternative was considered (e.g. tiered markdown vs "
                "flat). Counter-recommendation surfaces a tradeoff, not a hedge."
            ),
        },
    ),
    EvalScenario(
        name="weekend_heatwave",
        prompt=(
            "Heatwave hits this weekend. Push the right summer categories and lock "
            "down the campaign budget. I need this in market within 24h."
        ),
        expected_specialists=("Analyst", "Marketing", "Fulfillment"),
        golden_rubric={
            "factual_correctness": (
                "Weather-sensitivity score and current campaign queue match what "
                "list_categories + list_campaigns return."
            ),
            "evidence_cited": (
                "Either Marketing's pre-existing weather playbook is referenced, "
                "or the reasoning is grounded in cited momentum/inventory pressure."
            ),
            "policy_adherence": (
                "Campaign budget stays under $65k cap. No promotion above 40%."
            ),
            "recommendation_quality": (
                "Concrete categories and segments are named — not a generic "
                "'launch a heatwave campaign'. Lift estimate is plausible (not >2×)."
            ),
        },
    ),
    EvalScenario(
        name="supplier_risk",
        prompt=(
            "BreezeCo and SunGoods are flagged in the supplier risk panel. Two "
            "inbound POs in the next 10 days. Should we hold them, expedite them, "
            "or split the call by vendor?"
        ),
        expected_specialists=("Analyst", "Replenishment"),
        golden_rubric={
            "factual_correctness": (
                "Per-vendor PO counts and risk drivers (lateness vs damage vs "
                "stockout impact) match inventory_health.inbound_pos."
            ),
            "evidence_cited": (
                "On-hand depletion runway is computed (days of cover) before the "
                "hold/expedite call."
            ),
            "policy_adherence": (
                "If holding a PO, the on-hand depletion still has runway; no blanket "
                "'hold all' without vendor-by-vendor justification."
            ),
            "recommendation_quality": (
                "Splits the answer by vendor with explicit rationale, not a single "
                "category-wide verdict."
            ),
        },
    ),
    EvalScenario(
        name="single_store_stockout",
        prompt=(
            "Store-3 is stocked out on SUM-002. Local demand is high. What's the "
            "right play — transfer from another store, ship from DC, or pivot to "
            "BOPIS with a different SKU substitute?"
        ),
        expected_specialists=("Merchandiser", "Store Manager", "Fulfillment"),
        golden_rubric={
            "factual_correctness": (
                "Correct on-hand by store and store labor pressure cited."
            ),
            "evidence_cited": (
                "Comparison among at least two stores' on-hand and pressure before "
                "naming the source store."
            ),
            "policy_adherence": (
                "Source store's on_hand stays above its own projected demand. No "
                "transfer that creates a new stockout."
            ),
            "recommendation_quality": (
                "Names a concrete fulfillment route (BOPIS / ship-from-store / DC) "
                "with a tradeoff against speed and store-pressure cost."
            ),
        },
    ),
]


def by_name(name: str) -> EvalScenario:
    for s in SCENARIOS:
        if s.name == name:
            return s
    raise KeyError(f"unknown eval scenario: {name}")
