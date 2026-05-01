import { useEffect, useRef, useState } from "react";
import { listEvents, SpineEvent } from "../lib/api";
import { cacheEvents, readCachedEvents } from "../lib/eventCache";
import { useDrawer } from "../lib/drawerContext";
import { agentInkStyle } from "../lib/agentInk";
import "./EventTape.css";

const MAX = 30;
// After this many ms without a successful poll we declare "offline" and
// re-hydrate the visible tape from the local cache. The threshold is
// 2× the poll interval so a single dropped request doesn't flicker the
// state.
const OFFLINE_THRESHOLD_MS = 5000;

export default function EventTape() {
  const [events, setEvents] = useState<SpineEvent[]>([]);
  const [lag, setLag] = useState<number>(0);
  const [offline, setOffline] = useState<boolean>(false);
  const lastIdRef = useRef<number>(0);
  const lastTickRef = useRef<number>(Date.now());
  const lastSuccessRef = useRef<number>(Date.now());
  const offlineHydratedRef = useRef<boolean>(false);
  const { open } = useDrawer();

  // Track 3 B5: hydrate the tape from IndexedDB on mount so the first
  // paint never shows "── no events yet ──" if there's a cache. This is
  // also the offline-resume path — even before the network comes back,
  // the operator can scrub the last 200 events.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const cached = await readCachedEvents(MAX);
      if (cancelled || cached.length === 0) return;
      lastIdRef.current = Math.max(lastIdRef.current, ...cached.map((r) => r.id));
      setEvents(cached);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

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
          // Persist to IndexedDB so an offline reload still shows the
          // tape. The cache helper trims to MAX_CACHED itself.
          void cacheEvents(rows);
        }
        setLag(start - lastTickRef.current);
        lastTickRef.current = start;
        lastSuccessRef.current = start;
        if (offline) setOffline(false);
        offlineHydratedRef.current = false;
      } catch {
        const now = Date.now();
        setLag(now - lastTickRef.current);
        if (now - lastSuccessRef.current > OFFLINE_THRESHOLD_MS) {
          if (!offline) setOffline(true);
          // Hydrate once per offline streak to avoid flicker; once
          // reconnected, the success branch above resets the flag.
          if (!offlineHydratedRef.current) {
            offlineHydratedRef.current = true;
            const cached = await readCachedEvents(MAX);
            if (!cancelled && cached.length > 0) {
              setEvents(cached);
            }
          }
        }
      }
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [offline]);

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
      {offline && (
        <span className="lag" data-testid="event-tape-offline">
          ⚡ offline · cached
        </span>
      )}
      {!offline && lag > 10000 && <span className="lag">… catching up …</span>}
      {events.length === 0 && lag <= 10000 && !offline && (
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
