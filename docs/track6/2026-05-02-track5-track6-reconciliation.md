# Track 5 (Wiki) ↔ Track 6 (GBrain) — reconciliation decision

> Decision date: 2026-05-02. Status: **complement (option B)**.

## The question

Track 5 (agentic wiki) and Track 6 (GBrain) occupy overlapping problem space — both are persistent agent memory layered over the cockpit. Open question from the original Track 6 brief was one of:

- (a) **Deprecate Track 5 entirely.** Track 6 fully replaces the wiki; cockpit's `[WIKI]` tab becomes a thin facade over GBrain pages.
- (b) **Complement.** Keep Track 5 for retail-domain `category/<x>`, `vendor/<x>`, `policy/<x>` pages — spine-native, owned by the cockpit, audited via the Critic-gated auto-publish. Route everything else (operator's broader durable memory, code-graph, cross-cutting decisions) to GBrain via MCP.
- (c) **Thin GBrain back-end.** Cockpit's WikiTab reads/writes GBrain pages directly — the wiki tables go away.

## What we're picking

**Option B — complement.** Keep both tables in lockstep:

| Layer            | Lives in                       | Owns                                                                   |
|------------------|--------------------------------|------------------------------------------------------------------------|
| **Wiki** (Track 5) | `wiki_pages` + `wiki_revisions` (SQLite, spine-native) | Retail-domain pages: `category/...`, `vendor/...`, `store/.../...`, `policy/...`, `playbook/...`. Auto-publish gated by the Critic; revision history captured per slug. The Wiki Curator (W6) writes here. |
| **Brain** (Track 6) | GBrain (Bun-native, PGLite locally / Postgres shared) | Operator's broader durable memory: chat-turn ingest, free-form pages, vendor research notes, decision logs. Code-graph (`callers`/`callees`/`def`/`refs`). Network egress for enrichment (operator-toggled). |

The two tables don't share rows. The cockpit surfaces them as **two adjacent tabs** (`[WIKI]` and `[BRAIN]`) with the same look-and-feel so the operator's muscle memory transfers.

## Why not (a) or (c)

- **(a) Deprecate Wiki.** Wiki's auto-publish gate (W4) is a *cockpit-specific* invariant: a draft only ships when its critique came back clean THIS TURN, with version cross-checks against the `wiki_pages` row. That gate is wired through `events_for_turn` + the Critic — moving it into GBrain would require re-implementing it inside an external system whose maintainers don't share our policy invariants. Hard pass.
- **(c) Thin GBrain back-end.** Same reasoning. The wiki's strongest property — the per-slug Critic-gated promotion path — depends on the spine event log + the cockpit's transactional control over `wiki_pages.status`. Routing it through GBrain's HTTP MCP layer would force us to re-invent transactions over an HTTP boundary.

## What that costs us

**Two tabs instead of one.** Operators who think "where did we write that down?" sometimes have to check both. Cost is paid once at search time; we mitigate by making `brain_search` (Critic / Analyst tool) AND `wiki_search` always available, and the operator's chat agent picks the right one based on the slug shape.

**Two stores, two backups.** Wiki backs up via SQLite; GBrain backs up via PGLite or Postgres. Reset paths are documented in `infra/gbrain/README.md` and in the wiki section of the main README. The brain-ingest hook (G3) writes the wiki's audit trail (artifact ids, turn ids) to brain pages but does not duplicate the wiki body — GBrain's role is the operator-facing brain, not a wiki replica.

## What we're killing

Nothing in Track 5 is retired. The decision is purely about scope going forward.

## What changes operationally

- **Curator (W6) keeps owning retail-domain slugs.** It only writes `category/*`, `vendor/*`, `store/*`, `policy/*`, `playbook/*` — those slugs live in the wiki.
- **Chief's chat-turn hook (G3)** ingests every operator turn into GBrain — but does NOT propose wiki edits. The Curator is the only path from a chat turn to `wiki_pages`.
- **Brain ingest is rate-limited.** Max 3 pages per turn, daemon-threaded so a slow brain can't stall the operator-facing reply.
- **Mock mode.** Without `GBRAIN_BEARER`, the brain tools fall back to a substrate-backed mock client that surfaces `wiki_pages`. So even in mock mode the cockpit is coherent — the operator just sees the wiki content twice (once via `wiki_search`, once via `brain_search`). When live GBrain comes online, the brain corpus diverges from the wiki and the mock view goes away.

## What's deferred

- **Code-graph live mode.** The Critic's `code_callers` / `code_def` / `code_refs` tools route through GBrain's `/v1/code/*` endpoints when configured; mock mode returns `{mock: true, results: []}` so the Critic surfaces the gap rather than hallucinating. Wiring in a real `gbrain sources add <repo> --strategy code` step against this codebase is operator-initiated, documented in `infra/gbrain/README.md`.
- **Approval rail for brain-pending pages.** If a future workflow lets agents *propose* brain pages (analogous to wiki drafts), the cockpit's approval rail will need a brain-side queue. Not in scope for the current rollout — brain is read + ingest only from the cockpit.

## Acceptance

- Operators can find retail-domain decisions via the WikiTab without leaving the cockpit (Track 5 W5 — already shipped).
- Operators can find broader / cross-cutting decisions via the BrainTab without leaving the cockpit (Track 6 G5 — this rollout).
- The Critic can quote both surfaces in a single critique. (`brain_search` + `wiki_search` are both on the Critic's toolkit.)
