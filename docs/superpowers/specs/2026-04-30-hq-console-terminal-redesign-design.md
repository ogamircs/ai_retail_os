# HQ Console — Trading-Terminal Redesign

**Date:** 2026-04-30
**Status:** Draft
**Scope:** Frontend UI redesign of the AI Retail OS HQ Console. No backend or contract changes.

## Goal

Restyle the HQ Console from its current paper-editorial layout into a dense, dark, monospace trading-terminal aesthetic. Restructure the layout from three free-floating rails into a fixed status strip + three-rail middle + event tape, with a dedicated approval rail surfacing the human-in-the-loop choke point. Keep the existing demo paths working unchanged.

## Decisions locked during brainstorming

| # | Question | Decision |
|---|---|---|
| 1 | Visual direction (4 mockups: editorial, terminal, Linear, ops-console) | **B · Trading terminal** — dark, monospace, ultra-dense, status-by-color-light |
| 2 | Layout topology (4 skeletons) | **Status bar + event tape** — thin always-on KPI strip top, scrolling event tape bottom, working surface in between |
| 3 | Middle-area split (3 variants) | **β · three rails** — `data (1.5fr) | chat (1fr) | approvals + artifacts (0.7fr)` |
| 4 | Palette flavor (4 flavors: Bloomberg, phosphor, modern-dark, light) | **α · Bloomberg classic** — pure black bg, amber accent, traffic-light statuses |
| 5 | Approval interaction (one-click / drawer / inline) | **Drawer** — click row → side drawer with payload + linked artifact + apply / reject |

## Architecture

### File scope

| Action | File |
|---|---|
| New | `frontend/src/lib/tokens.css` — design-token sheet (palette, type, spacing) |
| New | `frontend/src/lib/data.ts` — `useDashboardData()` hook lifting fetch logic out of `Dashboard.tsx` |
| New | `frontend/src/components/StatusStrip.tsx` |
| New | `frontend/src/components/DataRail.tsx` |
| New | `frontend/src/components/ApprovalRail.tsx` |
| New | `frontend/src/components/ApprovalDrawer.tsx` |
| Renamed | `frontend/src/components/EventLog.tsx` → `EventTape.tsx` |
| Refactored | `frontend/src/App.tsx` — new outer grid |
| Refactored | `frontend/src/components/Chat.tsx` — restyle, add collapsible agent-trace pane, input history |
| Refactored | `frontend/src/components/Dashboard.tsx` — split into rail components; thin remainder may collapse entirely |
| Refactored | `frontend/src/components/Artifacts.tsx` — restyle as compact list inside `ApprovalRail` |
| Replaced | `frontend/src/App.css` — full rewrite over tokens (~800–1000 lines) |
| Removed | `frontend/src/components/AgentBadge.tsx` — provider switcher folds into `StatusStrip` |

**No new dependencies.** React 18, Vite, TypeScript, `marked` already in tree.

**No backend changes.** All endpoints reused as-is; "reject" is purely client-side filter (no `DELETE` added).

### Migration approach

Keep current `App.css` for a single commit as `App.legacy.css`, switch the import in `main.tsx` to the new `tokens.css` + per-component CSS modules co-located with components. Delete `App.legacy.css` once parity is confirmed manually against the demo paths from `README.md`.

`Dashboard.tsx` currently does all fetching in a single effect. Hoist to `useDashboardData()` returning `{kpis, categories, campaigns, stores, actions, skus, inbound, systems, syncRuns, refresh, error, loading}`. Keep the existing 5s polling cadence.

## Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│ STATUS STRIP   28px                                                  │
│  RETAIL-OS · provider chip · UTC · KPI chips · APPROVE n!            │
├──────────────────────────┬──────────────────┬──────────────────────┤
│ DATA RAIL  1.5fr         │ COMMAND RAIL     │ APPROVAL RAIL        │
│  Tabs                    │  1fr             │  0.7fr (min 280)     │
│  [CAT] STR INV CMP INT   │                  │                      │
│  Sortable dense table    │  Chat thread     │  PENDING (n)         │
│                          │  + agent trace   │   ▶ rows             │
│                          │  collapsible     │  ──────              │
│                          │                  │  ARTIFACTS (recent 6)│
│                          │  > _             │                      │
├──────────────────────────┴──────────────────┴──────────────────────┤
│ EVENT TAPE   24px                                                    │
│  agent · kind · summary  ◆  agent · kind · summary  ◆  …             │
└─────────────────────────────────────────────────────────────────────┘
```

**CSS:**
- Outer: `display: grid; grid-template-rows: 28px 1fr 24px;`
- Middle: `display: grid; grid-template-columns: minmax(420px, 1.5fr) minmax(360px, 1fr) minmax(280px, 0.7fr);`
- Cells separated by 1px `--line` hairlines, no rounded corners.

**Approval drawer:** right-anchored over the middle area, `width: min(720px, 60vw)`, slides in 160ms. Backdrop click or `Esc` closes.

**Min viewport:** 1280×720. Below 1280px, approval rail collapses into a button in the status strip; chip count shows pending.

## Visual system

### Tokens

```css
:root {
  /* surface */
  --bg:        #000000;
  --panel:     #0a0a0a;
  --panel-2:   #111111;
  --line:      #1a1a1a;
  --line-hot:  #2a2a2a;

  /* ink */
  --ink:       #e6e6e6;
  --ink-dim:   #8a8a8a;
  --ink-mute:  #5a5a5a;

  /* accent — Bloomberg amber */
  --amber:     #ff8800;
  --amber-dim: #a85a00;

  /* status — traffic light */
  --good:      #33ff66;
  --warn:      #ffcc00;
  --bad:       #ff3333;
  --info:      #66aaff;

  /* chip backgrounds */
  --good-bg:   #0a3d10;
  --warn-bg:   #3d2a00;
  --bad-bg:    #3d0a0a;
  --info-bg:   #0a1f3d;

  /* agent ink */
  --ag-chief:        #ff8800;
  --ag-analyst:      #66aaff;
  --ag-pricing:      #ffcc00;
  --ag-marketing:    #ff66aa;
  --ag-merchandiser: #aa88ff;
  --ag-fulfillment:  #66ffaa;
  --ag-replenishment:#ff8866;
  --ag-store:        #aaccff;
  --ag-integration:  #888888;
}
```

### Typography

- One family, monospace everywhere: `ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace`.
- Hierarchy via weight + color + uppercase, never via face.
- Sizes: `--fs-xs: 10px` (tape, chips, timestamps), `--fs-sm: 11px` (body, table, chat), `--fs-md: 12px` (panel headers, KPI labels), `--fs-lg: 14px` (KPI values, drawer headings), `--fs-xl: 18px` (drawer title only).
- Line height: 1.4 body, 1.5 chat, 1.3 tape.
- Letter-spacing: `0.04em` on uppercased labels only.
- No bold over 600.

### Spacing

- 4px baseline. `--sp-1: 4px` … `--sp-6: 24px`.
- Panel padding `8px 10px`.
- Row padding `4px 8px`.
- No border-radius anywhere.

### Status language

| Status | Token | Used by |
|---|---|---|
| good / connected / measured / applied | `--good` / `--good-bg` | integrations connected, campaigns post-measure, applied outbox actions |
| warn / approval_required / draft / proposed | `--warn` / `--warn-bg` | action-queue items pending approval, drafts |
| bad / rollback / blocked | `--bad` / `--bad-bg` | failed syncs, blocked policies |
| info / mock / sync_in_progress | `--info` / `--info-bg` | mock-mode systems, in-flight syncs |

Existing `status-*` classes in `App.css` get a 1:1 mapping to these tokens during the rewrite.

### Iconography

ASCII only: `▶` (action), `◆` (separator), `▲ ▼` (delta), `✓ ✗` (resolved), `─ ├ └` (trace tree), `> _` (input prompt). No SVGs, no emoji. The current `⬢` brand glyph is dropped — `RETAIL-OS` is the brand mark.

## Components

### `StatusStrip.tsx` (new) — 28px, full width

Left to right: `RETAIL-OS` brand (amber) · provider chip clickable for switch · UTC clock (1s tick) · spacer · KPI chips · spacer · `APPROVE n!` (pulses amber when `n > 0`, click opens drawer focused on first pending).

KPI chips render directly from `/api/kpis` → `KpiResponse.kpis[]` (each has `label`, `value`, `format`, `delta`). Source of truth is `executive_kpis()` in `backend/app/substrate/omnichannel.py`. The chip column maps as:

| Chip label | Backend label (`Kpi.label`)   | `format`   |
|------------|-------------------------------|------------|
| `REV.30D`  | `30d omnichannel revenue`     | currency   |
| `MGN`      | `Gross margin`                | percent    |
| `RISK`     | `Inventory at risk`           | number     |
| `CAMP`     | `Active campaigns`            | number     |
| `SUPP`     | `Supplier risks`              | number     |
| `STORE`    | `Store exceptions`            | number     |

The chip short-label is derived in the frontend (lookup table on `Kpi.label`). The `APPROVE n!` chip is **not** part of `/api/kpis`; it counts items from `/api/action-queue` where `status === "approval_required"` plus outbox actions where `status` ∈ `{mock_only, proposed}` (see `ApprovalRail` source rules below).

Polls `/api/kpis` every 5s. Provider chip writes via `POST /api/config`.

### `DataRail.tsx` (new) — left middle, 1.5fr

Tab bar: `[CATEGORIES] STORES INVENTORY CAMPAIGNS INTEGRATIONS`. Active tab amber-uppercase, others `--ink-dim`. Active tab persisted to `localStorage`.

Tab body is one dense table per tab. Rules:
- One row = one entity, no multi-line rows.
- Numeric columns right-aligned, `font-variant-numeric: tabular-nums`.
- Hover row → `--panel-2`.
- Click row that has an outstanding action → opens approval drawer pre-filled.
- Sortable column headers (asc/desc toggle, simple stable sort, no library).

Per-tab columns:
- **Categories**: name · 7d revenue · margin · units · pressure score · push score · last action.
- **Stores**: id · region · capacity · labor · demand · weather · exception.
- **Inventory**: SKU · category · on-hand · reorder · price · base · risk.
- **Campaigns**: id · category · channel · budget · projected ROI · actual ROI · status.
- **Integrations**: system · domain · mode · last sync · last status · `[sync]` button.

### `Chat.tsx` (refactored) — middle, 1fr

- Header: `CHIEF OF STAFF · n specialists · [ trace ▾ ]`.
- Body: alternating user / chief blocks. User = `> message` amber. Chief = `[hh:mm] CHIEF` then text.
- `[trace]` toggle splits the rail 50/50; bottom half shows agent-trace tree (`├ Pricing 14:38 ✓`), each agent inked per `--ag-*` token, tool calls collapsible children.
- Input: `> _` prompt at bottom. `Enter` submits, `Shift+Enter` newline. `↑/↓` cycles 20-deep local input history.
- Existing SSE stream handler unchanged. Chat events stay in-place; trace events feed the trace pane only.

### `ApprovalRail.tsx` (new) — right middle, 0.7fr

Two stacked sections.

**`PENDING` (top)** — a unioned list from two sources, deduped where they reference the same logical action:

| Source | Endpoint | Filter |
|---|---|---|
| Action queue | `GET /api/action-queue` → `ActionItem[]` | `status === "approval_required"` |
| Integration outbox | nested `ActionItem.external_actions[]` (already returned in the same payload as `ExternalAction[]`) | `status ∈ {mock_only, proposed}` |

When an `ActionItem` has both a top-level `approval_required` status and one or more `external_actions` matching the outbox filter, the row is shown once with a stacked indicator showing all systems (e.g. `erpnext + mautic`). The drawer surfaces each external action distinctly.

Row format:
```
▶ <title>           <agent>
  <one-line context> · ←<hh:mm>
```
Click row → `ApprovalDrawer` opens for that action.

**`ARTIFACTS` (bottom)** — last 6 from `/api/artifacts`. One line each: `agent · kind · title · ts`. Click → drawer with markdown render.

### `ApprovalDrawer.tsx` (new)

Right-anchored, `width: min(720px, 60vw)`, full middle-area height, slide 160ms. Closes on backdrop click, `Esc`, or `[ ✗ close ]`.

- Header: agent badge (inked per `--ag-*`) · action title · timestamp.
- Body: payload as a definition-list (label / value rows, monospace) + linked artifact rendered via existing `marked`.
- Footer: `[ apply → external ]` (amber primary) and `[ reject ]` (dim).

`apply` → `POST /api/integrations/{system}/actions/{id}/apply`. On success the drawer flips to a confirmation panel showing `applied_mock` or `draft_created` for 2s before auto-closing.

`reject` is **local-only by design for v1**: removes the row from the in-memory rail filter until the next 5s refresh, after which the underlying action returns. This is acceptable for the demo: the operator's intent is "skip for now," not "tell the backend to forget about this." A real `DELETE /api/action-queue/{id}` (or a `dismissed` status field) is deferred — see Out of Scope.

### `EventTape.tsx` (refactor of `EventLog.tsx`) — 24px, full width

Single line, discrete append. Latest 30 events concatenated with `◆` separators, agent inked per `--ag-*`, kind in `--ink-dim`. Polls `/api/events?since=<lastId>` every 2s. Clicking the tape (or pressing `\``) opens the event drawer (same drawer component as approvals; different content) with last 200 events filterable by agent and kind.

## States and behaviors

### Loading / empty / error

- First mount: status strip shows `RETAIL-OS · loading…`; rails show centered `── loading ──` in `--ink-mute`. No skeleton shimmer.
- Empty: dim line, e.g. `── no pending approvals ──`.
- Fetch error: red chip in status strip `data link · err`; affected rail shows `── connection lost · retry in 5s ──`. No toasts.
- Tape lag: if poll falls behind by >10s, prepend `… catching up …` until back in sync.

### Live behaviors

- KPIs / tabs / approval rail / artifacts: 5s poll (existing cadence).
- Tape: 2s poll on `/api/events?since=<lastId>`, append-only.
- Chat SSE: unchanged.
- UTC clock: 1s tick.

### Keyboard

- `\`` — open event tape drawer
- `Esc` — close any drawer
- `Cmd+K` — focus chat input
- `↑ / ↓` in chat — cycle input history (20 deep)
- `1`–`5` while data rail focused — jump tabs

## Out of scope

- New backend endpoints. `DELETE /api/action-queue/{id}` for hard-reject is **not** added.
- Mobile / tablet responsive design. Min 1280×720 stays.
- Light-mode toggle.
- Real charts or sparklines. Numeric deltas only (`▲4.2`).
- Replacing `marked` for artifact rendering.
- Continuous-scroll marquee for the tape (it's discrete-append).
- Auth, multi-tenancy, role-gated approvals.
- Frontend test runner / snapshot tests (none exists today; this redesign doesn't add one).

## Done criteria

1. Cockpit renders status strip + 3 rails + tape on default seed data.
2. All 5 data tabs render their tables, sortable.
3. Approval rail shows pending action-queue items with `approval_required` status.
4. Drawer opens, applies an outbox action, shows result chip.
5. Tape appends new events live; drawer opens via click and `\``.
6. Chat works end-to-end with the demo paths from `README.md` (markdown demo and omnichannel demo) and visibly streams specialist events.
7. Provider switcher in status strip changes `LLM_PROVIDER` via `POST /api/config` and the next chat call uses the new provider.
8. `backend/tests/test_omnichannel.py` and `backend/tests/test_integrations.py` continue to pass — no API surface change.

## Risk / unknowns

- `App.css` is 1180 lines and likely encodes contributor assumptions. Mitigation: keep current sheet as `App.legacy.css` for one commit; delete in a follow-up after manual parity check.
- `Dashboard.tsx` (424 lines) does all fetching inline. Hoisting to `useDashboardData()` is the largest single diff.
- Three concurrent data loops (chat SSE + KPI 5s + tape 2s). Existing app already does this; no new pressure.

## Manual UAT checklist

- [ ] Status strip live updates KPI chips at 5s cadence.
- [ ] Approve count chip pulses when `n > 0`, dims to muted at `n == 0`.
- [ ] Tab switching persists across page reload (`localStorage`).
- [ ] Each tab table sorts on column-header click (toggles asc/desc).
- [ ] Click row with outstanding action → drawer opens pre-filled.
- [ ] Drawer apply (mock-mode) → flips to `applied_mock` confirmation, auto-closes after 2s.
- [ ] Drawer apply (with creds set) → flips to `draft_created` confirmation.
- [ ] Drawer reject → row removed locally; reappears on next 5s refresh until backend dismissed.
- [ ] Tape drawer opens via `\`` and via tape click; filters by agent and kind.
- [ ] Chat SSE during long delegation streams specialist events live.
- [ ] Chat input history cycles with `↑ / ↓`.
- [ ] `Cmd+K` focuses chat input from anywhere.
- [ ] Provider switch in status strip updates the provider for the next chat call.
- [ ] Demo paths from `README.md` (markdown demo, omnichannel demo) complete without console errors.
