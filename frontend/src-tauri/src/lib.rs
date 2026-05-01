//! AI Retail OS — Tauri 2.x shell.
//!
//! Wraps the Vite cockpit build in a native window with three plugins
//! relevant to operator workflow: notification (B3), biometric (B4),
//! updater (B7). The shell itself is intentionally thin — all UI lives
//! in the React build; Rust only orchestrates plugin registration and
//! a couple of host commands the WebView calls into.

use serde::Serialize;
use tauri::Manager;

#[derive(Serialize)]
struct ShellInfo {
    /// Always "tauri" inside the native shell. The cockpit's frontend
    /// looks for this to decide whether to attempt biometric / native
    /// notifications via the Tauri plugin APIs vs the web fallbacks.
    runtime: &'static str,
    version: &'static str,
    target_os: &'static str,
}

/// Cheap runtime identification — the cockpit frontend calls this on
/// startup so it can light up shell-only features (biometric gate on
/// drawer apply, native push notifier, offline tape replay's
/// always-on cache). In the browser, this command is unavailable and
/// the cockpit falls back to web APIs.
#[tauri::command]
fn shell_info() -> ShellInfo {
    ShellInfo {
        runtime: "tauri",
        version: env!("CARGO_PKG_VERSION"),
        target_os: std::env::consts::OS,
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_biometric::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![shell_info])
        .setup(|app| {
            // Surface a startup log so operators can confirm plugins
            // initialised on first run. The cockpit doesn't depend on
            // this for behaviour — purely diagnostic.
            let _ = app.get_webview_window("main");
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
