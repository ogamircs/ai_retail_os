"""Prompt registry (Track 4 M4).

Each agent's system prompt lives in a versioned file at the repo root:

    prompts/<agent>/v<n>.md

Aliases (e.g. `prod`, `staging`) point at a specific version via:

    prompts/<agent>/aliases.json   # { "prod": "v3", "staging": "v4" }

Resolution order at runtime:
  1. If the env var `<AGENT>_PROMPT_OVERRIDE` is set to a path, load
     that file directly. Useful for one-off A/B testing without
     touching the registry.
  2. If `<AGENT>_PROMPT_ALIAS` is set, look it up in `aliases.json`.
  3. Else, fall back to the alias `prod` from `aliases.json`.
  4. Else, fall back to the in-code SYSTEM string passed into
     `resolve_prompt(...)` so the cockpit demo path keeps working
     when the registry hasn't been seeded.

The registry is *read-only* at runtime — agents never write here.
Operators edit `prompts/<agent>/v<n+1>.md` and bump the alias in
`aliases.json`; the next operator turn picks up the change without a
code deploy. Track 4 M4 done-when: flipping an alias changes the next
turn's behaviour.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_BACKEND = Path(__file__).resolve().parent.parent.parent
_PROMPTS_ROOT = _BACKEND.parent / "prompts"


def _agent_dir(agent: str) -> Path:
    """Sanitise the agent name into a slug: 'Pricing & Promo' → 'pricing'.

    The slug rule is the same one the cockpit's frontend uses for
    agent-ink CSS classes — drop everything but [a-z0-9_], collapse
    multiple separators, lower-case. Keeps the on-disk layout readable
    when an operator runs `ls prompts/`.
    """
    s = "".join(c if c.isalnum() else "_" for c in agent.strip().lower())
    while "__" in s:
        s = s.replace("__", "_")
    return _PROMPTS_ROOT / s.strip("_")


def _read_aliases(agent: str) -> dict[str, str]:
    p = _agent_dir(agent) / "aliases.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _read_version(agent: str, version: str) -> Optional[str]:
    """Read `prompts/<agent>/<version>.md` if present; else None."""
    p = _agent_dir(agent) / f"{version}.md"
    if not p.exists():
        return None
    try:
        return p.read_text()
    except Exception:
        return None


def _env_for(agent: str, suffix: str) -> Optional[str]:
    """Per-agent env var lookup. `Pricing & Promo` + `_PROMPT_ALIAS` →
    `PRICING_PROMO_PROMPT_ALIAS`. Reading both an upper-snake-case and a
    plain-snake-case form covers operator typos.
    """
    slug = "".join(c if c.isalnum() else "_" for c in agent.upper().strip())
    while "__" in slug:
        slug = slug.replace("__", "_")
    slug = slug.strip("_")
    return os.getenv(f"{slug}{suffix}")


def resolve_prompt(agent: str, fallback: str) -> str:
    """Return the active system prompt for `agent`.

    Resolution chain (first hit wins):
      1. file path in <AGENT>_PROMPT_OVERRIDE env var
      2. version via <AGENT>_PROMPT_ALIAS env var → aliases.json
      3. version `prod` via aliases.json
      4. `fallback` (the in-code SYSTEM string)
    """
    override = _env_for(agent, "_PROMPT_OVERRIDE")
    if override:
        try:
            return Path(override).read_text()
        except Exception:
            return fallback

    aliases = _read_aliases(agent)

    alias_name = _env_for(agent, "_PROMPT_ALIAS") or "prod"
    version = aliases.get(alias_name)
    if version:
        prompt = _read_version(agent, version)
        if prompt is not None:
            return prompt

    return fallback


def list_versions(agent: str) -> list[str]:
    """Cheap introspection — useful for the cockpit's future Reports
    surfacing and for the M5 telemetry aggregator. Returns the version
    files present on disk for `agent`, sorted by name.
    """
    d = _agent_dir(agent)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("v*.md"))


def active_version(agent: str) -> str | None:
    """Return the version string currently resolved for `agent`'s
    system prompt under the alias chain — None if the registry has no
    entry (cockpit is using the in-code fallback)."""
    if _env_for(agent, "_PROMPT_OVERRIDE"):
        return "override"
    alias_name = _env_for(agent, "_PROMPT_ALIAS") or "prod"
    return _read_aliases(agent).get(alias_name)
