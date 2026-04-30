import { useEffect, useState } from "react";
import { marked } from "marked";
import {
  applyIntegrationAction,
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

  useEffect(() => {
    if (!drawer) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer, close]);

  if (!drawer) return null;

  return (
    <>
      <div className="drawer-backdrop" data-testid="drawer-backdrop" onClick={close} />
      <aside className="drawer" data-testid="drawer">
        <header className="drawer-header">
          <button className="close" onClick={close} data-testid="drawer-close">✗ close</button>
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
  const [artifact, setArtifact] = useState<(ArtifactMeta & { body: string }) | null>(null);

  const action = drawer?.kind === "approval" ? drawer.action : null;
  const external = drawer?.kind === "approval" ? drawer.external : undefined;

  useEffect(() => {
    if (!action?.artifact_id) {
      setArtifact(null);
      return;
    }
    let cancelled = false;
    getArtifact(action.artifact_id)
      .then((a) => {
        if (!cancelled) setArtifact(a);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [action?.artifact_id]);

  if (!action) return null;

  const apply = async () => {
    if (!external) return;
    setPhase("applying");
    try {
      const r = await applyIntegrationAction(external.system_id, external.id);
      setResult(r);
      setPhase("applied");
      setTimeout(close, 2000);
    } catch (e: any) {
      setError(e?.message ?? "apply failed");
      setPhase("error");
    }
  };

  const reject = () => {
    window.dispatchEvent(
      new CustomEvent("approval-reject", { detail: { key: `q-${action.id}` } }),
    );
    close();
  };

  return (
    <>
      <div className="drawer-title-block">
        <span className="who" style={agentInkStyle(action.owner)}>{action.owner}</span>
        <h2 className="title">{action.title}</h2>
        <span className="ts">{new Date(action.ts).toUTCString().slice(17, 25)} UTC</span>
      </div>

      <dl className="kv">
        <dt>type</dt>
        <dd>{action.action_type}</dd>
        <dt>queue id</dt>
        <dd>{action.id}</dd>
        <dt>status</dt>
        <dd><span className="chip chip-warn">{action.status}</span></dd>
        {external && (
          <>
            <dt>external</dt>
            <dd>
              {external.system_id} · {external.external_domain} ·{" "}
              <span className="chip chip-info">{external.status}</span>
            </dd>
          </>
        )}
        {Object.entries(action.payload || {}).map(([k, v]) => (
          <PayloadRow key={k} k={k} v={v} />
        ))}
      </dl>

      {artifact && (
        <section className="artifact-render">
          <div className="label">artifact · {artifact.title}</div>
          <div
            className="md"
            dangerouslySetInnerHTML={{ __html: marked.parse(artifact.body) as string }}
          />
        </section>
      )}

      <footer className="drawer-actions">
        {phase === "idle" && (
          <>
            <button
              className="primary"
              onClick={apply}
              disabled={!external}
              data-testid="drawer-apply"
              title={!external ? "no external action linked" : "apply via integration adapter"}
            >
              apply → external
            </button>
            <button onClick={reject} data-testid="drawer-reject">reject</button>
          </>
        )}
        {phase === "applying" && <span className="dim">applying…</span>}
        {phase === "applied" && (
          <span className="chip chip-good" data-testid="drawer-applied">
            {result?.status ?? "applied"}
            {result?.result?.message ? ` — ${result.result.message}` : ""}
          </span>
        )}
        {phase === "error" && (
          <span className="chip chip-bad" data-testid="drawer-error">{error}</span>
        )}
      </footer>
    </>
  );
}

function PayloadRow({ k, v }: { k: string; v: any }) {
  if (v === null || v === undefined) return null;
  return (
    <>
      <dt>{k}</dt>
      <dd>
        {typeof v === "string" || typeof v === "number" ? (
          String(v)
        ) : (
          <pre>{JSON.stringify(v, null, 2)}</pre>
        )}
      </dd>
    </>
  );
}

function ArtifactContent() {
  const { drawer } = useDrawer();
  const [art, setArt] = useState<(ArtifactMeta & { body: string }) | null>(null);

  useEffect(() => {
    if (drawer?.kind !== "artifact") return;
    let cancelled = false;
    getArtifact(drawer.artifactId)
      .then((a) => {
        if (!cancelled) setArt(a);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [drawer]);

  if (drawer?.kind !== "artifact") return null;
  if (!art) return <div className="dim">loading…</div>;
  return (
    <>
      <div className="drawer-title-block">
        <span className="who" style={agentInkStyle(art.agent)}>{art.agent}</span>
        <h2 className="title">{art.title}</h2>
        <span className="ts">
          {art.kind} · {art.id}
        </span>
      </div>
      <section className="artifact-render">
        <div
          className="md"
          dangerouslySetInnerHTML={{ __html: marked.parse(art.body) as string }}
        />
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
    listEvents()
      .then((rows) => setEvents(rows.slice(0, 200)))
      .catch(() => {});
  }, [drawer]);

  if (drawer?.kind !== "event-tape") return null;

  const agents = Array.from(new Set(events.map((e) => e.agent)));
  const kinds = Array.from(new Set(events.map((e) => e.kind)));
  const filtered = events.filter(
    (e) =>
      (agentFilter === "all" || e.agent === agentFilter) &&
      (kindFilter === "all" || e.kind === kindFilter),
  );

  return (
    <>
      <div className="drawer-title-block">
        <h2 className="title">Event Log</h2>
      </div>
      <div className="filters">
        <label>
          agent{" "}
          <select value={agentFilter} onChange={(e) => setAgentFilter(e.target.value)}>
            <option value="all">all</option>
            {agents.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
        </label>
        <label>
          kind{" "}
          <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
            <option value="all">all</option>
            {kinds.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="event-list" data-testid="event-list">
        {filtered.length === 0 && <div className="empty">── no events ──</div>}
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
