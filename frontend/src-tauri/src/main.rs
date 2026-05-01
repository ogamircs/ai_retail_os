// Prevents the default console window on Windows release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    ai_retail_os_shell_lib::run()
}
