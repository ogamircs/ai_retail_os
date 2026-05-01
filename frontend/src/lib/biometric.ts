/**
 * Biometric drawer-apply gate (Track 3 B4).
 *
 * The cockpit's `apply → external` button can mutate real systems
 * (ERPNext Pricing Rules, Mautic campaigns, Akeneo PIM patches, …).
 * In the Tauri shell we gate the click behind Touch ID / Face ID /
 * Windows Hello via `@tauri-apps/plugin-biometric`. In the browser
 * (where no biometric API exists across vendors) we degrade to a
 * password-style confirm dialog so the operator still can't fat-finger
 * the apply.
 *
 * The wrapper returns one of three outcomes:
 *  - `verified`     — biometric (or fallback) confirmed; proceed.
 *  - `cancelled`    — operator dismissed the prompt; abort the apply.
 *  - `unsupported`  — neither biometric nor a usable fallback is
 *                     available; treat as `verified` so the cockpit
 *                     stays usable on environments that lack both
 *                     (CI, headless tests). Caller is free to harden
 *                     the flow further (e.g. require explicit operator
 *                     re-auth) by inspecting this value.
 */

import { isTauri, loadTauriModule } from "./shell";

export type BiometricOutcome = "verified" | "cancelled" | "unsupported";

export interface BiometricRequest {
  reason: string;
  /** Short title surfaced in the OS prompt (mac shows it above the app
   *  name; Windows Hello uses it as the prompt title). */
  title?: string;
  /** Whether to allow the device passcode as a fallback when no
   *  biometric enrolment exists. Defaults to true so the operator can
   *  always recover with their device PIN. */
  allowDeviceCredential?: boolean;
}

async function authenticateTauri(req: BiometricRequest): Promise<BiometricOutcome | null> {
  const mod = await loadTauriModule(() => import("@tauri-apps/plugin-biometric"));
  if (!mod) return null;
  try {
    const status = await mod.checkStatus();
    if (!status.isAvailable) {
      return "unsupported";
    }
    await mod.authenticate(req.reason, {
      title: req.title ?? "Confirm operator action",
      allowDeviceCredential: req.allowDeviceCredential ?? true,
    });
    return "verified";
  } catch (e: unknown) {
    // Tauri's plugin throws on cancel; treat any thrown error as a
    // cancel rather than risking an apply on a malformed reject.
    return "cancelled";
  }
}

function authenticateBrowser(req: BiometricRequest): BiometricOutcome {
  // No cross-vendor biometric API in the browser. The closest
  // available primitive is `WebAuthn` — but enrolling a credential is
  // a multi-step flow that doesn't suit a quick drawer apply. Fall
  // back to a confirm() dialog so the operator can't fat-finger.
  if (typeof window === "undefined" || typeof window.confirm !== "function") {
    return "unsupported";
  }
  const ok = window.confirm(
    `${req.reason}\n\nThe browser doesn't expose biometric APIs; this confirm dialog is the fallback. ` +
      "In the Tauri desktop shell, this prompt would be Touch ID / Face ID / Windows Hello.",
  );
  return ok ? "verified" : "cancelled";
}

/**
 * Gate an operator action behind biometric (or fallback) verification.
 *
 * Returns `verified` to proceed. Anything else means abort the apply.
 */
export async function requireBiometric(req: BiometricRequest): Promise<BiometricOutcome> {
  if (isTauri()) {
    const t = await authenticateTauri(req);
    if (t !== null) return t;
  }
  return authenticateBrowser(req);
}

/**
 * Cheap one-shot probe — does this runtime have a biometric path
 * available? Used by the drawer to decide whether to render the
 * "biometric ✓" hint next to the apply button.
 */
export async function biometricAvailable(): Promise<boolean> {
  if (!isTauri()) return false;
  const mod = await loadTauriModule(() => import("@tauri-apps/plugin-biometric"));
  if (!mod) return false;
  try {
    const status = await mod.checkStatus();
    return Boolean(status.isAvailable);
  } catch {
    return false;
  }
}
