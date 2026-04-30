# AI Retail OS — Prototype

A vertical-slice prototype of the "AI Retail OS" architecture: an orchestrator agent ("Chief of Staff") routes operator intent to specialist DRI agents, each acting through a queryable spine (event log, artifact store, knowledge graph) over mocked retail systems of record.

The current PoC is an omnichannel executive control tower: Marketing can push selected categories, Pricing can create promotions and markdowns, Merchandising can rebalance stores, Fulfillment can route demand, Replenishment can hold or expedite inbound POs, Store Manager can create store tasks, and Analyst can measure the closed loop.

## Layer mapping

| Architecture layer | This repo |
|---|---|
| Surfaces — HQ Console | `frontend/` (React + Vite executive cockpit + chat) |
| Agent Mesh — Chief of Staff + specialists | `backend/app/agents/` |
| Data Spine — Event Log · Artifact Store · KG | `backend/app/spine/`, `artifacts/`, `backend/data/spine.db` |
| Substrate — POS · Inventory · Stores · Orders · Campaigns · POs | `backend/app/substrate/` |
| LLM provider abstraction | `backend/app/llm/` (Anthropic / OpenAI / Google) |

## Setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env
# Edit .env — set LLM_PROVIDER and the matching API key

# Seed mock data
python -m app.substrate.seed

# Run backend (port 8000)
uvicorn app.main:app --reload
```

Frontend (separate shell):

```bash
cd frontend
npm install
npm run dev
# Open http://localhost:5173
```

## Demo path

In the chat, type:

> *We're carrying too much summer apparel. Analyze the situation and propose a markdown plan, then hold any inbound POs for affected SKUs.*

You should see:
- Chief of Staff routes to Analyst → Pricing → Replenishment
- Event Log fills with `decision`, `action`, `observation` rows
- 3+ artifacts appear (analysis, markdown plan, replenishment plan)
- `substrate_inventory.price` updates for marked-down SKUs

Then ask:

> *Did the markdowns lift sales for summer apparel?*

Analyst will read events since the markdown timestamp, query sales, and report.

## Omnichannel demo path

The cockpit loads a seeded operating picture with category momentum, campaign queue, action queue, inventory risk, store exceptions, and supplier risk.

In the chat, type:

> *We have excess summer inventory, uneven store demand, and a weekend heatwave. Build a marketing push for the right categories, decide markdowns, route fulfillment, rebalance stores, hold risky inbound POs, and show expected margin impact.*

You should see:
- Chief of Staff routes across Analyst, Marketing, Merchandiser, Pricing & Promo, Fulfillment, Replenishment, and Store Manager as needed
- Marketing creates category-push campaign briefs and can launch mocked campaigns
- Operational agents add proposed/launched/measured work to the action queue
- Event Log records `proposal`, `campaign_launch`, `approval_required`, `action`, and `measurement` rows
- Artifact Store shows only artifacts attached to the current spine events

Then ask:

> *Did the category push work? Measure lift, ROI, margin impact, fulfillment cost, and remaining risks.*

Marketing and Analyst can read campaign records, orders, sales, and prior spine events to produce a post-action readout.

## New read APIs

- `GET /api/kpis`
- `GET /api/categories`
- `GET /api/marketing/campaigns`
- `GET /api/inventory/health`
- `GET /api/stores`
- `GET /api/orders`
- `GET /api/action-queue`
- `GET /api/kg/neighborhood?id=summer_apparel`

## Provider swap

In the header, change the provider dropdown (Anthropic / OpenAI / Google). Identical behavior, different model. Requires the corresponding API key in `.env`.

## What's stubbed (non-goals)

- Real production integrations to POS, OMS, WMS/3PL, CRM/CDP, EDI, ad networks, payment networks, or finance systems
- Loop Scheduler is mocked through measurement tools rather than cron
- Policy & Spec Registry as a separate human-editable surface (currently embedded as system prompts)
- Auth, multi-tenancy
