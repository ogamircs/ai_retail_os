/**
 * Approval push notifier (Track 3 B3).
 *
 * Watches the operator's pending-approval queue and fires an OS-level
 * notification when a new `approval_required` (or pending external)
 * action arrives. Two delivery paths:
 *
 *  - **Tauri shell** — uses `@tauri-apps/plugin-notification` so the
 *    notification fires through the platform's native notifier (mac
 *    Notification Center, Windows action centre, linux libnotify).
 *  - **Browser** — falls back to the standard `window.Notification` API.
 *    Same UX inside Chrome/Firefox/Safari, no permission prompt until
 *    the operator clicks the chip; if they decline, we degrade to
 *    cockpit-only chips silently.
 *
 * Idempotency: each (action_id, system_id) pair is notified at most
 * once per session. The cockpit reloads the queue every 5s; without
 * this dedup the operator would get 12 pings/minute for the same row.
 */

import type { ActionItem } from "./api";
import { isTauri, loadTauriModule } from "./shell";

type ExternalAction = NonNullable<ActionItem["external_actions"]>[number];

const PENDING_INTERNAL = new Set(["approval_required"]);
const PENDING_EXTERNAL = new Set(["approval_required", "mock_only", "proposed"]);

const SEEN: Set<string> = new Set();
let _permission: NotificationPermission | "unknown" = "unknown";

function actionKey(a: ActionItem, ex?: ExternalAction): string {
  return ex
    ? `ext::${ex.system_id}::${ex.id}`
    : `int::${a.id ?? a.title}::${a.status}`;
}

function pendingExternal(a: ActionItem): ExternalAction | undefined {
  return (a.external_actions ?? []).find((x) => PENDING_EXTERNAL.has(x.status));
}

async function ensureBrowserPermission(): Promise<NotificationPermission> {
  if (typeof Notification === "undefined") return "denied";
  if (Notification.permission !== "default") return Notification.permission;
  try {
    return await Notification.requestPermission();
  } catch {
    return "denied";
  }
}

async function fireTauri(title: string, body: string): Promise<boolean> {
  const mod = await loadTauriModule(() => import("@tauri-apps/plugin-notification"));
  if (!mod) return false;
  try {
    let granted = await mod.isPermissionGranted();
    if (!granted) {
      const r = await mod.requestPermission();
      granted = r === "granted";
    }
    if (!granted) return false;
    await mod.sendNotification({ title, body });
    return true;
  } catch {
    return false;
  }
}

function fireBrowser(title: string, body: string): boolean {
  if (typeof Notification === "undefined") return false;
  if (Notification.permission !== "granted") return false;
  try {
    new Notification(title, { body });
    return true;
  } catch {
    return false;
  }
}

async function fireOne(title: string, body: string): Promise<boolean> {
  if (isTauri()) {
    const ok = await fireTauri(title, body);
    if (ok) return true;
  }
  return fireBrowser(title, body);
}

/**
 * One-shot consent prompt — call this from an operator-initiated event
 * (button click, keyboard shortcut). Browsers refuse to show the
 * permission prompt outside a user gesture, so the notifier never
 * prompts on its own.
 */
export async function requestApprovalPushPermission(): Promise<NotificationPermission> {
  if (isTauri()) {
    const mod = await loadTauriModule(() => import("@tauri-apps/plugin-notification"));
    if (mod) {
      try {
        const granted = await mod.isPermissionGranted();
        if (granted) {
          _permission = "granted";
          return "granted";
        }
        const r = await mod.requestPermission();
        _permission = r === "granted" ? "granted" : "denied";
        return _permission as NotificationPermission;
      } catch {
        _permission = "denied";
        return "denied";
      }
    }
  }
  const r = await ensureBrowserPermission();
  _permission = r;
  return r;
}

/**
 * Drop-in hook helper — call on every dashboard refresh with the
 * current actions array. The notifier dedups via the SEEN cache so
 * passing the same array twice is a no-op.
 *
 * Returns the count of notifications actually fired this call (0 in
 * the browser when the operator hasn't granted permission yet).
 */
export async function notifyPendingApprovals(actions: ActionItem[]): Promise<number> {
  if (typeof window === "undefined") return 0;
  // Skip silently when we know permission is denied — avoid recomputing
  // the queue on every refresh. Guard against runtimes where
  // `Notification` itself is undeclared (some embedded WebViews,
  // headless test environments) — `Notification?.permission` would
  // raise ReferenceError on the bare identifier rather than yielding
  // undefined, turning a routine poll into an unhandled rejection.
  if (
    !isTauri() &&
    typeof Notification !== "undefined" &&
    Notification.permission === "denied"
  ) {
    return 0;
  }

  let fired = 0;
  for (const a of actions) {
    if (PENDING_INTERNAL.has(a.status)) {
      const k = actionKey(a);
      if (!SEEN.has(k)) {
        SEEN.add(k);
        const ok = await fireOne(
          `Pending approval: ${a.title || "Untitled action"}`,
          `${a.action_type || "action"} from ${a.owner || "an agent"}`,
        );
        if (ok) fired++;
      }
    }
    const ex = pendingExternal(a);
    if (ex) {
      const k = actionKey(a, ex);
      if (!SEEN.has(k)) {
        SEEN.add(k);
        const ok = await fireOne(
          `External pending: ${a.title || "Untitled action"}`,
          `${ex.system_id} · ${ex.external_domain ?? "?"} — ${ex.status}`,
        );
        if (ok) fired++;
      }
    }
  }
  return fired;
}

/** Drain the dedup set — useful for tests, and for an operator-driven
 *  "re-notify me about everything" flow if we ever add it to the UI.
 */
export function resetSeenForTests(): void {
  SEEN.clear();
}

export function lastKnownPermission(): NotificationPermission | "unknown" {
  return _permission;
}
