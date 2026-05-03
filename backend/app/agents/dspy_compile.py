"""DSPy → prompt-registry bridge (Track 7 D3).

The optimizer (`scripts/dspy_optimize.py`) produces a compiled
`dspy.Module`: bootstrap-selected few-shot demos + (optionally)
optimizer-rewritten instructions. We render that compiled artefact
into the same `prompts/<slug>/v<n+1>.md` shape the Track 4 M4 prompt
registry already resolves — so the optimizer's output ships through
the same alias-flip gate as a hand-edited prompt.

Layout of the rendered file:

    {handwritten_policy_block}

    ## Few-shot demos

    ### Demo 1
    **operator_question:** ...
    **spine_snapshot:** ...
    **findings_md:** ...
    **evidence_md:** ...
    **open_questions_md:** ...

    ### Demo 2
    ...

The `## Few-shot demos` heading is the contract: the prompt registry
treats anything below it as bootstrap context. A handwritten v1
without that section is unaffected.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

FEW_SHOT_HEADING = "## Few-shot demos"


def compiled_to_markdown(
    compiled_module: Any,
    handwritten_policy_block: str,
) -> str:
    """Render a compiled DSPy module into the prompt-registry markdown.

    `handwritten_policy_block` is the operator-authored guidance that
    must survive every recompile (policy floors, citation rules, etc).
    The optimizer never rewrites it — it only appends demos.

    `compiled_module.demos` is the bootstrap-selected list (DSPy stores
    them on each Predict child after compile()). When the predictor
    has no demos (e.g. compile() failed or the optimizer found no
    improvements), we still emit the heading so the file shape stays
    uniform — the registry then resolves the same handwritten content
    plus an empty demo block.
    """
    demos = _collect_demos(compiled_module)
    lines = [handwritten_policy_block.rstrip(), "", FEW_SHOT_HEADING, ""]
    if not demos:
        lines.append("_No demos selected — optimizer pass returned an empty bootstrap set._")
        return "\n".join(lines).rstrip() + "\n"
    for idx, demo in enumerate(demos, start=1):
        lines.append(f"### Demo {idx}")
        for k, v in demo.items():
            v_str = _format_demo_value(v)
            lines.append(f"**{k}:** {v_str}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _collect_demos(compiled_module: Any) -> list[dict]:
    """Walk the compiled module's children and pull every `demos` list
    we can find. DSPy stores them on each Predict node — for a single-
    Predict module like AnalystModule there's one list; for a multi-
    step module each Predict can carry its own.

    Returns a flat list of dicts. Each dict is the demo's key/value
    fields. Order preserved.
    """
    out: list[dict] = []
    seen: set[int] = set()
    queue = [compiled_module]
    while queue:
        node = queue.pop(0)
        nid = id(node)
        if nid in seen:
            continue
        seen.add(nid)
        demos = getattr(node, "demos", None)
        if isinstance(demos, list):
            for d in demos:
                out.append(_demo_to_dict(d))
        # Walk into sub-modules / predictors. DSPy modules expose
        # children via `named_predictors()` / `named_parameters()`;
        # fall back to vars() for anything we don't recognise.
        named = getattr(node, "named_predictors", None)
        if callable(named):
            try:
                for _, child in named():
                    queue.append(child)
            except Exception:
                pass
        for v in vars(node).values():
            # Avoid sucking in strings, numbers, or lists of demos
            # we already harvested.
            if hasattr(v, "demos") or hasattr(v, "forward"):
                queue.append(v)
    return out


def _demo_to_dict(demo: Any) -> dict:
    """DSPy demos can be `dspy.Example` instances (with `.toDict()` /
    `.__dict__`) or already plain dicts. Normalise."""
    if isinstance(demo, dict):
        return {str(k): demo[k] for k in demo}
    to_dict = getattr(demo, "toDict", None)
    if callable(to_dict):
        try:
            d = to_dict()
            if isinstance(d, dict):
                return {str(k): d[k] for k in d}
        except Exception:
            pass
    # Fallback: pull the public attrs.
    return {
        str(k): v
        for k, v in vars(demo).items()
        if not str(k).startswith("_")
    }


def _format_demo_value(v: Any) -> str:
    """Demo values can be long markdown bodies; render them inline as
    a fenced block when they contain newlines so the prompt file stays
    parseable. Short values render on one line."""
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    if "\n" in s:
        return "\n```\n" + s.rstrip() + "\n```"
    return s


def write_compiled_prompt(
    agent_slug: str,
    compiled_module: Any,
    handwritten_policy_block: str,
    prompts_root: Path,
) -> tuple[Path, str]:
    """Resolve the next version filename under `prompts/<slug>/`, write
    the compiled markdown, return `(path, version)` (e.g. ('.../v3.md',
    'v3')).

    Picks v<n+1> where n is the highest existing v* file. A fresh
    agent dir starts at v1.

    Two concurrent compiles for the same agent (possible via repeated
    `/api/dspy/optimize/{agent}` calls) used to be a TOCTOU: both scan
    the dir, both pick `v(n+1)`, the second `write_text` silently
    overwrites the first and the alias bumps reflect only the last
    writer. Atomic create via `os.O_CREAT | os.O_EXCL` closes the gap
    — the loser of the race gets `FileExistsError`, bumps to v(n+2),
    and retries. Capped at 64 attempts so a misconfigured filesystem
    can't busy-loop the worker.
    """
    agent_dir = prompts_root / agent_slug
    agent_dir.mkdir(parents=True, exist_ok=True)
    body = compiled_to_markdown(compiled_module, handwritten_policy_block)
    body_bytes = body.encode("utf-8")
    for _attempt in range(64):
        existing = sorted(
            int(p.stem[1:])
            for p in agent_dir.glob("v*.md")
            if p.stem[1:].isdigit()
        )
        next_n = (existing[-1] + 1) if existing else 1
        next_version = f"v{next_n}"
        path = agent_dir / f"{next_version}.md"
        try:
            fd = os.open(
                str(path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError:
            # Another compile won this slot; rescan and try the next n.
            continue
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body_bytes)
        except Exception:
            # Best-effort cleanup — leave a partial v<n>.md behind only
            # when even the unlink fails.
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return path, next_version
    raise RuntimeError(
        f"could not allocate a fresh prompt version under {agent_dir} "
        "after 64 attempts — check filesystem state."
    )


def bump_alias(
    agent_slug: str,
    alias: str,
    version: str,
    prompts_root: Path,
) -> dict[str, str]:
    """Update `prompts/<slug>/aliases.json` so `alias` -> `version`.
    Preserves any other aliases already in the file. Returns the new
    aliases dict.

    Used by D3 (`staging` → v<n+1>) and D4 (`prod` → v<n+1>) flows.
    """
    p = prompts_root / agent_slug / "aliases.json"
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
    else:
        data = {}
    data[alias] = version
    p.write_text(json.dumps(data, indent=2) + "\n")
    return {str(k): str(v) for k, v in data.items()}
