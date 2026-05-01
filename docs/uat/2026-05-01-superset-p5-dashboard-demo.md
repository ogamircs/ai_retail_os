# Superset P5 — analyst dashboard demo UAT log

**Date:** 2026-05-01
**Scope:** End-to-end run of the Analyst-handoff flow against a real
Apache Superset 3.x instance: operator asks the cockpit Analyst to
explain a category drop → Analyst's report deep-links into the
Superset dashboard backed by the same `spine.db` the cockpit reads.

> **Status:** template walkthrough. Steps + acceptance gates are
> scripted and reproducible from the CLI; UI screenshots are an
> operator follow-up. Mark each step with the actual screenshot path
> under `img/` once captured.

## Setup

Local stack up + seeded:

```bash
make superset-up         # postgres + redis + apache/superset:3.1.1
make superset-bootstrap  # db upgrade + admin user (idempotent)
make superset-seed       # registers spine.db + 3 datasets + 3 charts + 1 dashboard
```

Sign in at <http://localhost:8088> with `admin` / `retail-superset`.

`backend/.env`:

```env
SUPERSET_BASE_URL=http://localhost:8088
SUPERSET_USERNAME=admin
SUPERSET_PASSWORD=retail-superset
```

Backend on `:8000`, frontend on `:5173`, Superset on `:8088`. After
restarting uvicorn the cockpit's Integrations tab shows the `superset`
row as `connected`.

Pre-UAT sanity:

```bash
# JWT round-trip
curl -s -X POST 'http://localhost:8088/api/v1/security/login' \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$SUPERSET_USERNAME\",\"password\":\"$SUPERSET_PASSWORD\",\"provider\":\"db\",\"refresh\":true}" \
  | jq -r '.access_token' | head -c 20; echo
# → eyJ0eXAi...

# Inbound sync via the cockpit
curl -s -X POST http://localhost:8000/api/integrations/superset/sync | jq '.result.summary'
# → {mode: connected, domains: {Database: 1, Dataset: 3, Chart: 3, Dashboard: 1}}
```

## Step 1 — Analyst delegation

Cockpit chat:

> why did summer-apparel sell-through tank last week?

Chief delegates to **Analyst**. Analyst tool-calls `aggregate_by_category`
+ `daily_sales` from substrate, writes a `report` artifact summarising
the dip, and quotes the Superset dashboard URL deep-link from the
cockpit's `external_refs` cache.

> **Screenshot:** `img/superset-p5-1-analyst-report.png`

## Step 2 — Operator opens the dashboard

The Analyst's report renders an inline link:

```
External BI: AI Retail OS — Demo · http://localhost:8088/superset/dashboard/ai-retail-os-demo/
```

Click → Superset's dashboard view. The three charts ("Top SKUs",
"Orders by channel", "Inventory by store") are backed by the same
`spine.db` the cockpit reads, so the numbers match.

> **Screenshot:** `img/superset-p5-2-superset-dashboard.png`

## Step 3 — Round-trip via inbound sync

```bash
curl -s -X POST http://localhost:8000/api/integrations/superset/sync | jq '.result.summary'
# → {mode: connected, domains: {Database: 1, Dataset: 3, Chart: 3, Dashboard: 1}}
```

Drilling into the cockpit's `external_refs` table for system_id=`superset`:

```bash
sqlite3 backend/data/spine.db \
  "SELECT domain, local_id, external_id FROM external_refs WHERE system_id='superset';"
# Database|AI Retail OS spine|1
# Dataset|substrate_skus|2
# Dataset|substrate_orders|3
# Dataset|substrate_inventory|4
# Chart|Retail · Top SKUs|1
# Chart|Retail · Orders by channel|2
# Chart|Retail · Inventory by store|3
# Dashboard|ai-retail-os-demo|1
```

The cockpit's Integrations row last-sync timestamp updates. Drilling
into the Dashboard row in `record_cache` shows `external_id=1` aligned
with the seeded slug `ai-retail-os-demo`.

## Outbound apply (intentionally N/A)

Superset is **read-only** in our architecture. No agent emits an
action_type that mutates Superset state — we deep-link operators into
existing dashboards rather than synthesising new ones from the
cockpit. `apply_outbound` therefore falls back to the
`IntegrationAdapter` base class:

- adapter unconfigured → `applied_mock` (no HTTP call)
- adapter configured → `draft_created` (no HTTP call, marker only)

This is intentional. If a future Analyst agent ever needs to
auto-create dashboards (e.g. one-shot incident reports), the
extension point is `LIVE_ACTION_TYPES`, currently empty.

## Result

End-to-end agent loop runs against a real Superset:

- Analyst proposes → report quotes the seeded dashboard URL.
- Operator clicks the deep-link → Superset shows charts over the same `spine.db`.
- Inbound sync round-trips Database / Dataset / Chart / Dashboard rows back to the substrate.
- Read-only stance is explicit; no outbound apply path needed.

## Acceptance checklist

Operator must verify each before promoting this UAT:

- [ ] `make superset-bootstrap` succeeds; admin login works at `/`.
- [ ] `make superset-seed` completes with zero `++` lines on the second run.
- [ ] `POST /api/integrations/superset/sync` reports `mode=connected`. `Database` ≥ 1, `Dataset` ≥ 3, `Chart` ≥ 3, `Dashboard` ≥ 1.
- [ ] JWT round-trip (login endpoint) returns an `access_token`.
- [ ] Cockpit Integrations row shows `superset · connected · last sync …`.
- [ ] Analyst report deep-link opens the seeded dashboard at `/superset/dashboard/ai-retail-os-demo/`.
- [ ] Numbers in the Superset charts match `aggregate_by_category` returned by the cockpit Analyst (same `spine.db`).
- [ ] no Superset-side errors in `make superset-logs` during the demo.

## Notes

- The seed mounts `backend/data/` read-only into the container at
  `/spine/`, so the cockpit and Superset share the same SQLite file
  without copy/sync. If the cockpit writes new substrate rows after
  the dashboard is open, hit the chart's "Force refresh" button — the
  default cache TTL is short but non-zero.
- Screenshots are an operator follow-up. Replace each `> **Screenshot:** …`
  block with the captured PNG path under `docs/uat/img/`.
