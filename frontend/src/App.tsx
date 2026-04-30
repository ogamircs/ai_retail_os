import { useState } from "react";
import Chat from "./components/Chat";
import EventLog from "./components/EventLog";
import Artifacts from "./components/Artifacts";
import AgentBadge from "./components/AgentBadge";
import Dashboard from "./components/Dashboard";

export default function App() {
  const [refreshKey, setRefreshKey] = useState(0);
  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <span className="logo">⬢</span>
          <span className="title">AI Retail OS</span>
          <span className="subtitle">HQ Console</span>
        </div>
        <AgentBadge />
      </header>
      <main className="app-main">
        <section className="cockpit">
          <Dashboard refreshKey={refreshKey} />
        </section>
        <section className="command-rail">
          <Chat onEvent={() => setRefreshKey((k) => k + 1)} />
        </section>
        <aside className="audit-rail">
          <EventLog refreshKey={refreshKey} />
          <Artifacts refreshKey={refreshKey} />
        </aside>
      </main>
    </div>
  );
}
