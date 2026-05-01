# MLflow tracking stack (demo)

Single-host MLflow for the AI Retail OS Track 4 rollout. Same shape as the other `infra/*` stacks — postgres metadata store + MinIO S3-compatible artifact store + the official `ghcr.io/mlflow/mlflow:v2.16.2` server.

## Quick start

```bash
make mlflow-up        # postgres + minio + bucket-init + mlflow on :5500
```

Tracking UI: <http://localhost:5500>
MinIO console: <http://localhost:9001> (admin / `retail-mlflow`)

`backend/.env`:

```env
MLFLOW_TRACKING_URI=http://localhost:5500
MLFLOW_S3_ENDPOINT_URL=http://localhost:9000
AWS_ACCESS_KEY_ID=admin
AWS_SECRET_ACCESS_KEY=retail-mlflow
MLFLOW_TRACE_ENABLED=1
```

When `MLFLOW_TRACE_ENABLED=1` is set, the backend's agent run-loop (Track 2 chief_of_staff) opens one MLflow run per operator turn with nested runs per specialist delegate + critic round. Without the env var, the trace logger no-ops so the cockpit demo path works unchanged when MLflow isn't running.

By default, runs land in MLflow's `Default` experiment. Pin the cockpit's runs into a dedicated experiment by also setting `MLFLOW_EXPERIMENT_NAME=ai-retail-os/cockpit` (or any name you like) in `backend/.env`. The trace logger only reads the env var — it never overrides an experiment the caller has already set, so the eval harness's per-scenario experiments (`eval/<scenario>/<sha>`) stay intact.

## Lifecycle

| Target | What it does |
|---|---|
| `mlflow-up` | `docker compose up -d` (no build — uses official images) |
| `mlflow-status` | `docker compose ps` |
| `mlflow-logs` | tail compose logs |
| `mlflow-down` | stop, keep volumes |
| `mlflow-nuke` | stop and wipe volumes (`docker compose down -v`) |

## Why MinIO

MLflow's S3 client treats the artifact root as a real S3 bucket. We could use the local filesystem instead (`--default-artifact-root /mlruns`), but the cockpit's eval harness (Track 2 A5 / Track 4 M3) writes 30-100KB transcripts per run and the S3 path scales cleanly when an operator decides to point at AWS later. Swapping MinIO for AWS is one env-var change.

## Port choice

Tracking server binds to host port `5500` (not the upstream-default `5000`) to avoid clashing with macOS's AirPlay Receiver, which silently grabs `:5000`. Override via `MLFLOW_HOST_PORT=5000 make mlflow-up` if you've turned AirPlay off.

## Eval pipe (Track 4 M3)

Once `MLFLOW_TRACKING_URI` is set in `backend/.env`, the eval harness:

```bash
RUN_EVAL=1 ANTHROPIC_API_KEY=... python -m tests.agents.eval.run_eval
```

logs each (scenario × mode) run to MLflow under experiment names of the form `eval/<scenario_name>/<git-sha>`. Metrics (factual_correctness, evidence_cited, policy_adherence, recommendation_quality, latency_ms, tokens_in, tokens_out) attach to each run. The full transcript + generated artifacts upload as MLflow artifacts so spot-checks live next to the score.

Compare runs in the UI (Compare button on the experiment view) to confirm multi-pass mesh strictly beats single-pass.

## Reset path

```bash
make mlflow-nuke && make mlflow-up
```

Wipes the postgres run table and the MinIO bucket. The cockpit's spine.db is untouched.
