import { useState } from "react";
import StatusStrip from "./StatusStrip";
import Chat from "./Chat";
import ApprovalRail from "./ApprovalRail";
import EventTape from "./EventTape";
import ApprovalDrawer from "./ApprovalDrawer";
import { useDashboardData } from "../lib/data";
import "./MobileShell.css";

/**
 * Track 3 B6 — phone-shaped operator companion.
 *
 * The cockpit's 3-rail Bloomberg layout doesn't render on a phone, so
 * below the 720px breakpoint we switch to a tabbed view that surfaces
 * the operator's three primary touchpoints:
 *
 *   - Approvals (default tab) — pending queue with full external chips
 *   - Chat       — Chief of Staff command rail
 *   - Tape       — recent audit events (still uses the offline cache
 *                  from B5 so the operator can scrub on a flaky link)
 *
 * The data rail (categories / stores / inventory tabs) is dropped:
 * those are operator-on-laptop tools, not phone-on-the-go ones. If
 * the operator needs them, the desktop cockpit (or the Tauri desktop
 * shell) is still the right tool.
 */
type Tab = "approvals" | "chat" | "tape";

export default function MobileShell() {
  const { bump } = useDashboardData();
  const [tab, setTab] = useState<Tab>("approvals");

  return (
    <div className="mobile-shell" data-testid="mobile-shell">
      <StatusStrip />
      <nav className="mobile-tabs" data-testid="mobile-tabs">
        <button
          className={tab === "approvals" ? "active" : ""}
          onClick={() => setTab("approvals")}
          data-testid="mobile-tab-approvals"
        >
          [APPROVALS]
        </button>
        <button
          className={tab === "chat" ? "active" : ""}
          onClick={() => setTab("chat")}
          data-testid="mobile-tab-chat"
        >
          [CHAT]
        </button>
        <button
          className={tab === "tape" ? "active" : ""}
          onClick={() => setTab("tape")}
          data-testid="mobile-tab-tape"
        >
          [TAPE]
        </button>
      </nav>
      <main className="mobile-main">
        {/* Each panel renders unconditionally so its internal hooks
            stay mounted (chat session, approval refresh polling, tape
            cache hydrate) — we just hide the inactive ones via CSS. */}
        <section className={tab === "approvals" ? "panel active" : "panel"}>
          <ApprovalRail />
        </section>
        <section className={tab === "chat" ? "panel active" : "panel"}>
          <Chat onEvent={bump} />
        </section>
        <section className={tab === "tape" ? "panel active mobile-tape-panel" : "panel mobile-tape-panel"}>
          <EventTape />
        </section>
      </main>
      <ApprovalDrawer />
    </div>
  );
}
