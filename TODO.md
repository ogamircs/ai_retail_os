# TODO

Two parallel tracks. Pick whichever has the next freeing-up unit.

1. **Track 1 — Real-system rollout**: replace mock-mode adapters with real instances (ERPNext first, then Mautic / Medusa / OpenBoxes / Akeneo / Superset). Each system has its own 6-phase rollout (P1–P6).
2. **Track 2 — Agent mesh upgrade**: turn the single-shot delegate-and-write loop into a multi-agent dialogue with critique before the final report ships.

---

# Track 1 — Real-system rollout

Goal: replace mock-mode adapters with real systems, one at a time, in a foundation-first order. Each system goes through its own 6-phase rollout (P1–P6). When all 6 are done for a system, we move on.

**Strategy:** A · Foundation-first.

Order:
1. **ERPNext** ← active
2. Mautic
3. Medusa
4. OpenBoxes
5. Akeneo
6. Superset

For every system, the per-system phases are the same:

| Phase | Outcome |
|---|---|
| P1 · Local instance | Docker stack up, healthy, admin reachable |
| P2 · Demo seed | Idempotent script populates the system with retail data that mirrors our spine |
| P3 · Inbound sync | `.env` wired, `/api/integrations/{system}/sync` pulls real records into `record_cache` + `external_refs` (+ updates substrate where the adapter does so) |
| P4 · Live outbound apply | Adapter overrides `apply_outbound` to actually create real (draft) records on apply |
| P5 · Agent-loop UAT | One README demo path runs end-to-end against the real system |
| P6 · Docs + env-gated tests | README section + troubleshooting + skip-if-no-creds tests |

---

## ERPNext (active)

- [x] **P1 · Local instance** _(merged: `feature/erpnext-integration` → main)_
  - [x] `infra/erpnext/docker-compose.yml` (frappe/erpnext v15 multi-arch digest + mariadb 10.6 + redis 6.2)
  - [x] Root `Makefile` targets: `erpnext-up`, `erpnext-down`, `erpnext-bootstrap`, `erpnext-logs`, `erpnext-status`, `erpnext-nuke`
  - [x] `infra/erpnext/bootstrap.sh` creates site `retail.localhost`, installs erpnext app, sets admin pw, generates API key/secret idempotently
  - [x] `infra/erpnext/README.md` documents URL + creds + reset path + Apple Silicon digest pin rationale
  - **Verified:** `curl /api/method/frappe.auth.get_logged_user` → `{"message":"Administrator"}`

- [x] **P2 · Demo seed** _(merged: `feature/erpnext-p2-seed` → main)_
  - [x] `infra/erpnext/seed.py` — REST-based, idempotent. Creates Company `AI Retail OS` (abbr `ARO`), 3 Item Groups, 30 Items, 5 Warehouses (one per store), Suppliers, Brands, Customers, 5 opening Stock Entries (Material Receipt, submitted), 12 Purchase Orders, 10 Sales Invoices (drafts).
  - [x] Reads from `backend/data/spine.db` directly so the seed stays in sync with the canonical retail demo data.
  - [x] Idempotent across all entity types: re-runs print zero `++` lines.
  - [x] Makefile target `make erpnext-seed`.
  - **Verified:** ERPNext desk populated; second run is a no-op.

- [x] **P3 · Inbound sync** _(merged: `feature/erpnext-p3-sync` → main)_
  - [x] `backend/.env` wired with ERPNext API credentials
  - [x] `POST /api/integrations/erpnext/sync` succeeds against real ERPNext: 272 records across Item Group / Item / Warehouse / Bin / Customer / Supplier / Purchase Order
  - [x] `record_cache` + `external_refs` populate end-to-end; cockpit Integrations row shows `mode=connected` · last sync timestamp updated
  - [x] **Bin aggregation fix** — adapter previously overwrote `substrate_inventory.on_hand` with whichever Bin came last; now sums across all warehouses per SKU. Pinned by a regression test.
  - [x] `backend/tests/test_integrations_erpnext_live.py` — env-gated, three tests covering live sync, external_refs round-trip, and the Bin-aggregation fix. Skipped automatically when credentials aren't present so CI stays mock-only.

- [ ] **P4 · Live outbound apply**
  - [ ] Override `ERPNextAdapter.apply_outbound` in `backend/app/integrations/systems.py`
    - [ ] `promotion` → POST `/api/resource/Pricing Rule` (draft, not submitted)
    - [ ] `po_held` → PATCH `/api/resource/Purchase Order/{name}` (status `On Hold` or add comment)
    - [ ] `po_expedited` → comment + bumped schedule_date
    - [ ] `store_transfer` → POST `/api/resource/Stock Entry` (Material Transfer, draft)
    - [ ] `fulfillment_routing` → POST `/api/resource/Sales Order` (draft)
  - [ ] Store returned `name` as `external_id` in `outbox_actions.external_id`
  - **Done when:** cockpit drawer apply produces a draft visible in the ERPNext desk; round-trip `external_id` stored

- [ ] **P5 · Agent-loop UAT**
  - [ ] Run README markdown demo against real ERPNext
  - [ ] Approve markdown → real Pricing Rule created
  - [ ] Capture before/after screenshots in PR

- [ ] **P6 · Docs + tests**
  - [ ] README section "Running with real ERPNext" (compose, seed, env, sanity curl)
  - [ ] Troubleshooting (auth, schedule_date format, doctype permissions, port conflicts)
  - [ ] Live-path test cases env-gated; CI stays mock-only

---

## Mautic
- [ ] P1 · Local instance
- [ ] P2 · Demo seed
- [ ] P3 · Inbound sync
- [ ] P4 · Live outbound apply
- [ ] P5 · Agent-loop UAT
- [ ] P6 · Docs + tests

## Medusa
- [ ] P1 · Local instance
- [ ] P2 · Demo seed
- [ ] P3 · Inbound sync
- [ ] P4 · Live outbound apply
- [ ] P5 · Agent-loop UAT
- [ ] P6 · Docs + tests

## OpenBoxes
- [ ] P1 · Local instance
- [ ] P2 · Demo seed
- [ ] P3 · Inbound sync
- [ ] P4 · Live outbound apply
- [ ] P5 · Agent-loop UAT
- [ ] P6 · Docs + tests

## Akeneo
- [ ] P1 · Local instance
- [ ] P2 · Demo seed
- [ ] P3 · Inbound sync
- [ ] P4 · Live outbound apply
- [ ] P5 · Agent-loop UAT
- [ ] P6 · Docs + tests

## Superset
- [ ] P1 · Local instance
- [ ] P2 · Demo seed (point at our spine.db / mirror)
- [ ] P3 · Inbound sync
- [ ] P4 · Live outbound apply (mostly N/A — Superset is read-only)
- [ ] P5 · Agent-loop UAT
- [ ] P6 · Docs + tests

---

# Track 2 — Agent mesh upgrade

Goal: instead of `Chief → one specialist → artifact`, run a multi-agent dialogue where specialists peer-review each other's drafts and the Chief only synthesizes after critique converges. Operator should see drafts → critique → revision → final, not just the final report.

Today's loop (`backend/app/agents/chief_of_staff.py`):
- Chief calls `delegate_to_<specialist>` once per turn.
- Specialist tool-calls substrate, writes one artifact, returns final text.
- Chief synthesizes a 5–10-line operator reply.
- No second pass. No critique. Whatever the specialist wrote on first try ships.

Failure modes this fails to catch: shallow analysis, missed evidence, wrong category, miscalibrated confidence, recommendations that violate the policy table the operator hasn't even surfaced yet.

## Phases

- [x] **A1 · Critic agent (single-pass review)** _(merged: `feature/agent-mesh-a1-critic` → main)_
  - [x] `backend/app/agents/critic.py` — read-only Critic specialist with the spec'd system prompt (facts / gaps / risks / counter-rec / overclaim) and a forced `kind="critique"` write_artifact.
  - [x] 11 tools: `read_artifact`, `read_events`, plus the Analyst's read-only spine kit (`query_sales`, `aggregate_by_category`, `daily_sales`, `get_kpis`, `list_categories`, `list_campaigns`, `inventory_health`, `list_orders`) and `write_artifact`.
  - [x] `chief_of_staff.delegate_to_critic` takes `{artifact_id, task?}` and stitches the id into the task so the Critic's first call is always `read_artifact(artifact_id=…)`.

- [ ] **A2 · Drafter / critic round-trip**
  - [ ] Each specialist (Analyst, Pricing, Marketing, Merchandiser, Fulfillment, Replenishment, Store Manager) gains an optional `revise_artifact(original_id, critique_id)` tool that reads its prior draft + the critique and produces a revised artifact (kind suffix `_revised`).
  - [ ] Chief's run-loop is updated: after a specialist returns a draft, Chief invokes Critic, then asks the original specialist to `revise_artifact` if the critique flagged Gaps or Risks.
  - [ ] Convergence rule: max **2 revision rounds**, or stop earlier when the latest critique returns "no material gaps".
  - **Done when:** a single chat turn produces draft → critique → revision artifacts in the spine, all linked, and the operator-facing reply references the *final* revision.

- [ ] **A3 · Multi-specialist debate (where it helps)**
  - [ ] For decisions where specialists naturally disagree (Pricing vs Replenishment on markdowns; Merchandiser vs Marketing on push priority), the Chief can invoke `delegate_to_<peer>` with the first specialist's draft as input and ask for a peer review (`peer_review` tool) — distinct from generic Critic, scoped to that peer's domain expertise.
  - [ ] Peer reviews are written as `peer_review` artifacts and feed into the same revision loop.
  - **Done when:** a markdown plan triggers Pricing draft → Replenishment peer review → Pricing revision → Critic critique → Pricing final, all visible in the trace pane.

- [ ] **A4 · UI surfacing**
  - [ ] Chat turn renders a stacked thread: each artifact tagged by stage (`draft`, `critique`, `peer review`, `revision`, `final`) with an inline "diff" affordance.
  - [ ] Reports tab gets a `stage` column and a filter for `final` only (default), with a toggle to show all stages.
  - [ ] Approval drawer's `apply` button refuses to fire on a non-`final` artifact.
  - **Done when:** operator can scrub through the dialogue and apply only on the converged final.

- [ ] **A5 · Eval harness**
  - [ ] `backend/tests/agents/eval/` — a small suite of seeded scenarios (overstock summer, weekend heatwave, supplier risk, single-store stockout) with a golden answer per scenario.
  - [ ] Each scenario runs the chat turn end-to-end and scores (LLM-as-judge): factual correctness, evidence cited, policy adherence, recommendation quality.
  - [ ] Compare single-pass (mesh disabled) vs multi-pass (mesh enabled). The multi-pass run must score strictly higher on at least 3 of 4 dimensions on at least 3 of the 4 scenarios.
  - **Done when:** numbers exist and are reproducible; mesh stays on by default.

- [ ] **A6 · Cost & latency guardrails**
  - [ ] Per-turn token budget; if mesh would exceed it, automatically downgrade to single-pass and log a `mesh_downgrade` event.
  - [ ] Backoff: max 3 rounds of revision globally, max 2 critic invocations per draft, hard wall-clock cap (e.g. 60s per operator turn).
  - **Done when:** budget config is in `backend/app/config.py`, surfaced in the cockpit's status strip as a small chip when active.

## Open design questions (decide during A1 brainstorming)

- Critic as one universal agent vs N domain critics (pricing-critic, marketing-critic, …)?
- Revision authored by original specialist vs by the Critic itself acting as writer?
- Should the Chief be allowed to ignore the Critic ("override flag") or always defer?
- Streaming UX: render drafts immediately or wait until convergence? (Lean: stream, mark stage; A4 surfaces this.)
- LLM-as-judge for A5: same provider as production? Different provider for less correlation? Quorum?

---

# Track 3 — Native app

Goal: ship a native client (mobile + desktop) that wraps the HQ Console — push notifications for approvals, biometric unlock, offline tape replay. Ride on top of all backend work; should not require backend changes.

Decide between two paths during scoping:

- **Tauri + the existing Vite/React frontend** — desktop-first (macOS/Windows/Linux), thin Rust shell, smallest delta from the web build. Mobile via Tauri 2.0 (iOS/Android) once it stabilizes for our stack.
- **React Native (Expo) re-skin** — mobile-first, native push and biometrics out of the box, but doubles the UI codebase since the Bloomberg-terminal density doesn't translate cleanly to phones. Likely needs a new "operator at the store" layout, not the cockpit.

## Phases (start after at least 2 systems have completed P1–P6)

- [ ] **B1 · Scoping spike** — pick path (Tauri vs RN), validate SSE chat works, write a one-pager.
- [ ] **B2 · Desktop shell (Tauri)** — wraps the Vite build, bundles for mac/win/linux, wires deep-link to a hosted backend or `localhost`.
- [ ] **B3 · Push for approvals** — when an action enters the queue with `approval_required`, fire an OS notification (`tauri-plugin-notification` / APNs / FCM). Tap → opens drawer.
- [ ] **B4 · Biometric unlock** — Touch ID / Face ID gate before drawer apply. Falls back to password.
- [ ] **B5 · Offline tape replay** — last 200 events cached locally; tape reads cache when network is down; sync on reconnect.
- [ ] **B6 · Mobile companion** — phone-shaped layout (no 3-rail cockpit). Approvals + chat + tape only. Either Tauri 2.0 if it's ready or a thin RN shell sharing api.ts.
- [ ] **B7 · Distribution** — signed binaries + an auto-updater (`tauri-plugin-updater`), TestFlight track for iOS if RN.

Out of scope for this track: real-time multi-user collab, video, voice chat with the agent (separate initiative if it ever happens).

---

# Track 4 — MLflow + MLOps

Goal: every operator turn (and every offline eval run) is logged as a tracked experiment with prompts, traces, scored outputs, and the exact provider/model/version. Without this we can't tell whether changes to the agent mesh, the spec, or the model actually made things better.

Pairs naturally with Track 2 A5 (eval harness) — A5 produces scores, MLflow gives them a home, a UI, and a comparison surface.

## Phases

- [ ] **M1 · Local MLflow stack**
  - [ ] `infra/mlflow/docker-compose.yml` — single-host: postgres backend store + minio (or local fs) artifact store + mlflow tracking server on `:5000`.
  - [ ] Makefile targets: `mlflow-up`, `mlflow-down`, `mlflow-status`.
  - **Done when:** `http://localhost:5000` shows the empty MLflow UI.

- [ ] **M2 · Trace logger in the agent run-loop**
  - [ ] `backend/app/llm/tracing.py` wraps each LLM call: log prompt, system, tool list, response, token counts, model, provider, wall-clock, parent run id.
  - [ ] One MLflow run per operator turn; nested runs per specialist delegate; further nesting per critic round (Track 2 A1).
  - [ ] Tags: `agent`, `phase` (draft/critique/revision/final), `provider`, `model`.
  - **Done when:** running the README demo prompt creates one parent run with N nested runs visible in MLflow UI.

- [ ] **M3 · Eval harness pipes to MLflow**
  - [ ] Track 2 A5 scenarios run as MLflow experiments named `eval/<scenario>/<git-sha>`.
  - [ ] Metrics logged: factual_correctness, evidence_cited, policy_adherence, recommendation_quality, latency_ms, tokens_in, tokens_out.
  - [ ] Artifacts logged: full transcript, all generated reports, the judge's reasoning.
  - **Done when:** `mlflow ui` shows side-by-side comparison of single-pass vs multi-pass runs across all scenarios with a green delta on at least 3 of 4 dimensions on at least 3 of 4 scenarios.

- [ ] **M4 · Prompt + model registry**
  - [ ] System prompts for each agent versioned in MLflow Model Registry (or a lightweight equivalent — `prompts/<agent>/v<n>.md` + an MLflow-tracked alias).
  - [ ] At runtime the agent fetches the active prompt by alias (e.g. `Analyst@prod`), with a kill-switch env var to fall back to the file in repo.
  - **Done when:** flipping an alias in MLflow UI changes the next operator turn's behavior without a code deploy.

- [ ] **M5 · Online drift + cost dashboards**
  - [ ] Periodic job logs daily aggregates: per-agent token spend, per-tool error rate, per-scenario score drift vs the eval baseline.
  - [ ] Surface in the cockpit's Reports tab as a "telemetry" kind, or a thin Streamlit/Superset board pointed at MLflow's tracking DB.
  - **Done when:** an unexpected provider switch or prompt regression shows up in the dashboard within 24h.

- [ ] **M6 · CI gate**
  - [ ] On every PR that touches `backend/app/agents/` or `backend/app/llm/`, a CI job runs the small eval suite and posts the MLflow run ids + a delta table as a PR comment.
  - [ ] Block merge if any dimension regresses by more than a configurable threshold (e.g. 5%).
  - **Done when:** a deliberately bad prompt change is blocked by the gate and the PR comment explains why.

## Open design questions (decide during M1 brainstorming)

- MLflow vs Langfuse vs Phoenix? MLflow chosen for breadth (works for non-LLM models too once we add forecasting), but Langfuse has better LLM-trace UX out of the box. Compare during M1.
- Self-host vs MLflow Cloud? Self-host for now; revisit if multiple people need access.
- Where to keep the prompts of record — repo (`prompts/`) or registry-only? Repo + alias lets us code-review prompts.

---

# Track 5 — Agentic Wiki

Goal: agents accumulate a durable, searchable body of knowledge from prior decisions instead of re-deriving everything per turn. Today every operator question starts from zero. The Wiki gives every specialist a way to write down "what we tried, what worked, what didn't, when" and read it back in future turns.

Pairs with Track 2 (mesh) and Track 4 (MLflow) — critique loops surface lessons, MLflow scores let us promote a lesson from "draft" to "settled". The Wiki is the durable substrate those lessons live in.

## Concept

- One markdown page per topic. Topics are agent-coined (`category/summer_apparel/markdown_playbook`, `vendor/breezeco/reliability_notes`, `policy/margin_floors`).
- Pages are versioned (each agent edit = a commit-like event in the spine event log, kind=`wiki_edit`).
- Pages link to the spine event ids and artifact ids they were derived from — every claim has a citation back into the audit log.
- Every specialist gains `wiki_search`, `wiki_read`, `wiki_propose_edit` tools. Edits are draft-only; promotion to `published` requires either operator approval or a Critic agent (Track 2 A1) sign-off.

## Phases

- [ ] **W1 · Storage + spine wiring**
  - [ ] New `wiki_pages` table in `spine.db`: `slug PRIMARY KEY, title, body_md, owner_agent, status (draft/published/deprecated), version, updated_ts, refs_json`.
  - [ ] New `wiki_revisions` table: full history per slug.
  - [ ] Spine event kinds added: `wiki_edit`, `wiki_publish`, `wiki_deprecate`.
  - **Done when:** programmatic create/edit round-trips through the spine.

- [ ] **W2 · Read tools across specialists**
  - [ ] `wiki_search(query, limit)` — keyword + tag search; reuses the existing artifact body search pattern.
  - [ ] `wiki_read(slug)` — returns body + citations + last-updated.
  - [ ] All specialists get both tools by default (read-only). System prompts updated to encourage citing the wiki.
  - **Done when:** Analyst answers a familiar question by quoting a wiki page with the slug surfaced in the trace.

- [ ] **W3 · Write tools (propose-edit)**
  - [ ] `wiki_propose_edit(slug, body_md, refs)` writes a `draft` revision and emits `wiki_edit`.
  - [ ] Drafts surface in the cockpit's approval rail beside outbox actions; operator approves → `published`.
  - **Done when:** an Analyst running a fresh investigation can leave a published learning behind that the next turn reads.

- [ ] **W4 · Critic-gated auto-publish**
  - [ ] When Track 2 A1 Critic is online, drafts that survive critique with no Risks/Gaps auto-publish.
  - [ ] Operator override always wins.
  - **Done when:** a clean draft promotes itself within one turn without operator action; a flagged draft sits in approvals.

- [ ] **W5 · UI surface**
  - [ ] New `[WIKI]` tab in the cockpit's data rail (search + recent edits).
  - [ ] Drawer renders pages with citations as inline links to the source events / artifacts.
  - [ ] Stage chip (`draft` / `published` / `deprecated`) reused from the chip-token system.
  - **Done when:** operator can pin a wiki page from the drawer; pinned slugs surface in the status strip as a chip.

- [ ] **W6 · Cross-agent learning loop**
  - [ ] After every chat turn, a background `Wiki Curator` agent (read-only over the turn's events + new artifacts) decides whether anything is wiki-worthy and proposes drafts.
  - [ ] Rate-limited: max 3 proposals per turn; max 1 proposal per slug per day.
  - [ ] Curator runs use the same MLflow tracing path (Track 4 M2) so we can score whether wiki growth is helping.
  - **Done when:** a week of operator turns produces a non-trivial wiki, and an A/B comparison (Track 4 M3) shows scenarios where the wiki is read score higher.

## Open design questions (decide during W1 brainstorming)

- Slug shape — agent-coined free-form vs constrained namespaces (`category/<x>`, `vendor/<x>`, `policy/<x>`)? Constrained scales better.
- Auth model — should specialists be allowed to edit each other's pages, or only their own (with peer-review via Track 2 A3)?
- Decay — pages need a "last verified" stamp + a way to flag stale entries; otherwise the wiki rots.
- Embedding-based search vs sqlite FTS? Start with FTS; revisit if recall is poor.
