import { useEffect, useRef, useState } from "react";
import { listEvents, SpineEvent } from "../lib/api";
import { useDrawer } from "../lib/drawerContext";
import { agentInkStyle } from "../lib/agentInk";
import "./EventTape.css";

const MAX = 30;

export default function EventTape() {
  const [events, setEvents] = useState<SpineEvent[]>([]);
  const [lag, setLag] = useState<number>(0);
  const lastIdRef = useRef<number>(0);
  const lastTickRef = useRef<number>(Date.now());
  const { open } = useDrawer();

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const start = Date.now();
      try {
        const rows = await listEvents(lastIdRef.current);
        if (cancelled) return;
        if (rows.length > 0) {
          const fresh = [...rows].reverse();
          lastIdRef.current = Math.max(lastIdRef.current, ...rows.map((r) => r.id));
          setEvents((prev) => [...fresh, ...prev].slice(0, MAX));
        }
        setLag(start - lastTickRef.current);
        lastTickRef.current = start;
      } catch {
        setLag(Date.now() - lastTickRef.current);
      }
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (
        e.key === "`" &&
        !(e.target instanceof HTMLInputElement) &&
        !(e.target instanceof HTMLTextAreaElement)
      ) {
        e.preventDefault();
        open({ kind: "event-tape", events });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [events, open]);

  return (
    <div
      className="event-tape"
      data-testid="event-tape"
      onClick={() => open({ kind: "event-tape", events })}
      title="click or press ` for full event log"
    >
      {lag > 10000 && <span className="lag">… catching up …</span>}
      {events.length === 0 && lag <= 10000 && (
        <span className="empty">── no events yet ──</span>
      )}
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
  if (p.action) return String(p.action);
  if (p.summary && typeof p.summary === "string") return p.summary.slice(0, 60);
  if (p.artifact_title) return String(p.artifact_title);
  return e.sku ?? "";
}
