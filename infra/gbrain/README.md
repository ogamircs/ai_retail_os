# GBrain (Track 6)

Persistent agent memory for the AI Retail OS cockpit. Same problem space as the [agentic Wiki (Track 5)](../../backend/app/spine/wiki.py), but a mature off-the-shelf product instead of a roll-our-own minimal version. Runs as a long-lived HTTP service speaking [Model Context Protocol (MCP)](https://modelcontextprotocol.io) on `localhost:8787`; every read-only specialist (Analyst, Critic) gets `brain_search` / `brain_get` / `brain_query` tools wired through `backend/app/llm/mcp.py`.

The cockpit is **mock-mode by default** — without `GBRAIN_BEARER` set in `backend/.env`, the brain tools return canned responses sourced from `substrate` + `wiki_pages`, the cockpit's `[BRAIN]` tab renders that mock corpus, and no external HTTP calls are made. Operators can flip the switch by spinning up GBrain locally (below) and exporting the bearer token.

## Quick start (real GBrain)

GBrain is built with [Bun](https://bun.sh) and ships its own CLI. The repo's own README warns against `bun install -g`, so we install it locally per their recommended pattern:

```bash
# 1. clone + link
git clone https://github.com/garrytan/gbrain.git ~/gbrain
cd ~/gbrain
bun install
bun link

# 2. one-shot init (creates the local PGLite store)
gbrain init

# 3. mint a bearer token (kept in backend/.env)
gbrain auth create --name "ai-retail-os" --print
# copy the token; it won't be shown again

# 4. start the HTTP MCP server (foreground, port 8787)
gbrain serve --http --port 8787
```

Then in `backend/.env`:

```
GBRAIN_BASE_URL=http://localhost:8787
GBRAIN_BEARER=<token from step 3>
```

Restart the cockpit (`uvicorn app.main:app --reload`). The status strip chip flips from `mock` to `brain n pages`.

## Disabled jobs

GBrain ships several recurring jobs (`gbrain jobs list`). For the demo we disable any that hit the public web by default — they're not destructive, but they take time and cost LLM calls. Enable selectively when the operator wants:

```bash
gbrain jobs disable web-research
gbrain jobs disable smoke-test-public
```

Keep on:
- `ingest` (ingests the cockpit's chat turns when G3's hook fires)
- `maintain` (compacts the brain corpus weekly)

## Reset

```bash
gbrain doctor      # diagnostics
gbrain corpus rm --confirm   # nukes the local PGLite store
gbrain init                  # bootstraps a fresh store
```

The cockpit is unaffected — it falls back to mock mode whenever the brain endpoint stops responding.

## Why no Docker compose

GBrain's recommended path is `bun install + bun link`. PGLite is embedded in the Bun binary; there's no DB container to stand up. Postgres mode exists for shared deployments — see `gbrain doctor --postgres` and the upstream README. For the cockpit demo, PGLite + a single `gbrain serve` process is enough.

## Apple Silicon

Bun ships native arm64 builds. No platform pin. If `bun link` errors with `permission denied`, try `BUN_INSTALL=$HOME/.bun bun install` to keep everything under your home directory.
