import StatusStrip from "./components/StatusStrip";
import DataRail from "./components/DataRail";
import Chat from "./components/Chat";
import ApprovalRail from "./components/ApprovalRail";
import EventTape from "./components/EventTape";
import ApprovalDrawer from "./components/ApprovalDrawer";
import { DashboardProvider, useDashboardData } from "./lib/data";
import { DrawerProvider } from "./lib/drawerContext";
import "./App.css";

function Shell() {
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

export default function App() {
  return (
    <DashboardProvider>
      <DrawerProvider>
        <Shell />
      </DrawerProvider>
    </DashboardProvider>
  );
}
