# Track 3 B7 — Distribution runbook

Operator-facing build + signing path for the Tauri desktop shell. **Not** wired into CI yet; intentionally scaffold-only because signing certificates are private to the operator's account and shouldn't live in the repo.

## Prerequisites

```bash
# Rust toolchain (one time)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup default stable

# Tauri CLI (already wired into frontend/package.json devDependencies)
cd frontend && npm install
```

Verify the toolchain:

```bash
cd frontend
npx tauri info
# → expects "Tauri" + "Cargo" + "Node" sections all "OK"
```

## Local dev loop

```bash
cd frontend
npm run tauri:dev
# → builds the Vite output, launches the native window pointing at
#   http://localhost:5173 (the dev server is started automatically via
#   `beforeDevCommand` in tauri.conf.json).
```

Hot reload works for the React side; Rust changes to `src-tauri/src/` re-compile and the window restarts (~5-10s).

## Production bundle (unsigned)

```bash
cd frontend
npm run tauri:build
```

Outputs to `frontend/src-tauri/target/release/bundle/`:
- macOS: `.dmg` + `.app` bundle
- Windows: `.msi` + `.exe`
- Linux: `.deb` + `.AppImage`

Unsigned binaries trip Gatekeeper (mac) and SmartScreen (win) — operators must right-click → Open the first time. Fine for internal demo; not fine for distribution.

## Code signing (per platform)

### macOS

1. Apple Developer Program membership ($99/y).
2. Mint a **Developer ID Application** certificate in Apple's Developer Portal; download + double-click to install in Keychain.
3. Edit `frontend/src-tauri/tauri.conf.json`:
   ```json
   "macOS": {
     "signingIdentity": "Developer ID Application: Your Name (TEAMID)",
     "providerShortName": "TEAMID",
     "entitlements": null
   }
   ```
4. (optional) Notarisation — set `APPLE_ID`, `APPLE_PASSWORD` (app-specific), `APPLE_TEAM_ID` env vars and `tauri build` invokes `notarytool` automatically.

### Windows

1. Acquire a code-signing certificate (DigiCert / Sectigo / Comodo, ~$300/y).
2. Install the cert to the Windows certificate store; note its SHA-1 thumbprint.
3. Update `tauri.conf.json`:
   ```json
   "windows": {
     "certificateThumbprint": "YOUR_THUMBPRINT_HEX",
     "digestAlgorithm": "sha256",
     "timestampUrl": "http://timestamp.digicert.com"
   }
   ```

### Linux

`.deb` and `.AppImage` are unsigned; that's the convention. Distribute via your apt repo / direct download with a SHA-256 published alongside.

## Auto-updater

Tauri's updater plugin reads a JSON manifest from `endpoints[]` in `tauri.conf.json` (currently set to `https://updates.ai-retail-os.example.com/...`, a placeholder — replace with the real CDN before flipping `plugins.updater.active` to `true`).

The manifest format (per architecture):

```json
{
  "version": "0.2.0",
  "notes": "Track 3 follow-up: signed binaries.",
  "pub_date": "2026-06-01T00:00:00Z",
  "platforms": {
    "darwin-aarch64": {
      "signature": "<base64-encoded-tauri-sig>",
      "url": "https://updates.ai-retail-os.example.com/AI%20Retail%20OS_0.2.0_aarch64.dmg"
    },
    "windows-x86_64": {
      "signature": "...",
      "url": "..."
    }
  }
}
```

Sign updates with a Tauri-managed Ed25519 keypair:

```bash
cd frontend/src-tauri
npx tauri signer generate -w ~/.tauri/private.key
# → prints the public key — paste into tauri.conf.json under
#   plugins.updater.pubkey before bundling.
# Sign each release artifact:
npx tauri signer sign -k ~/.tauri/private.key path/to/AI\ Retail\ OS_0.2.0_aarch64.dmg
```

Keep the private key out of CI checkouts; either inject via secrets at sign time, or run the signer step on a release-controller box that owns the key.

## CI matrix (planned, not landed)

```yaml
# .github/workflows/tauri-bundle.yml — TODO
matrix:
  include:
    - { os: macos-14,  target: aarch64-apple-darwin }
    - { os: macos-13,  target: x86_64-apple-darwin }
    - { os: ubuntu-22, target: x86_64-unknown-linux-gnu }
    - { os: windows-2022, target: x86_64-pc-windows-msvc }
```

Once signing infra is in place, this workflow runs `npm install && npm run tauri:build`, signs the artifacts using secrets, uploads to a GitHub Release, and publishes the updater manifest to the static endpoint in `tauri.conf.json`.

## Mobile (B6 follow-up)

Tauri 2 mobile pipeline lands once we have:
- Apple Developer + APNs cert (for iOS push)
- Google Play Console account (for Android Play submissions)
- Firebase / FCM project (for Android push)

Build commands for when those are in place:

```bash
npx tauri ios init
npx tauri ios dev
npx tauri ios build

npx tauri android init
npx tauri android dev
npx tauri android build
```

## Reset path

```bash
cd frontend/src-tauri
cargo clean
rm -rf target/
cd .. && npm run tauri:build
```
