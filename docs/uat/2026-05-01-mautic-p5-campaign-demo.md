# Mautic P5 — heatwave campaign demo UAT log

**Date:** 2026-05-01
**Scope:** End-to-end run of the `campaign_brief` + `campaign_launch` flow against a real Mautic 5.x instance: operator approves a Marketing proposal → idempotent Segment reused + new draft Campaign lands in Mautic.

> **Status:** template walkthrough. Steps + acceptance gates are scripted and reproducible from the CLI; UI screenshots are an operator follow-up. Mark each step with the actual screenshot path under `img/` once captured.

## Setup

Local stack up + seeded:

```bash
make mautic-up            # mariadb + mautic apache + cron + worker
make mautic-bootstrap     # one-shot install, prints admin creds
# Sign in at http://localhost:8081 with admin / <printed pw>.
make mautic-seed          # 4 Segments + 20 Contacts + N draft Campaigns
```

`backend/.env`:

```env
MAUTIC_BASE_URL=http://localhost:8081
MAUTIC_USERNAME=admin
MAUTIC_PASSWORD=<your-admin-pw>
```

Backend on `:8000`, frontend on `:5173`, Mautic on `:8081`. After restarting uvicorn the cockpit's Integrations tab shows the `mautic` row as `connected`.

Pre-UAT sanity:

```bash
# Token round-trip via Basic auth
curl -s -u "$MAUTIC_USERNAME:$MAUTIC_PASSWORD" \
  http://localhost:8081/api/segments | jq '.total'
# → 4

# Inbound sync via the cockpit
curl -s -X POST http://localhost:8000/api/integrations/mautic/sync | jq '.result.summary'
# → {mode: connected, domains: {Segment: 4, Contact: 20, Campaign: N}}
```

## Step 1 — Marketing proposes the heatwave brief

Cockpit chat:

> draft a heatwave campaign for summer apparel, push to vacationers and the loyalty tier

Chief delegates to **Marketing**. Marketing emits two proposals:

```json
{
  "action_type": "campaign_brief",
  "external_domain": "Segment",
  "payload": {"alias": "seg_vacation"}
}
{
  "action_type": "campaign_launch",
  "external_domain": "Campaign",
  "payload": {"campaign_id": "cmp_heatwave_2026", "title": "Heatwave 2026 — Summer Apparel"}
}
```

Both rows land in `approval_required` and surface in the cockpit Pending rail. Status strip: `APPROVE 2!` chip pulses amber.

> **Screenshot:** `img/mautic-p5-1-cockpit-before.png`

## Step 2 — Operator opens the drawer

Click `▶ Heatwave 2026 — Summer Apparel`.

Drawer renders `payload`, the proposed Mautic `Segment` chip, and the proposed `Campaign` chip. Pick the mautic chip on the brief row first and click `apply → external`.

> **Screenshot:** `img/mautic-p5-2-drawer-before-brief.png`

## Step 3 — Adapter dispatches the brief

`MauticAdapter.apply_outbound` → `_mautic_ensure_segment`:

1. `_mautic_find_one("/api/segments", "lists", filters=[("alias", "eq", "seg_vacation")])` — already seeded.
2. Match → return existing list id; no POST.
3. `result.message` → `Reused existing Mautic segment 'seg_vacation' (id=<n>).` `details.reused=true`.

Drawer flashes:

```
applied — Reused existing Mautic segment 'seg_vacation' (id=2).
```

The brief row's `outbox_actions.status` flips to `draft_created`; `external_id` mirrors the Mautic list id.

> **Screenshot:** `img/mautic-p5-3-drawer-after-brief.png`

## Step 4 — Operator approves the launch

Pick the mautic chip on the launch row and click `apply → external`.

`_mautic_create_campaign`:

1. `POST /api/campaigns/new` with body
   ```json
   {
     "name": "Heatwave 2026 — Summer Apparel",
     "isPublished": false,
     "description": "[retail-os:cmp_heatwave_2026] Heatwave 2026 — Summer Apparel"
   }
   ```
2. Mautic returns `{campaign: {id: 7, ...}}`.
3. `external_id = 7`; `result.message` → `Created Mautic campaign 'Heatwave 2026 — Summer Apparel' (id=7).`

> **Screenshot:** `img/mautic-p5-4-drawer-after-launch.png`

## Step 5 — Verify in Mautic admin UI

Open `http://localhost:8081/s/campaigns`. New row:

```
Heatwave 2026 — Summer Apparel    Unpublished    description: [retail-os:cmp_heatwave_2026] …
```

Click into it — Mautic shows the draft state ("Add an action to publish").

> **Screenshot:** `img/mautic-p5-5-mautic-campaign-list.png`

Open `http://localhost:8081/s/segments` — `seg_vacation` (id=2) unchanged: contacts still attached, no duplicate row.

> **Screenshot:** `img/mautic-p5-6-mautic-segments-list.png`

## Step 6 — Round-trip via inbound sync

```bash
curl -s -X POST http://localhost:8000/api/integrations/mautic/sync | jq '.result.summary'
# → {mode: connected, domains: {Segment: 4, Contact: 20, Campaign: N+1}}
```

The cockpit's Integrations row last-sync timestamp updates. Drilling into the Campaign row in `record_cache` shows `external_id=7` aligned with the substrate `cmp_heatwave_2026` via the `[retail-os:…]` marker parser from P3.

## Idempotency

- `campaign_brief` is naturally idempotent (alias-eq lookup → reuse).
- `campaign_launch` is one-shot via `outbox_actions.status` short-circuit. Manual re-runs (calling the adapter directly) WILL create a new Mautic campaign because Mautic has no native dedup on `description`. This is fine — the cockpit drawer is the operator-facing path and never duplicates.

## Result

End-to-end agent loop runs against a real Mautic:

- Marketing proposes → two outbox rows in `approval_required`.
- Cockpit drawer surfaces both. Brief reuses the seeded `seg_vacation`. Launch creates a draft Campaign with the `[retail-os:cmp_heatwave_2026]` marker.
- Inbound sync round-trips the new Campaign id back to the substrate.
- `result.details.reused=true` on the brief makes the no-op visible in the audit log.

## Acceptance checklist

Operator must verify each before promoting this UAT:

- [ ] `make mautic-bootstrap` succeeds; admin login works at `/s/dashboard`.
- [ ] `make mautic-seed` completes with zero `++` lines on the second run.
- [ ] `POST /api/integrations/mautic/sync` reports `mode=connected`. `Segment`, `Contact`, `Campaign` domains > 0.
- [ ] Token round-trip (Basic auth) succeeds against `/api/segments`.
- [ ] Cockpit Pending rail surfaces both `campaign_brief` and `campaign_launch` rows.
- [ ] Drawer apply on the brief returns `details.reused=true`.
- [ ] Drawer apply on the launch creates a new draft campaign in `/s/campaigns` carrying the `[retail-os:…]` marker.
- [ ] Second sync surfaces the Mautic campaign id back; cockpit Integrations row shows last-sync timestamp updated.
- [ ] no Mautic-side errors in `make mautic-logs` during the demo.

## Notes

- The Marketing agent's chat path is operator-driven; the substrate stand-in (calling `app.substrate.omnichannel.queue_marketing_brief(...)`) gives the same outbox shape if the chat path is unavailable in your environment.
- Screenshots are an operator follow-up. Replace each `> **Screenshot:** …` block with the captured PNG path under `docs/uat/img/`.
