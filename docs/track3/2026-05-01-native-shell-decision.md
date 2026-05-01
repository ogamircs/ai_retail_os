# Track 3 — Native shell decision (B1 spike)

**Date:** 2026-05-01
**Outcome:** Adopt **Tauri 2.x** as the native shell. Desktop now (mac / win / linux); mobile via Tauri 2 mobile when the iOS build pipeline stabilizes for our stack.

## Context

Track 3 ships a native client that wraps the cockpit's Vite/React build with three pieces the browser can't do well:

1. OS-level push notifications when an action enters `approval_required`.
2. Biometric (Touch ID / Face ID / Windows Hello) gate on drawer apply.
3. Offline tape replay — last N events cached locally, served when the backend is unreachable.

Two paths considered.

## Path A — Tauri 2.x + the existing Vite build (chosen)

- **Shell:** Rust + WebKit (mac), WebView2 (win), WebKitGTK (linux). Bundle = WebView wrapping our Vite output.
- **Mobile:** Tauri 2 introduced iOS + Android; not yet as polished as the desktop story but viable for a phone-shaped operator companion (approvals + chat + tape).
- **Push:** `tauri-plugin-notification` on desktop (cross-platform native API). For mobile, APNs / FCM via `tauri-plugin-push` (later, when we have signing infra).
- **Biometric:** `tauri-plugin-biometric` (desktop + mobile in 2.x).
- **Updater:** `tauri-plugin-updater` (signed binaries with auto-update).
- **Bundle size:** ~3-8MB final binary (vs ~120MB Electron).

### Pros

- **Smallest delta from the web build.** The cockpit's existing 3-rail layout works as-is in the WebView; mobile gets a separate phone-shaped layout (B6) but reuses every component and every API call.
- **One frontend codebase.** Browser, desktop, mobile all consume the same SSE chat + JSON polls.
- **Native APIs without a JS bridge tax.** Tauri commands cross the boundary directly.
- **Bundle/cost.** Production bundle is ~30× smaller than Electron; mac/win/linux signed binaries from a single CI matrix.

### Cons

- **Rust toolchain required to build.** Operator needs rustup + cargo; CI needs the same matrix. Not a blocker but a real onboarding cost.
- **Mobile still maturing.** Tauri 2's mobile pipeline works but the iOS signing story is fiddly (Xcode + provisioning profile + APNs cert). We defer mobile push to B6 / B7 follow-up rather than ship something half-real.
- **WebView quirks.** Date/time inputs render differently across mac/win; absolute positioning has been flaky in WebKitGTK historically. We test all three on every release.

## Path B — React Native (Expo) re-skin (rejected)

- **Shell:** Native iOS + Android via React Native; desktop via RN Web or a separate Electron wrapper.
- **Push / biometric:** Excellent native support out of the box (Expo has both as managed config).
- **Mobile UX:** First-class native feel.

### Why we passed

- **Doubles the UI codebase.** The cockpit is a Bloomberg-terminal density UI; phones can't render it. RN means writing a phone-only layout from scratch *and* keeping the web cockpit running. Tauri lets us have the cockpit on desktop and a phone-shaped variant when mobile lands — same React tree, different layout switch.
- **Desktop story is weak.** RN-Windows / RN-macOS exist but neither is treated as a first-class target by the RN core team. Operators do their work on a desktop today; that's the path that has to feel native.
- **Build complexity.** Two native build pipelines (iOS + Android via RN) plus a third for desktop — vs a single Tauri matrix.

## Conclusion

Tauri 2.x. B2-B5 land in this PR (desktop scaffolding + push + biometric stub + offline tape + responsive companion layout). B6 mobile-shell + B7 signed-binary distribution are operator follow-ups once the Apple Developer + Microsoft signing certs are provisioned.

## Open follow-ups

- **CI matrix.** Track 3 follow-up adds `.github/workflows/tauri-bundle.yml` once the desktop scaffold is stabilised.
- **APNs / FCM.** Needs a server-side push gateway. Out of scope for the v1 cut; web-Notification API + Tauri desktop notifier covers the demo path.
- **iOS TestFlight.** Requires an Apple Developer account; defer until B6 has a working build.
- **Auto-updater.** Needs a signed update server (Tauri's updater plugin can read from any HTTPS endpoint that returns a JSON manifest). Defer to B7 once we know where binaries live.
