import { useEffect, useState } from "react";

/**
 * Track the layout breakpoint the cockpit should render against.
 *
 * Three modes:
 *  - `desktop` (≥ 1280px): full 3-rail Bloomberg-density cockpit.
 *  - `tablet`  (≥ 720px and < 1280px): drops the approval rail (existing
 *    @media in App.css already handles this server-side, but a hook
 *    field lets components react too if they want to).
 *  - `mobile`  (< 720px): Track 3 B6 phone-shaped companion. The
 *    cockpit's column-dense table layout doesn't translate; we switch
 *    to a tabbed approvals / chat / tape view instead.
 *
 * SSR-safe: returns `desktop` when `window` is undefined (the cockpit
 * doesn't SSR, but defensive in case a future build path does).
 */
export type Viewport = "desktop" | "tablet" | "mobile";

const MOBILE_BP = 720;
const TABLET_BP = 1280;

function compute(): Viewport {
  if (typeof window === "undefined") return "desktop";
  const w = window.innerWidth;
  if (w < MOBILE_BP) return "mobile";
  if (w < TABLET_BP) return "tablet";
  return "desktop";
}

export function useViewport(): Viewport {
  const [v, setV] = useState<Viewport>(compute);
  useEffect(() => {
    const onResize = () => setV(compute());
    window.addEventListener("resize", onResize);
    // Also fire once on mount in case `compute()` ran before the
    // browser settled on a width (rare but happens during the first
    // paint of a Tauri window restoring its previous size).
    onResize();
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return v;
}
