import { ReactNode, createContext, useContext, useState } from "react";
import type { ActionItem, ExternalAction, SpineEvent } from "./api";

export type DrawerContent =
  | { kind: "approval"; action: ActionItem; external?: ExternalAction }
  | { kind: "artifact"; artifactId: string }
  | { kind: "event-tape"; events: SpineEvent[] }
  | null;

type DrawerCtx = {
  drawer: DrawerContent;
  open: (c: DrawerContent) => void;
  close: () => void;
};

const Ctx = createContext<DrawerCtx | null>(null);

export function DrawerProvider({ children }: { children: ReactNode }) {
  const [drawer, setDrawer] = useState<DrawerContent>(null);
  return (
    <Ctx.Provider value={{ drawer, open: setDrawer, close: () => setDrawer(null) }}>
      {children}
    </Ctx.Provider>
  );
}

export function useDrawer() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useDrawer must be used inside DrawerProvider");
  return v;
}
