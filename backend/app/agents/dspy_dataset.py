"""DSPy training-set loader (Track 7 D2).

Loads `prompts/training/<agent>.jsonl` and converts each row into a
`dspy.Example` with the input fields the optimizer will hold fixed
during teleprompter passes. Operators can extend the jsonl file by
hand or via the eval-harness extractor (`extract_examples_from_run`)
that walks `last_run.json` for the highest-scored Analyst artifact
per scenario.

The jsonl shape is intentionally agent-agnostic — every row is just a
flat object whose keys match the agent's Signature input/output
fields. No header row, one example per line.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def _require_dspy() -> Any:
    try:
        import dspy  # type: ignore

        return dspy
    except ImportError as exc:  # pragma: no cover — env-gated
        raise ImportError(
            "dspy-ai is not installed. Activate the optional extra with "
            "`pip install -e .[dspy]` to load training examples."
        ) from exc


def load_jsonl(path: Path) -> list[dict]:
    """Read the raw jsonl rows. Skip blank lines + lines whose JSON
    fails to parse (with a printed warning) so a single typo doesn't
    nuke the whole training set."""
    rows: list[dict] = []
    if not path.exists():
        return rows
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"[dspy_dataset] skipping {path.name}:{i} — bad JSON: {e}")
            continue
        if not isinstance(obj, dict):
            print(f"[dspy_dataset] skipping {path.name}:{i} — not an object")
            continue
        rows.append(obj)
    return rows


def to_examples(rows: Iterable[dict], input_keys: tuple[str, ...]):
    """Wrap each row in a `dspy.Example` and call `.with_inputs(...)`
    so the optimizer only treats `input_keys` as fixed. Output fields
    are what the bootstrap pass scores against."""
    dspy = _require_dspy()
    out = []
    for row in rows:
        ex = dspy.Example(**row).with_inputs(*input_keys)
        out.append(ex)
    return out


def load_analyst_dataset(prompts_root: Path):
    """Convenience loader for D3 — returns a list of `dspy.Example`
    ready to feed `BootstrapFewShot.compile`. The Analyst Signature
    has two input fields: `operator_question`, `spine_snapshot`."""
    rows = load_jsonl(prompts_root / "training" / "analyst.jsonl")
    return to_examples(rows, input_keys=("operator_question", "spine_snapshot"))


def extract_examples_from_run(
    last_run_path: Path,
    scenarios_module: Any,
    output_path: Path,
) -> int:
    """Walk an eval-harness `last_run.json` and append rows to
    `output_path` (one per scenario × winning mode) so a fresh eval
    feeds back into the next optimizer pass.

    Picks the multi_pass mode when its total score >= single_pass
    (Track 2 A5's success direction); else falls back to single_pass.
    The Analyst's findings/evidence/open_questions sections are pulled
    out of the final reply by markdown heading match. Rows whose final
    reply doesn't carry the expected sections are skipped — better an
    empty extraction than a malformed example.

    Returns the number of rows appended.
    """
    if not last_run_path.exists():
        return 0
    payload = json.loads(last_run_path.read_text())
    runs = payload.get("runs") or {}
    appended: list[dict] = []
    for scenario_name, modes in runs.items():
        scenario = getattr(scenarios_module, "by_name", lambda _n: None)(scenario_name)
        if scenario is None:
            continue
        # Pick the higher-scoring mode for this scenario.
        best_mode = None
        best_total = -1.0
        for mode_name, payload_mode in modes.items():
            score = (payload_mode or {}).get("score") or {}
            total = sum(
                float(score.get(k, 0))
                for k in (
                    "factual_correctness",
                    "evidence_cited",
                    "policy_adherence",
                    "recommendation_quality",
                )
            )
            if total > best_total:
                best_total = total
                best_mode = mode_name
        if best_mode is None:
            continue
        final = (modes[best_mode] or {}).get("final") or ""
        sections = _split_markdown_sections(final)
        findings = sections.get("findings") or sections.get("findings_md")
        evidence = sections.get("evidence") or sections.get("evidence_md")
        oq = sections.get("open questions") or sections.get("open_questions")
        if not (findings and evidence):
            # Reply didn't follow the canonical Analyst shape; skip
            # rather than feed the optimizer noise.
            continue
        appended.append(
            {
                "operator_question": scenario.prompt,
                "spine_snapshot": "{}",  # Live snapshot not preserved in last_run.json; operator can hand-fill.
                "findings_md": findings,
                "evidence_md": evidence,
                "open_questions_md": oq or "",
            }
        )
    if not appended:
        return 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a") as f:
        for row in appended:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(appended)


def _split_markdown_sections(body: str) -> dict[str, str]:
    """Cheap markdown section splitter — returns a dict keyed by the
    lower-cased heading text (without the leading `#` / `##`). Captures
    body up to the next heading at the same depth."""
    sections: dict[str, str] = {}
    cur_key: str | None = None
    cur_lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if cur_key is not None:
                sections[cur_key] = "\n".join(cur_lines).strip()
            heading = stripped.lstrip("# ").strip().lower()
            cur_key = heading
            cur_lines = []
            continue
        if cur_key is not None:
            cur_lines.append(line)
    if cur_key is not None:
        sections[cur_key] = "\n".join(cur_lines).strip()
    return sections
