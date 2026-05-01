import { useEffect, useState } from "react";
import { getMlflowStatus, MlflowStatus } from "../../lib/api";
import "./MlflowTab.css";

/**
 * Cockpit-side MLflow surface (Track 4 follow-up).
 *
 * Renders a live experiment/runs summary above an embedded iframe of
 * the MLflow tracking UI. Polls `/api/mlflow/status` every 5s. Three
 * states:
 *
 *  - **Disabled** (`MLFLOW_TRACKING_URI` unset): green-on-black
 *    instruction panel + a link to the Track 4 README.
 *  - **Unreachable** (URL set but the server doesn't answer): red
 *    chip with the error string + a manual retry button.
 *  - **Connected**: experiment/runs counts + the iframe.
 *
 * The iframe is intentionally lazy — the URL is the same one the
 * backend proxy uses, so opening the tab triggers a full-page load.
 * Cross-origin iframe-restriction headers from MLflow can block this
 * in a hardened deploy; in that case the operator gets a "open in
 * new window" link as a fallback.
 */
export default function MlflowTab() {
  const [status, setStatus] = useState<MlflowStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [iframeError, setIframeError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await getMlflowStatus(15);
        if (!cancelled) {
          setStatus(s);
          setLoading(false);
        }
      } catch {
        if (!cancelled) {
          setStatus(null);
          setLoading(false);
        }
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (loading) {
    return <div className="mlflow-tab" data-testid="tab-mlflow"><div className="mlflow-empty">── loading mlflow status ──</div></div>;
  }

  if (!status?.enabled) {
    return (
      <div className="mlflow-tab" data-testid="tab-mlflow">
        <div className="mlflow-empty mlflow-disabled">
          <h3>MLflow tracking is not configured.</h3>
          <p>
            Set <code>MLFLOW_TRACKING_URI</code> in <code>backend/.env</code> and restart
            the API. Spin up a local MLflow with{" "}
            <code>make mlflow-up</code> (default URL{" "}
            <code>http://localhost:5500</code>); see{" "}
            <code>infra/mlflow/README.md</code> for the full path.
          </p>
          <p>
            With <code>MLFLOW_TRACE_ENABLED=1</code> on top of that, every operator
            chat turn opens a parent run with nested per-delegate / per-critic /
            per-revision frames.
          </p>
        </div>
      </div>
    );
  }

  if (!status.reachable) {
    return (
      <div className="mlflow-tab" data-testid="tab-mlflow">
        <div className="mlflow-empty mlflow-error">
          <h3>MLflow tracking server unreachable.</h3>
          <p>
            <code>MLFLOW_TRACKING_URI</code> is configured (
            <code>{status.ui_url}</code>) but the server didn't answer.
          </p>
          {status.error && <p className="error-detail">{status.error}</p>}
          <p>
            Check the stack: <code>make mlflow-status</code>. Tail logs:{" "}
            <code>make mlflow-logs</code>.
          </p>
        </div>
      </div>
    );
  }

  const ui = status.ui_url!;
  return (
    <div className="mlflow-tab" data-testid="tab-mlflow">
      <header className="mlflow-summary" data-testid="mlflow-summary">
        <span className="chip chip-good" data-testid="mlflow-status-chip">connected</span>
        <span className="mlflow-stat">
          <span className="label">EXPERIMENTS</span>
          <span className="value">{status.experiments.length}</span>
        </span>
        <span className="mlflow-stat">
          <span className="label">RUNS (recent)</span>
          <span className="value">{status.recent_runs.length}</span>
        </span>
        <span className="mlflow-spacer" />
        <a
          className="mlflow-open-out"
          href={ui}
          target="_blank"
          rel="noreferrer noopener"
          data-testid="mlflow-open-out"
        >
          open in new window ↗
        </a>
      </header>

      <section className="mlflow-runs-table" data-testid="mlflow-runs-table">
        <table>
          <thead>
            <tr>
              <th>Started</th>
              <th>Experiment</th>
              <th>Run</th>
              <th>Phase</th>
              <th>Agent</th>
              <th>Status</th>
              <th className="num">Latency (ms)</th>
              <th className="num">Total</th>
            </tr>
          </thead>
          <tbody>
            {status.recent_runs.map((r) => (
              <tr key={r.run_id} data-testid={`mlflow-run-${r.run_id}`}>
                <td>{r.start_time ? new Date(r.start_time).toUTCString().slice(17, 25) : "—"}</td>
                <td title={r.experiment_id}>{r.experiment_name ?? r.experiment_id}</td>
                <td>{r.run_name ?? r.run_id.slice(0, 8)}</td>
                <td>{r.phase ?? "—"}</td>
                <td>{r.agent ?? "—"}</td>
                <td><span className={`chip ${r.status === "FINISHED" ? "chip-good" : r.status === "FAILED" ? "chip-bad" : "chip-info"}`}>{r.status ?? "—"}</span></td>
                <td className="num">{r.metrics?.latency_ms !== undefined ? Math.round(r.metrics.latency_ms) : "—"}</td>
                <td className="num">{r.metrics?.total_score !== undefined ? r.metrics.total_score.toFixed(0) : "—"}</td>
              </tr>
            ))}
            {status.recent_runs.length === 0 && (
              <tr>
                <td colSpan={8} style={{ textAlign: "center", color: "var(--ink-mute)" }}>
                  ── no runs yet — fire a chat turn with <code>MLFLOW_TRACE_ENABLED=1</code> ──
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="mlflow-iframe-wrap" data-testid="mlflow-iframe-wrap">
        {iframeError ? (
          <div className="mlflow-empty">
            <p>
              MLflow's UI refused to embed (likely{" "}
              <code>X-Frame-Options</code> set on the tracking server).
            </p>
            <p>
              <a href={ui} target="_blank" rel="noreferrer noopener" className="mlflow-open-out">
                open the UI in a new window ↗
              </a>
            </p>
          </div>
        ) : (
          <iframe
            src={ui}
            title="MLflow tracking UI"
            data-testid="mlflow-iframe"
            onError={() => setIframeError(true)}
          />
        )}
      </section>
    </div>
  );
}
