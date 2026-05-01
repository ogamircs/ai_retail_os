import StatusStrip from "./components/StatusStrip";
import DataRail from "./components/DataRail";
import Chat from "./components/Chat";
import ApprovalRail from "./components/ApprovalRail";
import EventTape from "./components/EventTape";
import ApprovalDrawer from "./components/ApprovalDrawer";
import MobileShell from "./components/MobileShell";
import { DashboardProvider, useDashboardData } from "./lib/data";
import { DrawerProvider } from "./lib/drawerContext";
import { useViewport } from "./lib/useViewport";
import "./App.css";

function DesktopShell() {
  const { bump } = useDashboardData();
  return (
    <div className="app">
      <StatusStrip />
      <div className="app-middle">
        <DataRail />
        <Chat onEvent={bump} />
        <ApprovalRail />
      </div>
      <EventTape />
      <ApprovalDrawer />
    </div>
  );
}

function Shell() {
  // Track 3 B6: switch to a phone-shaped layout below 720px. The
  // cockpit's column-dense layout doesn't fit a phone screen; the
  // mobile shell exposes approvals + chat + tape as tabs.
  const viewport = useViewport();
  return viewport === "mobile" ? <MobileShell /> : <DesktopShell />;
}

export default function App() {
  return (
    <DashboardProvider>
      <DrawerProvider>
        <Shell />
      </DrawerProvider>
    </DashboardProvider>
  );
}
