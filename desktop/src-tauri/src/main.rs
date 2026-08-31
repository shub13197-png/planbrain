// Planning Brain desktop shell.
//
// Owns one thing: the lifetime of the Python sidecar, and the stdio pipe to it.
// No planning logic lives here and none should -- the engines are tested in
// Python and re-deriving any of them in Rust would be re-deriving verified
// arithmetic for no gain.
//
// stdio rather than a localhost port, because the sidecar blocks every AF_INET
// socket at startup and loopback is still AF_INET. See docs/packaging.md.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;
use tauri::{Manager, State};
use tauri_plugin_shell::process::{CommandEvent, CommandChild};
use tauri_plugin_shell::ShellExt;

struct Backend {
    child: Mutex<Option<CommandChild>>,
}

/// Send one JSON-RPC line to the sidecar. Replies arrive on the `backend`
/// event; the frontend correlates them by `id`.
#[tauri::command]
async fn call_backend(state: State<'_, Backend>, request: String) -> Result<(), String> {
    let mut guard = state.child.lock().map_err(|e| e.to_string())?;
    let child = guard.as_mut().ok_or("backend is not running")?;
    child
        .write(format!("{}
", request).as_bytes())
        .map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let (mut rx, child) = app
                .shell()
                .sidecar("planbrain-backend")
                .expect("sidecar not bundled -- run the PyInstaller build first")
                .spawn()
                .expect("failed to start the backend");

            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            let text = String::from_utf8_lossy(&line).to_string();
                            let _ = handle.emit("backend", text);
                        }
                        // The sidecar writes only JSON to stdout, so anything on
                        // stderr is a real fault worth surfacing rather than
                        // discarding into a log nobody opens.
                        CommandEvent::Stderr(line) => {
                            let text = String::from_utf8_lossy(&line).to_string();
                            let _ = handle.emit("backend-error", text);
                        }
                        CommandEvent::Terminated(status) => {
                            let _ = handle.emit("backend-exit", format!("{:?}", status));
                        }
                        _ => {}
                    }
                }
            });

            app.manage(Backend { child: Mutex::new(Some(child)) });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![call_backend])
        .run(tauri::generate_context!())
        .expect("error while running Planning Brain");
}
