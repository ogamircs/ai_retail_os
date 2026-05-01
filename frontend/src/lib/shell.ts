/**
 * Runtime detection for the Tauri shell vs the browser.
 *
 * The cockpit's frontend runs in three places:
 *  - the operator's browser (production demo path)
 *  - the Tauri 2.x desktop shell (`tauri:dev` / `tauri:build`)
 *  - the Tauri 2 mobile shell (planned, B6)
 *
 * Several features (biometric drawer-apply gate, native push
 * notifications, the always-on offline tape cache) only exist in the
 * shell. We detect once on import and expose `isTauri` / `runtime` so
 * consumers can pick the native path when available and fall back
 * gracefully in the browser.
 */

declare global {
  interface Window {
    // Tauri 2.x sets `window.__TAURI_INTERNALS__` on every WebView page
    // before user code runs. The legacy 1.x flag (`__TAURI__`) is also
    // checked in case an operator is on an older shell.
    __TAURI_INTERNALS__?: unknown;
    __TAURI__?: unknown;
  }
}

export function isTauri(): boolean {
  if (typeof window === "undefined") return false;
  return Boolean(window.__TAURI_INTERNALS__ || window.__TAURI__);
}

export type ShellRuntime = "browser" | "tauri";

export function runtime(): ShellRuntime {
  return isTauri() ? "tauri" : "browser";
}

/**
 * Best-effort dynamic import of a Tauri plugin. Returns `null` in the
 * browser; returns the imported module inside the shell. Wrapping the
 * import in a try/catch lets us ship a single bundle that works in both
 * contexts — Vite bundles `@tauri-apps/*` lazily and the import never
 * fires in the browser code path.
 */
export async function loadTauriModule<T>(loader: () => Promise<T>): Promise<T | null> {
  if (!isTauri()) return null;
  try {
    return await loader();
  } catch {
    // The plugin may be unregistered (e.g. capabilities not granted) or
    // the operator built without it — degrade quietly instead of
    // breaking the cockpit entirely.
    return null;
  }
}
