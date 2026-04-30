import { useEffect, useState } from "react";
import { listEvents, SpineEvent } from "../lib/api";

interface Props {
  refreshKey: number;
}

export default function EventLog({ refreshKey }: Props) {
  const [events, setEvents] = useState<SpineEvent[]>([]);

  const refresh = async () => {
    try {
      const evs = await listEvents();
      setEvents(evs);
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 2500);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    refresh();
  }, [refreshKey]);

  return (
    <div className="panel event-log">
      <div className="panel-header">
        <span>Event Log</span>
        <span className="count">{events.length}</span>
      </div>
      <div className="panel-body">
        {events.length === 0 && <div className="empty">No events yet.</div>}
        {events.map((e) => (
          <div key={e.id} className={`row kind-${e.kind}`}>
            <div className="row-meta">
              <span className="ts">
                {new Date(e.ts).toLocaleTimeString()}
              </span>
              <span className="agent">{e.agent}</span>
              <span className="kind">{e.kind}</span>
              {e.sku && <span className="sku">{e.sku}</span>}
            </div>
            <div className="row-payload">
              {e.payload.action && <strong>{e.payload.action}</strong>}{" "}
              {e.payload.summary || e.payload.artifact_title || e.payload.reason || ""}
              {e.payload.percent != null && ` ${e.payload.percent}%`}
              {e.payload.qty_ordered != null && ` qty=${e.payload.qty_ordered}`}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
