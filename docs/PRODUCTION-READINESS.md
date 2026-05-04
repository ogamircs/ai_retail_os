# Production readiness boundary

`ai_retail_os` is a **prototype**. Every piece below is intentionally
unfinished — they're the things a real production deployment would have
to add before exposing the API to anything beyond a developer laptop or
internal demo. Treat this list as the gap analysis between "demoable"
and "deployable".

## What is intentionally not production-ready

| Area | Today | What "production" needs |
|------|-------|--------------------------|
| **Auth** | None. Every API call lands as the implicit `Operator` agent. | OAuth (or SSO) on the cockpit; service tokens on integration writes; per-user audit. |
| **Tenant isolation** | Single global SQLite (`backend/data/spine.db`). | Per-tenant DB or schema; row-level security on the agent surfaces. |
| **Secrets rotation** | `.env` files. Flat, long-lived. | Vault/SSM/KMS fetch on startup; rotation cadence; revocation drill. |
| **Schema migrations** | Append-only ledger via `app.spine.migrations` (PR #43). Never tested with a backwards-incompatible change. | Online migration story (read-old / write-new), shadow tables, rollback runbook. |
| **Durable workers** | DSPy compiles + improvement audits run in daemon threads (`background_jobs` table tracks state, but the worker itself is in-process). | Real queue (RQ / Celery / Cloudflare Workers) with retry, DLQ, and timeout. |
| **Rate limiting** | None. | Per-route + per-tenant limits; backoff on adapter calls. |
| **External-system retry policy** | Per-adapter ad-hoc — Mautic / Akeneo refresh on 401, others raise. | Centralised retry with budget, circuit breaker, observability hooks. |
| **Audit retention** | `events`, `outbox_actions`, `wiki_revisions` grow unbounded. | Cold storage tier; retention policy per kind; compaction. |
| **Observability** | `print()` for sync runs; MLflow optional; no tracing. | Structured logs (JSON), metrics (Prometheus / OpenTelemetry), distributed traces, on-call dashboards. |
| **Multi-region** | All paths assume one process, one disk. | Active-active read replicas; outbox writes routed through a single primary. |
| **PII / GDPR** | Customer rows seeded with synthetic data. No deletion API. | Subject-access flow; deletion + export endpoints; PII tagging on schemas. |
| **Frontend auth-aware UX** | Cockpit renders without identity; chat history stays in the browser. | Real session, role-based UI gates, consistent error surfaces. |
| **Backups** | None. | `spine.db` snapshot cadence; off-host artifact retention; restore drill. |

## Ship-blockers vs nice-to-haves

The first six rows (auth → durable workers) are **ship-blockers** for
any deployment beyond a single developer's laptop. The rest are
operational tax that a real customer will hit within weeks.

## Where to land each fix

- **Auth** → middleware in `app/main.py` plus a `routes/auth.py`
  router. Cockpit will need a `<SignIn />` component before the rails.
- **Tenant isolation** → `app/config.py` should resolve `DB_PATH` per
  request (e.g. tenant id from JWT). Every adapter env (`SHOPIFY_*`)
  becomes per-tenant.
- **Durable workers** → swap `threading.Thread` calls in
  `app/routes/dspy.py` and `app/agents/improvement_auditor.py` for a
  real queue client. The `background_jobs` table is already the right
  shape for the job ledger.
- **Audit retention** → cron-style task that moves rows older than N
  days into a partitioned archive. `events.kind` is now a closed set
  (PR #42) so partitioning by kind is straightforward.
- **Observability** → wrap `app.spine.events.append_event` to emit a
  structured log record per write; expose Prometheus counters from
  `app/main.py` startup hook.

## What this list is NOT

This file isn't a roadmap. It's a **boundary** — the line between
prototype and production. When work crosses the line (e.g. shipping to
a paying customer), the gap analysis above becomes the migration plan.
Until then, keep the prototype shape: small, queryable spine,
mock-friendly adapters, single-process FastAPI app.
