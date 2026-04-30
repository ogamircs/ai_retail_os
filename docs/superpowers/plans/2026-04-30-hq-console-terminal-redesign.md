# HQ Console Terminal-Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the AI Retail OS HQ Console frontend into a Bloomberg-style trading-terminal cockpit (status strip + 3 middle rails + event tape + drawer-based approvals), per `docs/superpowers/specs/2026-04-30-hq-console-terminal-redesign-design.md`.

**Architecture:** Frontend-only refactor of `frontend/src/`. Introduce a token-based CSS layer (`lib/tokens.css`), hoist Dashboard data fetching into a `useDashboardData()` hook, decompose the monolithic `Dashboard.tsx` (424 lines) into focused rail components, replace the floating modal-based artifact viewer with a unified `ApprovalDrawer`, and move the EventLog to a full-width `EventTape` at the bottom. No backend changes; all endpoints reused as-is.

**Tech Stack:** React 18, Vite 5, TypeScript 5, `marked` 14. No new dependencies.

---

## Working assumptions for this plan

- The backend is running on `127.0.0.1:8000` with seeded data (`python -m app.substrate.seed`). The frontend dev server runs at `http://localhost:5173` via `npm run dev`. Vite proxies `/api/*` to the backend.
- There is **no frontend test runner** in this repo. Verification per task is manual (visual + console + curl), not automated. Backend tests (`backend/tests/test_omnichannel.py`, `backend/tests/test_integrations.py`) must continue to pass; run them once at the end.
- Each task ends with an atomic commit. The final commit closes the legacy CSS removal.
- Relative file paths in this plan are from the repo root `/Users/ymir/git_repos/ai_retail_os/`.

## File structure

### New files
| Path | Responsibility |
|---|---|
| `frontend/src/lib/tokens.css` | Design tokens: palette, typography, spacing |
| `frontend/src/lib/data.ts` | `useDashboardData()` hook unifying all dashboard polling |
| `frontend/src/lib/agentInk.ts` | `agentInk(name)` returning a CSS variable name from agent string |
| `frontend/src/components/StatusStrip.tsx` | Top 28px strip: brand, provider, clock, KPI chips, approve count |
| `frontend/src/components/StatusStrip.css` | Status strip styles |
| `frontend/src/components/DataRail.tsx` | Left middle rail: tabbed dense tables |
| `frontend/src/components/DataRail.css` | Data rail styles |
| `frontend/src/components/dataTabs/CategoriesTab.tsx` | Categories table |
| `frontend/src/components/dataTabs/StoresTab.tsx` | Stores table |
| `frontend/src/components/dataTabs/InventoryTab.tsx` | Inventory table |
| `frontend/src/components/dataTabs/CampaignsTab.tsx` | Campaigns table |
| `frontend/src/components/dataTabs/IntegrationsTab.tsx` | Integrations table with sync button |
| `frontend/src/components/dataTabs/sortable.ts` | Tiny `useSort()` helper for column sort state |
| `frontend/src/components/ApprovalRail.tsx` | Right middle rail: pending + artifacts |
| `frontend/src/components/ApprovalRail.css` | Approval rail styles |
| `frontend/src/components/ApprovalDrawer.tsx` | Right-anchored slide-in drawer |
| `frontend/src/components/ApprovalDrawer.css` | Drawer styles |
| `frontend/src/components/EventTape.tsx` | Bottom 24px tape + tape drawer |
| `frontend/src/components/EventTape.css` | Tape styles |
| `frontend/src/lib/drawerContext.tsx` | Tiny context for opening drawers from anywhere |

### Modified files
| Path | Change |
|---|---|
| `frontend/src/main.tsx` | Replace `import "./App.css"` with `import "./lib/tokens.css"` and per-component CSS imports (each component imports its own .css). |
| `frontend/src/App.tsx` | New outer grid; mount `StatusStrip`, `DataRail`, `Chat`, `ApprovalRail`, `EventTape`, `ApprovalDrawer` provider. |
| `frontend/src/components/Chat.tsx` | Restyle to terminal aesthetic; add agent-trace pane toggle; add input history with ↑/↓; co-located `Chat.css`. |
| `frontend/src/components/Chat.css` (new) | Chat styles |

### Renamed
| Path | Now |
|---|---|
| `frontend/src/components/EventLog.tsx` | → `frontend/src/components/EventTape.tsx` (replaced, not git-renamed; the new file lives alongside the old until the final cleanup task) |

### Removed (final cleanup task)
| Path | Reason |
|---|---|
| `frontend/src/components/AgentBadge.tsx` | Folded into `StatusStrip` |
| `frontend/src/components/EventLog.tsx` | Replaced by `EventTape` |
| `frontend/src/components/Artifacts.tsx` | Folded into `ApprovalRail` + `ApprovalDrawer` |
| `frontend/src/components/Dashboard.tsx` | Decomposed into `DataRail` + `StatusStrip` + `useDashboardData()` |
| `frontend/src/App.css` | Replaced by `tokens.css` + per-component sheets |

---

## Phase 0 — Tokens and reset

### Task 0.1: Add design tokens

**Files:**
- Create: `frontend/src/lib/tokens.css`

- [ ] **Step 1: Create the tokens stylesheet**

```css
/* Design tokens for the HQ Console terminal aesthetic.
   Spec: docs/superpowers/specs/2026-04-30-hq-console-terminal-redesign-design.md */

:root {
  color-scheme: dark;

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

  /* type */
  --font-mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  --fs-xs: 10px;
  --fs-sm: 11px;
  --fs-md: 12px;
  --fs-lg: 14px;
  --fs-xl: 18px;
  --lh-tight: 1.3;
  --lh: 1.4;
  --lh-loose: 1.5;

  /* spacing 4px grid */
  --sp-1: 4px;
  --sp-2: 8px;
  --sp-3: 12px;
  --sp-4: 16px;
  --sp-5: 20px;
  --sp-6: 24px;
}

* {
  box-sizing: border-box;
}

html,
body,
#root {
  height: 100%;
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  line-height: var(--lh);
}

body {
  font-variant-numeric: tabular-nums;
}

button,
input,
textarea,
select {
  font: inherit;
  color: inherit;
  background: transparent;
  border: 1px solid var(--line);
  padding: var(--sp-1) var(--sp-2);
}

button {
  cursor: pointer;
}

button:hover:not(:disabled) {
  border-color: var(--line-hot);
}

button:disabled {
  color: var(--ink-mute);
  cursor: not-allowed;
}

a {
  color: var(--info);
  text-decoration: none;
}

.label {
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--ink-dim);
  font-size: var(--fs-xs);
}

.chip {
  display: inline-block;
  padding: 0 var(--sp-1);
  border: 1px solid var(--line-hot);
  font-size: var(--fs-xs);
  white-space: nowrap;
}
.chip-good { color: var(--good); background: var(--good-bg); }
.chip-warn { color: var(--warn); background: var(--warn-bg); }
.chip-bad  { color: var(--bad);  background: var(--bad-bg); }
.chip-info { color: var(--info); background: var(--info-bg); }
```

- [ ] **Step 2: Switch the import in main.tsx**

Modify: `frontend/src/main.tsx` line 4: change `import "./App.css";` to `import "./lib/tokens.css";`.

- [ ] **Step 3: Verify dev server still runs**

```bash
cd frontend && npm run dev
```

Open `http://localhost:5173`. Expected: page loads (will look broken — App.tsx still references old class names), background is **black** with amber/light text. No JS console errors. If you see white background, the import didn't take effect — restart the dev server.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/tokens.css frontend/src/main.tsx
git commit -m "ui: add terminal design tokens and switch root css import"
```

---

### Task 0.2: Add agent-ink helper

**Files:**
- Create: `frontend/src/lib/agentInk.ts`

- [ ] **Step 1: Write the helper**

```ts
const AGENT_VARS: Record<string, string> = {
  "chief of staff": "--ag-chief",
  analyst: "--ag-analyst",
  pricing: "--ag-pricing",
  "pricing & promo": "--ag-pricing",
  marketing: "--ag-marketing",
  merchandiser: "--ag-merchandiser",
  fulfillment: "--ag-fulfillment",
  replenishment: "--ag-replenishment",
  "store manager": "--ag-store",
  integration: "--ag-integration",
};

export function agentInkVar(name: string): string {
  return AGENT_VARS[name.trim().toLowerCase()] ?? "--ink";
}

export function agentInkStyle(name: string): React.CSSProperties {
  return { color: `var(${agentInkVar(name)})` };
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/agentInk.ts
git commit -m "ui: add agentInk helper mapping agent names to css color vars"
```

---

## Phase 1 — Hoist data fetching

### Task 1.1: Extract `useDashboardData` hook

**Files:**
- Create: `frontend/src/lib/data.ts`

- [ ] **Step 1: Write the hook**

```ts
import { useEffect, useState, useCallback } from "react";
import {
  ActionItem,
  Campaign,
  CategoryOpportunity,
  InboundPo,
  IntegrationSystem,
  InventorySku,
  KpiResponse,
  Store,
  SyncRun,
  getInventoryHealth,
  getKpis,
  listActionQueue,
  listCampaigns,
  listCategories,
  listIntegrationSystems,
  listSyncRuns,
  listStores,
} from "./api";

export type DashboardData = {
  kpis: KpiResponse | null;
  categories: CategoryOpportunity[];
  campaigns: Campaign[];
  stores: Store[];
  actions: ActionItem[];
  skus: InventorySku[];
  inbound: InboundPo[];
  systems: IntegrationSystem[];
  syncRuns: SyncRun[];
};

const EMPTY: DashboardData = {
  kpis: null,
  categories: [],
  campaigns: [],
  stores: [],
  actions: [],
  skus: [],
  inbound: [],
  systems: [],
  syncRuns: [],
};

export function useDashboardData(refreshKey: number) {
  const [data, setData] = useState<DashboardData>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>("");

  const refresh = useCallback(async () => {
    try {
      setError("");
      const [kpis, categories, campaigns, stores, actions, inventory, systems, syncRuns] =
        await Promise.all([
          getKpis(),
          listCategories(),
          listCampaigns(),
          listStores(),
          listActionQueue(),
          getInventoryHealth(),
          listIntegrationSystems(),
          listSyncRuns(),
        ]);
      setData({
        kpis,
        categories,
        campaigns,
        stores,
        actions,
        skus: inventory.skus,
        inbound: inventory.inbound_pos,
        systems,
        syncRuns,
      });
    } catch (e: any) {
      setError(e?.message || "Dashboard data unavailable");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    refresh();
  }, [refreshKey, refresh]);

  return { data, loading, error, refresh };
}
```

- [ ] **Step 2: Verify it type-checks**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors. If `tsc` reports missing exports, double-check `lib/api.ts` actually exports each named import above (it does, but the error message tells you which one is wrong).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/data.ts
git commit -m "ui: extract useDashboardData hook from Dashboard"
```

---

## Phase 2 — Outer layout grid

### Task 2.1: Rewrite App.tsx with placeholder rails

**Files:**
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/App.css` (new minimal sheet for the outer grid)

- [ ] **Step 1: Add the outer-grid stylesheet**

```css
/* frontend/src/App.css — outer layout only */
.app {
  display: grid;
  grid-template-rows: 28px 1fr 24px;
  height: 100vh;
  background: var(--bg);
}

.app-middle {
  display: grid;
  grid-template-columns: minmax(420px, 1.5fr) minmax(360px, 1fr) minmax(280px, 0.7fr);
  min-height: 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.app-middle > * {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--panel);
}

.app-middle > * + * {
  border-left: 1px solid var(--line);
}
```

- [ ] **Step 2: Rewrite App.tsx**

Replace the entire file with:

```tsx
import { useState } from "react";
import StatusStrip from "./components/StatusStrip";
import DataRail from "./components/DataRail";
import Chat from "./components/Chat";
import ApprovalRail from "./components/ApprovalRail";
import EventTape from "./components/EventTape";
import { DrawerProvider } from "./lib/drawerContext";
import "./App.css";

export default function App() {
  const [refreshKey, setRefreshKey] = useState(0);
  const bump = () => setRefreshKey((k) => k + 1);
  return (
    <DrawerProvider>
      <div className="app">
        <StatusStrip refreshKey={refreshKey} />
        <div className="app-middle">
          <DataRail refreshKey={refreshKey} />
          <Chat onEvent={bump} />
          <ApprovalRail refreshKey={refreshKey} />
        </div>
        <EventTape refreshKey={refreshKey} />
      </div>
    </DrawerProvider>
  );
}
```

This file references components and a `DrawerProvider` that don't exist yet. The dev server will fail to compile until the next phases land — that's expected. Don't try to verify in the browser yet.

- [ ] **Step 3: Add a stub DrawerProvider so the import resolves**

Create: `frontend/src/lib/drawerContext.tsx`

```tsx
import { createContext, useContext, useState, ReactNode } from "react";
import type { ActionItem, ArtifactMeta, ExternalAction, SpineEvent } from "./api";

export type DrawerContent =
  | { kind: "approval"; action: ActionItem; external?: ExternalAction }
  | { kind: "artifact"; artifactId: string }
  | { kind: "event-tape"; events: SpineEvent[] }
  | null;

type DrawerCtx = {
  drawer: DrawerContent;
  open: (c: DrawerContent) => void;
  close: () => void;
};

const Ctx = createContext<DrawerCtx | null>(null);

export function DrawerProvider({ children }: { children: ReactNode }) {
  const [drawer, setDrawer] = useState<DrawerContent>(null);
  return (
    <Ctx.Provider
      value={{
        drawer,
        open: setDrawer,
        close: () => setDrawer(null),
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useDrawer() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useDrawer must be used inside DrawerProvider");
  return v;
}
```

`ArtifactMeta` is unused for now but keeping the import marker reminds the next task to wire it.

- [ ] **Step 4: Add temporary stubs so the build compiles**

Create empty placeholder files so the imports in App.tsx resolve. Each file:

```tsx
// frontend/src/components/StatusStrip.tsx (placeholder)
export default function StatusStrip(_: { refreshKey: number }) {
  return <div style={{ background: "#0a0a0a", color: "#ff8800" }}>RETAIL-OS · placeholder</div>;
}
```

Repeat for `DataRail.tsx`, `ApprovalRail.tsx`, `EventTape.tsx` with similar placeholder content. Leave `Chat.tsx` as-is (it already exists; we restyle it later).

- [ ] **Step 5: Run the dev server**

```bash
cd frontend && npm run dev
```

Open `http://localhost:5173`. Expected: black page with three labeled placeholder rails, the existing chat in the middle, and a thin black line top + bottom. The browser console should be clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.css frontend/src/lib/drawerContext.tsx \
        frontend/src/components/StatusStrip.tsx \
        frontend/src/components/DataRail.tsx \
        frontend/src/components/ApprovalRail.tsx \
        frontend/src/components/EventTape.tsx
git commit -m "ui: add outer 3-row grid and component stubs for new layout"
```

---

## Phase 3 — Status strip

### Task 3.1: Implement StatusStrip

**Files:**
- Modify: `frontend/src/components/StatusStrip.tsx`
- Create: `frontend/src/components/StatusStrip.css`

- [ ] **Step 1: Write the component**

Replace the StatusStrip.tsx placeholder with:

```tsx
import { useEffect, useState } from "react";
import { ConfigInfo, getConfig, setProvider, Kpi, KpiResponse, listActionQueue } from "../lib/api";
import { useDashboardData } from "../lib/data";
import { useDrawer } from "../lib/drawerContext";
import "./StatusStrip.css";

const CHIP_LABELS: Record<string, string> = {
  "30d omnichannel revenue": "REV.30D",
  "Gross margin": "MGN",
  "Inventory at risk": "RISK",
  "Active campaigns": "CAMP",
  "Supplier risks": "SUPP",
  "Store exceptions": "STORE",
};

function formatValue(k: Kpi): string {
  if (k.format === "currency") {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(k.value);
  }
  if (k.format === "percent") return `${Math.round(k.value * 100)}%`;
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(k.value);
}

function deltaClass(delta: string): string {
  const t = delta.trim();
  if (!t) return "";
  if (t.startsWith("▲") || t.startsWith("+")) return "delta-good";
  if (t.startsWith("▼") || t.startsWith("-")) return "delta-bad";
  return "delta-flat";
}

function approvalCount(kpis: KpiResponse | null, actions: { status: string; external_actions?: { status: string }[] }[]): number {
  // We re-derive from the action queue rather than KPI to stay consistent with ApprovalRail.
  let n = 0;
  for (const a of actions) {
    if (a.status === "approval_required") n++;
    for (const ex of a.external_actions ?? []) {
      if (ex.status === "mock_only" || ex.status === "proposed") n++;
    }
  }
  return n;
}

export default function StatusStrip({ refreshKey }: { refreshKey: number }) {
  const { data, error } = useDashboardData(refreshKey);
  const { open } = useDrawer();
  const [cfg, setCfg] = useState<ConfigInfo | null>(null);
  const [now, setNow] = useState<string>(new Date().toUTCString().slice(17, 25));

  useEffect(() => {
    getConfig().then(setCfg).catch(() => {});
  }, []);

  useEffect(() => {
    const id = setInterval(() => setNow(new Date().toUTCString().slice(17, 25)), 1000);
    return () => clearInterval(id);
  }, []);

  const change = async (p: string) => {
    try {
      const c = await setProvider(p);
      setCfg(c);
    } catch {
      /* ignore */
    }
  };

  const approveN = approvalCount(data.kpis, data.actions);
  const kpis = data.kpis?.kpis ?? [];

  return (
    <div className="status-strip">
      <span className="brand">RETAIL-OS</span>
      {cfg && (
        <span className="provider">
          provider=
          <select value={cfg.provider} onChange={(e) => change(e.target.value)}>
            <option value="anthropic">anthropic</option>
            <option value="openai">openai</option>
            <option value="google">google</option>
          </select>
          <span className={`key ${cfg.has_key ? "good" : "bad"}`}>
            {cfg.has_key ? "✓" : "✗"}
          </span>
        </span>
      )}
      <span className="clock">{now} UTC</span>
      <span className="spacer" />
      {kpis.map((k) => (
        <span key={k.label} className="kpi">
          <span className="kpi-label">{CHIP_LABELS[k.label] ?? k.label.slice(0, 4).toUpperCase()}</span>
          <span className="kpi-value">{formatValue(k)}</span>
          {k.delta && <span className={`kpi-delta ${deltaClass(k.delta)}`}>{k.delta}</span>}
        </span>
      ))}
      {error && <span className="link-err">data link · err</span>}
      <span className="spacer" />
      <button
        className={`approve ${approveN > 0 ? "approve-on" : ""}`}
        onClick={() => {
          const first = data.actions.find((a) => a.status === "approval_required");
          if (first) open({ kind: "approval", action: first });
        }}
        disabled={approveN === 0}
        title={approveN === 0 ? "no pending approvals" : "open first pending approval"}
      >
        APPROVE {approveN}{approveN > 0 ? "!" : ""}
      </button>
    </div>
  );
}
```

- [ ] **Step 2: Add the stylesheet**

```css
/* frontend/src/components/StatusStrip.css */
.status-strip {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: 0 var(--sp-3);
  font-size: var(--fs-xs);
  background: #000;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
}
.status-strip .brand {
  color: var(--amber);
  font-weight: 600;
  letter-spacing: 0.04em;
}
.status-strip .provider {
  color: var(--ink-dim);
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.status-strip .provider select {
  padding: 0 2px;
  border: none;
  color: var(--ink);
  font-size: var(--fs-xs);
}
.status-strip .provider .key.good { color: var(--good); }
.status-strip .provider .key.bad  { color: var(--bad);  }
.status-strip .clock {
  color: var(--ink-dim);
  font-variant-numeric: tabular-nums;
}
.status-strip .spacer { flex: 1; }
.status-strip .kpi {
  display: inline-flex;
  gap: 4px;
  align-items: baseline;
}
.status-strip .kpi-label {
  color: var(--ink-dim);
}
.status-strip .kpi-value {
  color: var(--ink);
  font-weight: 600;
}
.status-strip .delta-good { color: var(--good); }
.status-strip .delta-bad  { color: var(--bad);  }
.status-strip .delta-flat { color: var(--ink-mute); }
.status-strip .link-err {
  color: var(--bad);
  background: var(--bad-bg);
  padding: 0 var(--sp-1);
}
.status-strip .approve {
  background: transparent;
  border: 1px solid var(--line-hot);
  color: var(--ink-dim);
  padding: 0 var(--sp-2);
  font-size: var(--fs-xs);
}
.status-strip .approve-on {
  color: var(--amber);
  border-color: var(--amber);
  animation: amber-pulse 1.6s ease-in-out infinite;
}
@keyframes amber-pulse {
  0%, 100% { box-shadow: inset 0 0 0 0 var(--amber); }
  50%      { box-shadow: inset 0 0 0 1px var(--amber); }
}
```

- [ ] **Step 3: Verify**

Reload `http://localhost:5173`. Expected: top strip shows `RETAIL-OS · provider=<select> · 14:42:08 UTC` then KPI chips drawn from `/api/kpis`. The clock ticks. Switching the provider dropdown should not throw. The `APPROVE n` button shows the count from the action queue and pulses amber when `n > 0`. Curl sanity check:

```bash
curl -s http://127.0.0.1:8000/api/kpis | python -c "import sys,json; print([k['label'] for k in json.load(sys.stdin)['kpis']])"
```

Expected output: `['30d omnichannel revenue', 'Gross margin', 'Inventory at risk', 'Active campaigns', 'Supplier risks', 'Store exceptions']`. If labels differ, update `CHIP_LABELS`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/StatusStrip.tsx frontend/src/components/StatusStrip.css
git commit -m "ui: implement StatusStrip with provider chip, KPI chips, approve count"
```

---

## Phase 4 — Data rail

### Task 4.1: Sortable hook

**Files:**
- Create: `frontend/src/components/dataTabs/sortable.ts`

- [ ] **Step 1: Write the hook**

```ts
import { useMemo, useState } from "react";

export type SortDir = "asc" | "desc";

export function useSort<T>(rows: T[], initialKey: keyof T, initialDir: SortDir = "desc") {
  const [key, setKey] = useState<keyof T>(initialKey);
  const [dir, setDir] = useState<SortDir>(initialDir);

  const sorted = useMemo(() => {
    const factor = dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      if (av === bv) return 0;
      if (av == null) return -1 * factor;
      if (bv == null) return 1 * factor;
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * factor;
      return String(av).localeCompare(String(bv)) * factor;
    });
  }, [rows, key, dir]);

  function header(name: keyof T, label: string) {
    const active = name === key;
    return {
      onClick: () => {
        if (name === key) setDir((d) => (d === "asc" ? "desc" : "asc"));
        else {
          setKey(name);
          setDir("desc");
        }
      },
      className: `th ${active ? `active ${dir}` : ""}`,
      children: (
        <>
          {label} {active ? (dir === "asc" ? "▲" : "▼") : ""}
        </>
      ),
    };
  }

  return { sorted, key, dir, header };
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/dataTabs/sortable.ts
git commit -m "ui: add useSort helper for column-header sorting"
```

---

### Task 4.2: DataRail shell with tabs and persistence

**Files:**
- Modify: `frontend/src/components/DataRail.tsx`
- Create: `frontend/src/components/DataRail.css`

- [ ] **Step 1: Write the rail shell**

```tsx
import { useEffect, useState } from "react";
import { useDashboardData } from "../lib/data";
import CategoriesTab from "./dataTabs/CategoriesTab";
import StoresTab from "./dataTabs/StoresTab";
import InventoryTab from "./dataTabs/InventoryTab";
import CampaignsTab from "./dataTabs/CampaignsTab";
import IntegrationsTab from "./dataTabs/IntegrationsTab";
import "./DataRail.css";

const TABS = [
  { id: "cat", label: "CATEGORIES" },
  { id: "str", label: "STORES" },
  { id: "inv", label: "INVENTORY" },
  { id: "cmp", label: "CAMPAIGNS" },
  { id: "int", label: "INTEGRATIONS" },
] as const;

type TabId = (typeof TABS)[number]["id"];

const STORAGE_KEY = "retail-os.datarail.tab";

export default function DataRail({ refreshKey }: { refreshKey: number }) {
  const { data, loading, error, refresh } = useDashboardData(refreshKey);
  const [tab, setTab] = useState<TabId>(() => {
    const stored = (typeof localStorage !== "undefined" && localStorage.getItem(STORAGE_KEY)) || "cat";
    return (TABS.find((t) => t.id === stored)?.id ?? "cat") as TabId;
  });

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, tab);
  }, [tab]);

  return (
    <section className="data-rail" tabIndex={0}>
      <header className="rail-header">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`tab ${t.id === tab ? "active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            [{t.label}]
          </button>
        ))}
      </header>
      <div className="rail-body">
        {loading && data.kpis === null ? (
          <div className="rail-empty">── loading ──</div>
        ) : error ? (
          <div className="rail-empty err">── connection lost · retry in 5s ──</div>
        ) : (
          <>
            {tab === "cat" && <CategoriesTab rows={data.categories} />}
            {tab === "str" && <StoresTab rows={data.stores} />}
            {tab === "inv" && <InventoryTab skus={data.skus} />}
            {tab === "cmp" && <CampaignsTab rows={data.campaigns} />}
            {tab === "int" && (
              <IntegrationsTab systems={data.systems} runs={data.syncRuns} onRefresh={refresh} />
            )}
          </>
        )}
      </div>
    </section>
  );
}
```

- [ ] **Step 2: Add the stylesheet**

```css
/* frontend/src/components/DataRail.css */
.data-rail {
  display: flex;
  flex-direction: column;
  height: 100%;
  outline: none;
}
.data-rail:focus-within { box-shadow: inset 0 0 0 1px var(--line-hot); }

.data-rail .rail-header {
  display: flex;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}
.data-rail .tab {
  border: none;
  border-right: 1px solid var(--line);
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--ink-dim);
  letter-spacing: 0.04em;
}
.data-rail .tab.active {
  color: var(--amber);
  background: var(--panel-2);
}

.data-rail .rail-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.data-rail .rail-empty {
  padding: var(--sp-4);
  color: var(--ink-mute);
  text-align: center;
}
.data-rail .rail-empty.err { color: var(--bad); }

.data-rail table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}
.data-rail th,
.data-rail td {
  padding: var(--sp-1) var(--sp-2);
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}
.data-rail th {
  color: var(--ink-dim);
  font-weight: 500;
  font-size: var(--fs-xs);
  letter-spacing: 0.04em;
  text-transform: uppercase;
  cursor: pointer;
  user-select: none;
  position: sticky;
  top: 0;
  background: var(--panel);
}
.data-rail th.active { color: var(--amber); }
.data-rail tbody tr:hover { background: var(--panel-2); }
.data-rail tbody tr.has-action { cursor: pointer; }

.data-rail td.num,
.data-rail th.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
```

- [ ] **Step 3: Commit (will fail to compile until tab files land — that's fine for the partial commit, just push the stub tabs together)**

Skip this commit; proceed to Task 4.3, then commit together.

---

### Task 4.3: Implement five tab tables

**Files:**
- Create: `frontend/src/components/dataTabs/CategoriesTab.tsx`
- Create: `frontend/src/components/dataTabs/StoresTab.tsx`
- Create: `frontend/src/components/dataTabs/InventoryTab.tsx`
- Create: `frontend/src/components/dataTabs/CampaignsTab.tsx`
- Create: `frontend/src/components/dataTabs/IntegrationsTab.tsx`

The tables share a small numeric formatter; each is self-contained for clarity rather than DRY-cleverness.

- [ ] **Step 1: CategoriesTab**

```tsx
import type { CategoryOpportunity } from "../../lib/api";
import { useSort } from "./sortable";

const num = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const cur = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
const pct = (n: number) => `${(n * 100).toFixed(0)}%`;

export default function CategoriesTab({ rows }: { rows: CategoryOpportunity[] }) {
  const { sorted, header } = useSort(rows, "opportunity_score" as keyof CategoryOpportunity, "desc");
  return (
    <table>
      <thead>
        <tr>
          <th {...header("display_name", "Category")} />
          <th {...header("sales_revenue", "Rev 30d")} className="num" />
          <th {...header("margin_rate", "Margin")} className="num" />
          <th {...header("on_hand", "Units")} className="num" />
          <th {...header("inventory_pressure", "Pressure")} className="num" />
          <th {...header("opportunity_score", "Push")} className="num" />
          <th {...header("lifecycle_stage", "Lifecycle")} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => (
          <tr key={r.category}>
            <td>{r.display_name}</td>
            <td className="num">{cur.format(r.sales_revenue)}</td>
            <td className="num">{pct(r.margin_rate)}</td>
            <td className="num">{num.format(r.on_hand)}</td>
            <td className="num">{r.inventory_pressure.toFixed(2)}</td>
            <td className="num">{r.opportunity_score.toFixed(2)}</td>
            <td>{r.lifecycle_stage}</td>
          </tr>
        ))}
        {sorted.length === 0 && (
          <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--ink-mute)" }}>── no rows ──</td></tr>
        )}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 2: StoresTab, InventoryTab, CampaignsTab, IntegrationsTab**

Follow the same shape as CategoriesTab. Columns per spec (Section "Components → DataRail"). For brevity, key implementation notes:

- **StoresTab** — columns: id, name, region, capacity, labor_pressure, local_demand_signal, weather_signal, capacity_used. Initial sort: `local_demand_signal` desc.
- **InventoryTab** — columns: sku, category, on_hand, reorder_point, price, base_price, risk indicator (`on_hand < reorder_point` → red chip "short"; `on_hand > reorder_point * 3` → amber chip "over"). Initial sort: on_hand desc. Source: `data.skus` (already filtered to inventory health rows).
- **CampaignsTab** — columns: campaign_id, category, channel, budget, projected_roi, actual_roi, status. Status uses `chip-good` if `status === "measured"`, `chip-warn` if `proposed`/`launched`, `chip-bad` if `blocked`. Initial sort: starts_at desc.
- **IntegrationsTab** — columns: system_id, domain, mode (chip: `connected` good, `mock` info), last_sync_ts, last_status, action: `[sync]` button. The button calls `syncIntegration(system_id)` from `lib/api.ts` and then `onRefresh()`.

For each tab, render `── no rows ──` placeholder when empty.

- [ ] **Step 3: Verify in browser**

Reload. Each tab should render its table. Click column headers — sort indicator (▲/▼) toggles, rows reorder. Switch tab; refresh page; the same tab should still be selected (localStorage). Sort headers should remain sticky on scroll.

If the integrations sync button errors, run the curl manually first:

```bash
curl -s -X POST http://127.0.0.1:8000/api/integrations/erpnext/sync | head -c 200
```

Expected: a JSON body with `sync_run` and `result` keys.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/DataRail.tsx frontend/src/components/DataRail.css \
        frontend/src/components/dataTabs/
git commit -m "ui: implement DataRail with 5 tabbed sortable tables"
```

---

## Phase 5 — Chat refactor

### Task 5.1: Restyle chat in terminal aesthetic

**Files:**
- Modify: `frontend/src/components/Chat.tsx`
- Create: `frontend/src/components/Chat.css`

- [ ] **Step 1: Replace Chat.tsx body**

Keep the existing SSE wiring; rewrite render + add input history + add `trace` toggle.

```tsx
import { useState, useRef, useEffect, useMemo } from "react";
import { chatStream, AgentEvent } from "../lib/api";
import { agentInkStyle } from "../lib/agentInk";
import "./Chat.css";

type ChatTurn = { role: "user" | "assistant"; text: string; events?: AgentEvent[] };

const SUGGESTIONS = [
  "We have excess summer inventory, uneven store demand, and a weekend heatwave. Build a marketing push for the right categories, decide markdowns, route fulfillment, rebalance stores, hold risky inbound POs, and show expected margin impact.",
  "Which category should Marketing push this week, and why?",
  "Did the category push work? Measure lift, ROI, margin impact, fulfillment cost, and remaining risks.",
];

export default function Chat({ onEvent }: { onEvent: () => void }) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [showTrace, setShowTrace] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  const [historyIdx, setHistoryIdx] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 99999, behavior: "smooth" });
  }, [turns]);

  // Allow Cmd/Ctrl+K to focus the chat input from anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const traceEvents = useMemo(
    () => turns.flatMap((t) => t.events ?? []).filter((e) => e.kind === "tool_call" || e.kind === "tool_result" || e.kind === "agent_start" || e.kind === "agent_end"),
    [turns],
  );

  const send = async () => {
    const msg = input.trim();
    if (!msg || streaming) return;
    setInput("");
    setHistory((h) => [...h.slice(-19), msg]);
    setHistoryIdx(null);
    setStreaming(true);
    setTurns((t) => [
      ...t,
      { role: "user", text: msg },
      { role: "assistant", text: "", events: [] },
    ]);

    await chatStream(
      msg,
      (ev) => {
        onEvent();
        setTurns((t) => {
          const copy = [...t];
          const last = copy[copy.length - 1];
          if (last.role !== "assistant") return copy;
          const events = [...(last.events || []), ev];
          let text = last.text;
          if (ev.kind === "agent_end" && ev.agent === "Chief of Staff") {
            text = ev.data?.text || text;
          } else if (ev.kind === "text" && ev.agent === "Chief of Staff" && !text) {
            text = ev.data?.text || "";
          }
          copy[copy.length - 1] = { ...last, text, events };
          return copy;
        });
      },
      () => setStreaming(false),
      (err) => {
        setTurns((t) => {
          const copy = [...t];
          const last = copy[copy.length - 1];
          if (last?.role === "assistant") copy[copy.length - 1] = { ...last, text: `error: ${err}` };
          return copy;
        });
        setStreaming(false);
      },
    );
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
      return;
    }
    if (e.key === "ArrowUp" && (input === "" || historyIdx !== null)) {
      e.preventDefault();
      if (history.length === 0) return;
      const next = historyIdx === null ? history.length - 1 : Math.max(0, historyIdx - 1);
      setHistoryIdx(next);
      setInput(history[next]);
    } else if (e.key === "ArrowDown" && historyIdx !== null) {
      e.preventDefault();
      const next = historyIdx + 1;
      if (next >= history.length) {
        setHistoryIdx(null);
        setInput("");
      } else {
        setHistoryIdx(next);
        setInput(history[next]);
      }
    }
  };

  return (
    <section className="chat">
      <header className="chat-header">
        <span className="label">Chief of Staff</span>
        <span className="spacer" />
        <button className={`trace-toggle ${showTrace ? "on" : ""}`} onClick={() => setShowTrace((v) => !v)}>
          [ trace {showTrace ? "▾" : "▸"} ]
        </button>
      </header>
      <div className={`chat-body ${showTrace ? "split" : ""}`}>
        <div className="chat-thread" ref={scrollRef}>
          {turns.length === 0 && (
            <div className="chat-empty">
              <div className="label">try</div>
              <ul>
                {SUGGESTIONS.map((s) => (
                  <li key={s} onClick={() => setInput(s)}>{s.length > 80 ? s.slice(0, 80) + "…" : s}</li>
                ))}
              </ul>
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} className={`turn turn-${t.role}`}>
              {t.role === "user" ? (
                <div className="user-line">&gt; {t.text}</div>
              ) : (
                <div className="cos-line">
                  <span className="ts">[{new Date().toUTCString().slice(17, 22)}]</span>
                  <span className="who" style={agentInkStyle("Chief of Staff")}>CHIEF</span>
                  <span className="text">{t.text || (streaming && i === turns.length - 1 ? "working…" : "")}</span>
                </div>
              )}
            </div>
          ))}
        </div>
        {showTrace && (
          <aside className="chat-trace">
            <div className="label">agent trace</div>
            {traceEvents.length === 0 && <div className="empty">── no trace yet ──</div>}
            {traceEvents.map((e, i) => (
              <div key={i} className="trace-row">
                <span className="who" style={agentInkStyle(e.agent)}>{e.agent}</span>
                <span className="kind">{e.kind}</span>
                {e.kind === "tool_call" && <span className="detail">→ {e.data?.tool}</span>}
                {e.kind === "tool_result" && e.data?.result?.artifact_id && (
                  <span className="detail">→ artifact {e.data.result.artifact_id}</span>
                )}
              </div>
            ))}
          </aside>
        )}
      </div>
      <footer className="chat-input">
        <span className="prompt">&gt;</span>
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => { setInput(e.target.value); setHistoryIdx(null); }}
          onKeyDown={onKey}
          placeholder="ask the chief of staff…"
          disabled={streaming}
          rows={2}
        />
      </footer>
    </section>
  );
}
```

- [ ] **Step 2: Add Chat.css**

```css
.chat {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.chat-header {
  display: flex;
  align-items: center;
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--line);
  font-size: var(--fs-xs);
}
.chat-header .spacer { flex: 1; }
.chat-header .trace-toggle {
  border: none;
  color: var(--ink-dim);
}
.chat-header .trace-toggle.on { color: var(--amber); }

.chat-body {
  flex: 1;
  display: grid;
  grid-template-rows: 1fr;
  min-height: 0;
}
.chat-body.split {
  grid-template-rows: 1fr 1fr;
}

.chat-thread {
  overflow: auto;
  padding: var(--sp-3);
  font-size: var(--fs-sm);
  line-height: var(--lh-loose);
}
.chat-empty .label { margin-bottom: var(--sp-2); }
.chat-empty ul { list-style: none; padding: 0; margin: 0; }
.chat-empty li {
  padding: var(--sp-1) 0;
  color: var(--ink-dim);
  cursor: pointer;
}
.chat-empty li:hover { color: var(--amber); }

.turn { margin-bottom: var(--sp-3); }
.user-line { color: var(--amber); white-space: pre-wrap; }
.cos-line { display: flex; gap: var(--sp-2); align-items: baseline; flex-wrap: wrap; }
.cos-line .ts { color: var(--ink-mute); }
.cos-line .who { font-weight: 600; }
.cos-line .text { color: var(--ink); white-space: pre-wrap; flex: 1; min-width: 0; }

.chat-trace {
  border-top: 1px solid var(--line);
  overflow: auto;
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-xs);
}
.chat-trace .label { margin-bottom: var(--sp-2); }
.chat-trace .empty { color: var(--ink-mute); }
.chat-trace .trace-row {
  display: flex;
  gap: var(--sp-2);
  padding: 1px 0;
}
.chat-trace .who { font-weight: 600; min-width: 6em; }
.chat-trace .kind { color: var(--ink-dim); min-width: 8em; }
.chat-trace .detail { color: var(--ink); }

.chat-input {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border-top: 1px solid var(--line);
}
.chat-input .prompt { color: var(--amber); padding-top: 6px; }
.chat-input textarea {
  flex: 1;
  border: none;
  resize: none;
  outline: none;
  color: var(--ink);
  font: inherit;
}
.chat-input textarea::placeholder { color: var(--ink-mute); }
```

- [ ] **Step 3: Verify**

Reload. Send a chat message. Expected:
1. User input shows as `> message` in amber.
2. Chief of Staff reply renders inline.
3. Toggle `[ trace ▸ ]` in header → splits the rail; trace populates with tool_call / tool_result rows once the agent runs.
4. Press `↑` in an empty input → fills with last sent message; `↓` advances back to empty.
5. From any focus, press `Cmd+K` (or `Ctrl+K` on Linux/Windows) → focus jumps to chat input.

If clicks on suggestions stop working, you probably broke the empty-state rendering condition.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Chat.tsx frontend/src/components/Chat.css
git commit -m "ui: restyle Chat as terminal thread with trace pane and input history"
```

---

## Phase 6 — Approval rail

### Task 6.1: Implement ApprovalRail

**Files:**
- Modify: `frontend/src/components/ApprovalRail.tsx`
- Create: `frontend/src/components/ApprovalRail.css`

- [ ] **Step 1: Implement the rail**

```tsx
import { useMemo, useState } from "react";
import type { ActionItem, ArtifactMeta, ExternalAction } from "../lib/api";
import { listArtifacts } from "../lib/api";
import { useEffect } from "react";
import { useDashboardData } from "../lib/data";
import { useDrawer } from "../lib/drawerContext";
import { agentInkStyle } from "../lib/agentInk";
import "./ApprovalRail.css";

type Pending = {
  key: string;
  action: ActionItem;
  external?: ExternalAction;
  systems: string[];
};

function buildPending(actions: ActionItem[]): Pending[] {
  const pending: Pending[] = [];
  for (const a of actions) {
    const externals = (a.external_actions ?? []).filter(
      (x) => x.status === "mock_only" || x.status === "proposed",
    );
    const top = a.status === "approval_required";
    if (top && externals.length === 0) {
      pending.push({ key: `q-${a.id}`, action: a, systems: [] });
    } else if (externals.length > 0) {
      pending.push({
        key: `q-${a.id}`,
        action: a,
        external: externals[0],
        systems: externals.map((x) => x.system_id),
      });
    }
  }
  return pending;
}

export default function ApprovalRail({ refreshKey }: { refreshKey: number }) {
  const { data, loading, error } = useDashboardData(refreshKey);
  const { open } = useDrawer();
  const [rejected, setRejected] = useState<Set<string>>(new Set());
  const [artifacts, setArtifacts] = useState<ArtifactMeta[]>([]);

  useEffect(() => {
    let active = true;
    const refresh = () => listArtifacts().then((a) => active && setArtifacts(a)).catch(() => {});
    refresh();
    const id = setInterval(refresh, 5000);
    return () => { active = false; clearInterval(id); };
  }, [refreshKey]);

  const pending = useMemo(
    () => buildPending(data.actions).filter((p) => !rejected.has(p.key)),
    [data.actions, rejected],
  );

  return (
    <section className="approval-rail">
      <div className="block pending">
        <header><span className="label">Pending</span><span className="count">{pending.length}</span></header>
        <div className="rows">
          {loading && <div className="empty">── loading ──</div>}
          {error && <div className="empty err">── connection lost ──</div>}
          {!loading && !error && pending.length === 0 && (
            <div className="empty">── no pending approvals ──</div>
          )}
          {pending.map((p) => (
            <button key={p.key} className="row" onClick={() => open({ kind: "approval", action: p.action, external: p.external })}>
              <div className="row-head">
                <span className="title">▶ {p.action.title}</span>
                <span className="who" style={agentInkStyle(p.action.owner)}>{p.action.owner}</span>
              </div>
              <div className="row-meta">
                <span className="ctx">{contextLine(p.action)}</span>
                {p.systems.length > 0 && <span className="systems">{p.systems.join("+")}</span>}
                <span className="ts">←{new Date(p.action.ts).toUTCString().slice(17, 22)}</span>
              </div>
            </button>
          ))}
        </div>
      </div>
      <div className="block artifacts">
        <header><span className="label">Artifacts</span></header>
        <div className="rows">
          {artifacts.length === 0 && <div className="empty">── no artifacts ──</div>}
          {artifacts.slice(0, 6).map((a) => (
            <button key={a.id} className="row art" onClick={() => open({ kind: "artifact", artifactId: a.id })}>
              <div className="row-head">
                <span className="title">{a.title}</span>
              </div>
              <div className="row-meta">
                <span style={agentInkStyle(a.agent)}>{a.agent}</span>
                <span className="ctx">{a.kind}</span>
                <span className="ts">{a.id.slice(0, 6)}</span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function contextLine(a: ActionItem): string {
  const p = a.payload || {};
  if (p.summary && typeof p.summary === "string") return p.summary;
  if (p.reason) return String(p.reason);
  if (p.percent != null) return `${p.percent}%`;
  if (p.qty_ordered != null) return `qty=${p.qty_ordered}`;
  return a.action_type;
}
```

- [ ] **Step 2: Add stylesheet**

```css
.approval-rail {
  display: grid;
  grid-template-rows: 1fr 1fr;
  height: 100%;
  font-size: var(--fs-sm);
}
.approval-rail .block {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.approval-rail .block + .block { border-top: 1px solid var(--line); }
.approval-rail header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--line);
  font-size: var(--fs-xs);
}
.approval-rail .count { color: var(--amber); }
.approval-rail .rows { flex: 1; overflow: auto; }
.approval-rail .row {
  display: block;
  width: 100%;
  text-align: left;
  border: none;
  border-bottom: 1px solid var(--line);
  padding: var(--sp-2) var(--sp-3);
  background: transparent;
  font: inherit;
  color: var(--ink);
  cursor: pointer;
}
.approval-rail .row:hover { background: var(--panel-2); }
.approval-rail .pending .row .title { color: var(--warn); }
.approval-rail .row-head {
  display: flex;
  justify-content: space-between;
  gap: var(--sp-2);
}
.approval-rail .row-meta {
  display: flex;
  gap: var(--sp-2);
  color: var(--ink-dim);
  font-size: var(--fs-xs);
  margin-top: 2px;
}
.approval-rail .ts { margin-left: auto; }
.approval-rail .empty { padding: var(--sp-3); color: var(--ink-mute); text-align: center; }
.approval-rail .empty.err { color: var(--bad); }
.approval-rail .systems {
  color: var(--info);
  background: var(--info-bg);
  padding: 0 4px;
}
```

- [ ] **Step 3: Verify**

Reload. Right rail shows two blocks: `Pending` and `Artifacts`. Run the omnichannel demo (`./README.md` "Build a marketing push…"); within seconds, pending rows appear with amber `▶ title`. Clicking a row currently throws because `ApprovalDrawer` is not wired; the next phase fixes that.

If the rows count doesn't match `APPROVE n!` in the status strip, both panels read from the same union logic — re-check `buildPending` and `approvalCount` are equivalent.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ApprovalRail.tsx frontend/src/components/ApprovalRail.css
git commit -m "ui: implement ApprovalRail (pending + artifacts) with rejected-local filter"
```

---

## Phase 7 — Approval drawer

### Task 7.1: Build the drawer scaffold

**Files:**
- Create: `frontend/src/components/ApprovalDrawer.tsx`
- Create: `frontend/src/components/ApprovalDrawer.css`
- Modify: `frontend/src/App.tsx` (mount the drawer inside the provider)

- [ ] **Step 1: Mount the drawer**

In `frontend/src/App.tsx`, just before the closing `</DrawerProvider>`, add `<ApprovalDrawer />`. Add the import at top: `import ApprovalDrawer from "./components/ApprovalDrawer";`.

- [ ] **Step 2: Implement ApprovalDrawer.tsx**

```tsx
import { useEffect, useState } from "react";
import { marked } from "marked";
import {
  applyOutboundAction,
  ArtifactMeta,
  getArtifact,
  listEvents,
  SpineEvent,
} from "../lib/api";
import { useDrawer } from "../lib/drawerContext";
import { agentInkStyle } from "../lib/agentInk";
import "./ApprovalDrawer.css";

export default function ApprovalDrawer() {
  const { drawer, close } = useDrawer();

  // Esc to close.
  useEffect(() => {
    if (!drawer) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer, close]);

  if (!drawer) return null;

  return (
    <>
      <div className="drawer-backdrop" onClick={close} />
      <aside className="drawer">
        <header className="drawer-header">
          <button className="close" onClick={close}>✗ close</button>
        </header>
        <div className="drawer-body">
          {drawer.kind === "approval" && <ApprovalContent />}
          {drawer.kind === "artifact" && <ArtifactContent />}
          {drawer.kind === "event-tape" && <EventTapeContent />}
        </div>
      </aside>
    </>
  );
}

function ApprovalContent() {
  const { drawer, close } = useDrawer();
  const [phase, setPhase] = useState<"idle" | "applying" | "applied" | "error">("idle");
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string>("");
  const [artifact, setArtifact] = useState<ArtifactMeta & { body: string } | null>(null);

  if (drawer?.kind !== "approval") return null;
  const a = drawer.action;
  const ex = drawer.external;

  useEffect(() => {
    if (a.artifact_id) getArtifact(a.artifact_id).then(setArtifact).catch(() => {});
  }, [a.artifact_id]);

  const apply = async () => {
    if (!ex) return;
    setPhase("applying");
    try {
      const r = await applyOutboundAction(ex.system_id, ex.id);
      setResult(r);
      setPhase("applied");
      setTimeout(close, 2000);
    } catch (e: any) {
      setError(e?.message ?? "apply failed");
      setPhase("error");
    }
  };

  const reject = () => {
    // Local-only filter handled inside ApprovalRail by reading drawer close + rejected set.
    // For v1 we just close — see plan: ApprovalRail itself owns the rejected set.
    close();
  };

  return (
    <>
      <div className="drawer-title-block">
        <span className="who" style={agentInkStyle(a.owner)}>{a.owner}</span>
        <h2 className="title">{a.title}</h2>
        <span className="ts">{new Date(a.ts).toUTCString().slice(17, 25)} UTC</span>
      </div>

      <dl className="kv">
        <dt>type</dt><dd>{a.action_type}</dd>
        <dt>queue id</dt><dd>{a.id}</dd>
        <dt>status</dt><dd><span className="chip chip-warn">{a.status}</span></dd>
        {ex && (<><dt>external</dt><dd>{ex.system_id} · {ex.external_domain} · <span className="chip chip-info">{ex.status}</span></dd></>)}
        {Object.entries(a.payload).map(([k, v]) => (
          <PayloadRow key={k} k={k} v={v} />
        ))}
      </dl>

      {artifact && (
        <section className="artifact-render">
          <div className="label">artifact · {artifact.title}</div>
          <div className="md" dangerouslySetInnerHTML={{ __html: marked.parse(artifact.body) as string }} />
        </section>
      )}

      <footer className="drawer-actions">
        {phase === "idle" && (
          <>
            <button className="primary" onClick={apply} disabled={!ex}>apply → external</button>
            <button onClick={reject}>reject</button>
          </>
        )}
        {phase === "applying" && <span className="dim">applying…</span>}
        {phase === "applied" && (
          <span className="chip chip-good">{result?.status ?? "applied"} — {result?.result?.message ?? ""}</span>
        )}
        {phase === "error" && <span className="chip chip-bad">{error}</span>}
      </footer>
    </>
  );
}

function PayloadRow({ k, v }: { k: string; v: any }) {
  if (v === null || v === undefined) return null;
  return (
    <>
      <dt>{k}</dt>
      <dd>{typeof v === "string" || typeof v === "number" ? String(v) : <pre>{JSON.stringify(v, null, 2)}</pre>}</dd>
    </>
  );
}

function ArtifactContent() {
  const { drawer } = useDrawer();
  const [art, setArt] = useState<ArtifactMeta & { body: string } | null>(null);

  useEffect(() => {
    if (drawer?.kind !== "artifact") return;
    getArtifact(drawer.artifactId).then(setArt).catch(() => {});
  }, [drawer]);

  if (drawer?.kind !== "artifact") return null;
  if (!art) return <div className="dim">loading…</div>;
  return (
    <>
      <div className="drawer-title-block">
        <span className="who" style={agentInkStyle(art.agent)}>{art.agent}</span>
        <h2 className="title">{art.title}</h2>
        <span className="ts">{art.kind} · {art.id}</span>
      </div>
      <section className="artifact-render">
        <div className="md" dangerouslySetInnerHTML={{ __html: marked.parse(art.body) as string }} />
      </section>
    </>
  );
}

function EventTapeContent() {
  const { drawer } = useDrawer();
  const [events, setEvents] = useState<SpineEvent[]>([]);
  const [agentFilter, setAgentFilter] = useState<string>("all");
  const [kindFilter, setKindFilter] = useState<string>("all");

  useEffect(() => {
    if (drawer?.kind !== "event-tape") return;
    setEvents(drawer.events);
    listEvents().then((rows) => setEvents(rows.slice(0, 200))).catch(() => {});
  }, [drawer]);

  if (drawer?.kind !== "event-tape") return null;

  const agents = Array.from(new Set(events.map((e) => e.agent)));
  const kinds = Array.from(new Set(events.map((e) => e.kind)));
  const filtered = events.filter(
    (e) => (agentFilter === "all" || e.agent === agentFilter) && (kindFilter === "all" || e.kind === kindFilter),
  );

  return (
    <>
      <div className="drawer-title-block">
        <h2 className="title">Event Log</h2>
      </div>
      <div className="filters">
        <label>agent <select value={agentFilter} onChange={(e) => setAgentFilter(e.target.value)}>
          <option value="all">all</option>
          {agents.map((a) => <option key={a} value={a}>{a}</option>)}
        </select></label>
        <label>kind <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
          <option value="all">all</option>
          {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
        </select></label>
      </div>
      <div className="event-list">
        {filtered.map((e) => (
          <div key={e.id} className="event-row">
            <span className="ts">{new Date(e.ts).toUTCString().slice(17, 25)}</span>
            <span className="who" style={agentInkStyle(e.agent)}>{e.agent}</span>
            <span className="kind">{e.kind}</span>
            <span className="payload">{JSON.stringify(e.payload).slice(0, 120)}</span>
          </div>
        ))}
      </div>
    </>
  );
}
```

This file references `applyOutboundAction` from `lib/api.ts`. Confirm that helper exists; if it doesn't, add a one-liner:

```ts
// in lib/api.ts
export async function applyOutboundAction(systemId: string, actionId: number) {
  const r = await fetch(`/api/integrations/${systemId}/actions/${actionId}/apply`, { method: "POST" });
  if (!r.ok) throw new Error(`apply failed: ${r.status}`);
  return r.json();
}
```

(Search `lib/api.ts` first; if there's already an equivalent, use it instead of adding a duplicate.)

- [ ] **Step 3: Add stylesheet**

```css
.drawer-backdrop {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.55);
  z-index: 50;
}
.drawer {
  position: fixed;
  top: 28px; bottom: 24px; right: 0;
  width: min(720px, 60vw);
  background: var(--panel);
  border-left: 1px solid var(--line-hot);
  z-index: 60;
  display: flex;
  flex-direction: column;
  animation: drawer-in 160ms ease-out;
}
@keyframes drawer-in {
  from { transform: translateX(100%); }
  to   { transform: translateX(0); }
}
.drawer-header {
  display: flex;
  justify-content: flex-end;
  padding: var(--sp-2);
  border-bottom: 1px solid var(--line);
}
.drawer-header .close { border: none; color: var(--ink-dim); }
.drawer-body {
  flex: 1;
  overflow: auto;
  padding: var(--sp-4);
}
.drawer .drawer-title-block {
  display: flex;
  align-items: baseline;
  gap: var(--sp-3);
  margin-bottom: var(--sp-4);
  padding-bottom: var(--sp-2);
  border-bottom: 1px solid var(--line);
}
.drawer .drawer-title-block .who { font-weight: 600; }
.drawer .drawer-title-block .title { font-size: var(--fs-xl); margin: 0; flex: 1; }
.drawer .drawer-title-block .ts { color: var(--ink-mute); }

.drawer .kv {
  display: grid;
  grid-template-columns: 12em 1fr;
  gap: var(--sp-1) var(--sp-3);
  margin: 0 0 var(--sp-4);
}
.drawer .kv dt { color: var(--ink-dim); }
.drawer .kv dd { margin: 0; word-break: break-word; }
.drawer .kv pre {
  margin: 0;
  background: var(--bg);
  padding: var(--sp-2);
  font-size: var(--fs-xs);
  overflow: auto;
}

.drawer .artifact-render {
  border-top: 1px solid var(--line);
  padding-top: var(--sp-3);
  margin-top: var(--sp-3);
}
.drawer .artifact-render .label { margin-bottom: var(--sp-2); }
.drawer .md { line-height: var(--lh-loose); font-family: var(--font-mono); }
.drawer .md h1, .drawer .md h2, .drawer .md h3 { color: var(--amber); }
.drawer .md table { width: 100%; border-collapse: collapse; }
.drawer .md th, .drawer .md td { border: 1px solid var(--line); padding: 2px 6px; }

.drawer-actions {
  display: flex;
  gap: var(--sp-2);
  padding-top: var(--sp-3);
  border-top: 1px solid var(--line);
  margin-top: var(--sp-3);
}
.drawer-actions .primary { color: var(--amber); border-color: var(--amber); }
.drawer-actions .dim { color: var(--ink-dim); }

.drawer .filters {
  display: flex; gap: var(--sp-3);
  margin-bottom: var(--sp-3);
}
.drawer .filters select { padding: 0 var(--sp-1); }
.drawer .event-list { font-size: var(--fs-xs); }
.drawer .event-row {
  display: grid;
  grid-template-columns: 6em 10em 8em 1fr;
  gap: var(--sp-2);
  padding: 1px 0;
}
.drawer .event-row .kind { color: var(--ink-dim); }
.drawer .event-row .payload { color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
```

- [ ] **Step 4: Verify approvals end-to-end**

Reload. Type the demo prompt in chat:

> *We have excess summer inventory, uneven store demand, and a weekend heatwave. Build a marketing push for the right categories, decide markdowns, route fulfillment, rebalance stores, hold risky inbound POs, and show expected margin impact.*

After a few seconds:
1. Status strip `APPROVE` count goes nonzero and pulses.
2. Approval rail shows pending rows.
3. Click a row → drawer slides in from the right.
4. Drawer header shows agent (inked), title, timestamp.
5. Body shows definition list + (if linked) markdown artifact.
6. Click `apply → external` → `applying…` then green chip `applied_mock — Approved action applied in mock mode only…`. Drawer auto-closes after 2s.
7. Press `Esc` while drawer is open → closes immediately.
8. Click backdrop → closes.

If apply returns 404, you're hitting the wrong system_id; verify the rail row carries the external action's `system_id` correctly.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ApprovalDrawer.tsx frontend/src/components/ApprovalDrawer.css \
        frontend/src/App.tsx frontend/src/lib/api.ts
git commit -m "ui: implement ApprovalDrawer with apply/reject and artifact render"
```

---

## Phase 8 — Event tape

### Task 8.1: Implement EventTape

**Files:**
- Modify: `frontend/src/components/EventTape.tsx`
- Create: `frontend/src/components/EventTape.css`

- [ ] **Step 1: Implement the tape**

```tsx
import { useEffect, useRef, useState } from "react";
import { listEvents, SpineEvent } from "../lib/api";
import { useDrawer } from "../lib/drawerContext";
import { agentInkStyle } from "../lib/agentInk";
import "./EventTape.css";

const MAX = 30;

export default function EventTape({ refreshKey }: { refreshKey: number }) {
  const [events, setEvents] = useState<SpineEvent[]>([]);
  const lastIdRef = useRef<number>(0);
  const { open } = useDrawer();

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const rows = await listEvents(lastIdRef.current);
        if (cancelled) return;
        if (rows.length > 0) {
          // listEvents returns DESC by id; flip and prepend.
          const fresh = [...rows].reverse();
          lastIdRef.current = Math.max(lastIdRef.current, ...rows.map((r) => r.id));
          setEvents((prev) => [...fresh, ...prev].slice(0, MAX));
        }
      } catch { /* ignore */ }
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => { cancelled = true; clearInterval(id); };
  }, [refreshKey]);

  // Backtick to open the tape drawer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "`" && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLTextAreaElement)) {
        e.preventDefault();
        open({ kind: "event-tape", events });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [events, open]);

  return (
    <div className="event-tape" onClick={() => open({ kind: "event-tape", events })} title="click or press ` for full event log">
      {events.length === 0 && <span className="empty">── no events yet ──</span>}
      {events.map((e, i) => (
        <span key={e.id} className="evt">
          {i > 0 && <span className="sep">◆</span>}
          <span className="ts">{new Date(e.ts).toUTCString().slice(17, 22)}</span>
          <span className="who" style={agentInkStyle(e.agent)}>{e.agent}</span>
          <span className="kind">{e.kind}</span>
          <span className="payload">{summarize(e)}</span>
        </span>
      ))}
    </div>
  );
}

function summarize(e: SpineEvent): string {
  const p = e.payload || {};
  if (p.action) return `${p.action}`;
  if (p.summary && typeof p.summary === "string") return p.summary.slice(0, 60);
  if (p.artifact_title) return String(p.artifact_title);
  return e.sku ?? "";
}
```

- [ ] **Step 2: Add stylesheet**

```css
.event-tape {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: 0 var(--sp-3);
  background: #000;
  border-top: 1px solid var(--line);
  font-size: var(--fs-xs);
  white-space: nowrap;
  overflow: hidden;
  cursor: pointer;
  height: 24px;
}
.event-tape:hover { background: var(--panel); }
.event-tape .empty { color: var(--ink-mute); }
.event-tape .evt {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  flex-shrink: 0;
}
.event-tape .sep { color: var(--ink-mute); margin: 0 4px; }
.event-tape .ts { color: var(--ink-mute); }
.event-tape .who { font-weight: 600; }
.event-tape .kind { color: var(--ink-dim); }
.event-tape .payload { color: var(--ink); }
```

- [ ] **Step 3: Verify**

Reload. Run a chat prompt that produces multiple agent events. Expected:
1. Tape at the bottom appends new events as they happen, latest on the left.
2. Clicking the tape opens the drawer in event-tape mode with the full last 200 events.
3. Pressing `` ` `` (backtick) when not focused inside the chat input opens the same drawer.
4. Drawer filters by agent and kind.

If the tape grows past one line, your CSS lost `white-space: nowrap` somewhere.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/EventTape.tsx frontend/src/components/EventTape.css
git commit -m "ui: implement EventTape (bottom strip) with backtick drawer"
```

---

## Phase 9 — Polish and keyboard

### Task 9.1: Data-rail tab hotkeys

**Files:**
- Modify: `frontend/src/components/DataRail.tsx`

- [ ] **Step 1: Add focus-scoped key handler**

Inside `DataRail` add a ref to the section, and a `useEffect` listening to `keydown` on the section. Keys `1`–`5` map to the tabs. Skip the handler when the focused element is an input/textarea.

```tsx
import { useRef } from "react";
// inside component, after `tab` state:
const sectionRef = useRef<HTMLElement>(null);
useEffect(() => {
  const el = sectionRef.current;
  if (!el) return;
  const onKey = (e: KeyboardEvent) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    const idx = ["1","2","3","4","5"].indexOf(e.key);
    if (idx >= 0) {
      e.preventDefault();
      setTab(TABS[idx].id);
    }
  };
  el.addEventListener("keydown", onKey);
  return () => el.removeEventListener("keydown", onKey);
}, []);
// then on the <section> add ref={sectionRef}
```

- [ ] **Step 2: Verify**

Click the data rail (so it has focus), press `1`–`5` → jumps tabs.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/DataRail.tsx
git commit -m "ui: number-key shortcuts (1-5) jump data-rail tabs"
```

---

### Task 9.2: Tape lag indicator

**Files:**
- Modify: `frontend/src/components/EventTape.tsx`

- [ ] **Step 1: Track last poll latency**

Add a small `lag` state set from `Date.now() - lastTickStart`. If `lag > 10000`, prepend a `<span className="lag">… catching up …</span>` chip in the render.

- [ ] **Step 2: Verify (synthetic)**

Stop the backend (`Ctrl+C`), wait ~12s, restart it. The `… catching up …` chip should appear briefly, then events resume.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/EventTape.tsx
git commit -m "ui: show 'catching up' chip when event tape poll lags >10s"
```

---

## Phase 10 — Cleanup

### Task 10.1: Remove legacy components and CSS

**Files:**
- Delete: `frontend/src/components/AgentBadge.tsx`
- Delete: `frontend/src/components/EventLog.tsx`
- Delete: `frontend/src/components/Artifacts.tsx`
- Delete: `frontend/src/components/Dashboard.tsx`
- Delete: original `frontend/src/App.css` content (the file currently holds only the new outer-grid CSS — confirm nothing else references the old class names)

- [ ] **Step 1: Confirm no stragglers**

```bash
cd frontend && grep -rE "from ['\"](\.\./)?(components/(AgentBadge|EventLog|Artifacts|Dashboard))['\"]" src
```

Expected: no output.

- [ ] **Step 2: Confirm no class-name leakage**

```bash
grep -rE 'class(Name)?="(panel|cockpit|command-rail|audit-rail|kpi-grid|kpi-tile|dashboard|control-band|category-panel)' src
```

Expected: no output. If there are leftovers, fix them now (likely in `Chat.tsx` or new components reusing legacy class names).

- [ ] **Step 3: Delete the files**

```bash
git rm frontend/src/components/AgentBadge.tsx \
       frontend/src/components/EventLog.tsx \
       frontend/src/components/Artifacts.tsx \
       frontend/src/components/Dashboard.tsx
```

- [ ] **Step 4: Verify**

Reload. The cockpit should be visually identical to the previous iteration. Run both demo paths from `README.md`:

1. Markdown demo (`We're carrying too much summer apparel…`).
2. Omnichannel demo (`We have excess summer inventory…`).

For each: chat streams events; tape appends events; pending approvals appear in the rail; drawer opens, applies in mock mode.

- [ ] **Step 5: Run backend tests once**

```bash
cd backend && python -m unittest discover -s tests -v
```

Expected: all tests pass. If any fail, the redesign has somehow touched the API surface — investigate before committing the deletions.

- [ ] **Step 6: Commit**

```bash
git commit -m "ui: drop legacy Dashboard/EventLog/Artifacts/AgentBadge components"
```

---

### Task 10.2: Final UAT checklist run

**Files:** none

- [ ] **Run through the spec's UAT checklist** (Section "Manual UAT checklist" in the spec doc):

  - [ ] Status strip live updates KPI chips at 5s cadence.
  - [ ] Approve count chip pulses when `n > 0`, dims at `n == 0`.
  - [ ] Tab switching persists across page reload (`localStorage`).
  - [ ] Each tab table sorts on column-header click.
  - [ ] Click row with outstanding action → drawer opens pre-filled.
  - [ ] Drawer apply (mock-mode) → `applied_mock` confirmation; auto-closes after 2s.
  - [ ] Drawer apply (with creds set) → `draft_created` confirmation. *(skip if no creds — note in PR)*
  - [ ] Drawer reject → row removed locally; reappears on next 5s refresh.
  - [ ] Tape drawer opens via `\`` and via tape click; filters work.
  - [ ] Chat SSE during long delegation streams specialist events live.
  - [ ] Chat input history cycles with `↑ / ↓`.
  - [ ] `Cmd+K` focuses chat input from anywhere.
  - [ ] Provider switch updates the provider for the next chat call.
  - [ ] README demo paths complete without console errors.

- [ ] **Commit a UAT note if any item was deferred.**

```bash
# only if you have a deferral to record
git commit --allow-empty -m "ui: UAT pass — see PR description for deferrals"
```

---

## Done

The plan is complete when:
1. All 10 phases land as separate commits.
2. The cockpit renders status strip + 3 rails + tape on default seed data.
3. Both `README.md` demo paths run end-to-end with visible agent streaming and at least one drawer-apply.
4. `backend/tests/test_omnichannel.py` and `backend/tests/test_integrations.py` still pass.

## Notes for executor

- If you find a place where the spec is silent and you need to choose, prefer "less code, fewer features." This is a redesign, not a feature add.
- If a task's verification step fails for a reason that isn't covered by the troubleshooting hints, stop and report — don't paper over with broader changes.
- Commit messages should follow the existing repo convention (`✨ feat:`, `feat:`, plain prefix; recent commits use a mix). The `ui:` prefix used in this plan is fine for this redesign.
- Don't run `npm run build` between phases — `npx tsc --noEmit` is faster and surfaces the type errors that matter.
