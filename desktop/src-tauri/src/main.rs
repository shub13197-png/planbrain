// Planning Brain desktop shell.
//
// Owns exactly one thing: the lifetime of the Python sidecar and the stdio pipe
// to it. No planning logic lives here and none should -- the engines are tested
// in Python, and re-deriving any of them in Rust would be re-deriving verified
// arithmetic for no gain.
//
// stdio rather than a localhost port, because the backend blocks every AF_INET
// socket at startup and loopback is still AF_INET. See docs/packaging.md.
//
// **Why the backend is a resource rather than a Tauri `externalBin`.**
// `externalBin` ships a single binary. Our PyInstaller build is a one-dir tree
// -- an executable plus an `_internal` directory of 889 files, because numpy and
// scipy carry native libraries that have to sit beside it. The first honest
// check of "is this installable?" found that mismatch; it would have failed on
// the first bundle. So the whole tree ships under `resources` and is spawned by
// path.
//
// It is spawned with `std::process` rather than the shell plugin. We are not
// running user-supplied commands, we are running one binary we shipped at a
// path we computed, and the shell plugin would add a scope to configure, a
// permission to grant and a dependency to audit for no benefit.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use tauri::path::BaseDirectory;
use tauri::{Emitter, Manager, State};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

/// Keeps the console window from flashing on Windows. The backend is a console
/// application because it *is* the stdio pipe; without this the user sees a
/// black window appear and vanish on every launch.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct Backend {
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
    /// Lines the backend produced before the interface was listening.
    ///
    /// **Every launch used to lose the first one.** `setup()` spawns the
    /// backend and starts this reader immediately; the webview then loads
    /// `index.html` and only then runs the module script that calls
    /// `listen("backend", ...)`. A Tauri event goes to the listeners that exist
    /// when it is emitted, so the backend's opening line -- the `ready` frame
    /// carrying the offline state -- was emitted into an empty room and
    /// dropped. Every time, on every platform.
    ///
    /// The visible result was a status bar reading "starting..." forever and a
    /// profile list that never loaded. It survived every cross-file check we
    /// have because nothing here is wrong in a file; it is wrong in the order
    /// two things happen. It took launching the application to see it.
    ///
    /// Buffering rather than re-sending, because the general fault is worse
    /// than the symptom: a backend that dies during startup emits its error
    /// early too, and that line vanishing leaves a user staring at
    /// "starting..." with the one message that explains it already discarded.
    early: Arc<Mutex<Vec<(String, String)>>>,
    /// Set by `drain_backend` once the interface has its listeners attached.
    /// Before it, lines are buffered and not emitted; after it, emitted and not
    /// buffered -- so no line is delivered twice or lost.
    listening: Arc<AtomicBool>,
}

/// Send one JSON-RPC line to the backend.
///
/// Replies arrive asynchronously on the `backend` event and the frontend
/// correlates them by `id`, so this returns as soon as the line is written.
#[tauri::command]
fn call_backend(state: State<'_, Backend>, request: String) -> Result<(), String> {
    let mut guard = state.stdin.lock().map_err(|e| e.to_string())?;
    let stdin = guard.as_mut().ok_or("the backend is not running")?;
    writeln!(stdin, "{}", request).map_err(|e| e.to_string())?;
    // Without the flush the backend blocks waiting for a line that is sitting
    // in this process's buffer, and the pair deadlocks with no error on either
    // side.
    stdin.flush().map_err(|e| e.to_string())
}

/// Hand the interface everything the backend said before it was listening.
///
/// Called once by the frontend immediately after it registers its listeners.
/// Returns `(event, line)` pairs in the order they arrived, and flips the
/// backend over to emitting normally.
///
/// A command rather than a timer: "wait 200ms and hope" is the version of this
/// that works on the developer's machine and fails on a cold start, which is
/// exactly the machine this product is for.
#[tauri::command]
fn drain_backend(state: State<'_, Backend>) -> Result<Vec<(String, String)>, String> {
    let mut buffered = state.early.lock().map_err(|e| e.to_string())?;
    // Ordered: mark listening only while holding the lock, so a line arriving
    // on the reader thread either lands in this drain or is emitted, never
    // both and never neither.
    state.listening.store(true, Ordering::SeqCst);
    Ok(std::mem::take(&mut *buffered))
}

/// Where the backend lands inside the installed bundle.
///
/// A `resources` entry keeps its path relative to `tauri.conf.json`, so the
/// glob `binaries/planbrain-backend/**/*` installs to `binaries/...` under the
/// resource directory and the `binaries/` prefix belongs here too. Dropping it
/// is the easy mistake: the shell would compile, install, and then fail at
/// launch with a missing backend. `tests/test_desktop_shell.py` asserts this
/// path against the glob in the config so the two cannot drift.
const BACKEND_DIR: &str = "binaries/planbrain-backend";

fn backend_executable(app: &tauri::App) -> Result<std::path::PathBuf, String> {
    let name = if cfg!(windows) {
        format!("{BACKEND_DIR}/planbrain-backend.exe")
    } else {
        format!("{BACKEND_DIR}/planbrain-backend")
    };
    app.path()
        .resolve(name, BaseDirectory::Resource)
        .map_err(|e| format!("could not locate the bundled backend: {e}"))
}

/// Emit a line, or hold it until the interface is listening.
///
/// One function for both reader threads and every event name, so a new stream
/// cannot be added that quietly reintroduces the dropped-first-line bug.
fn deliver(
    handle: &tauri::AppHandle,
    early: &Arc<Mutex<Vec<(String, String)>>>,
    listening: &Arc<AtomicBool>,
    event: &str,
    line: String,
) {
    // The lock is taken first and the flag read inside it, matching the order
    // in `drain_backend`. Reading the flag outside would let a line be buffered
    // after the drain has already emptied the buffer, where nothing would ever
    // collect it.
    let mut buffered = match early.lock() {
        Ok(guard) => guard,
        Err(_) => return,
    };
    if listening.load(Ordering::SeqCst) {
        drop(buffered);
        let _ = handle.emit(event, line);
    } else {
        buffered.push((event.to_string(), line));
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let exe = backend_executable(app)?;
            if !exe.exists() {
                // Naming the path matters: the usual cause is a build that
                // packaged the shell without running the PyInstaller step.
                return Err(format!(
                    "the backend is missing from this build: {}",
                    exe.display()
                )
                .into());
            }

            let mut command = Command::new(&exe);
            command
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            #[cfg(windows)]
            command.creation_flags(CREATE_NO_WINDOW);

            let mut child = command.spawn()?;
            let stdin = child.stdin.take();
            let stdout = child.stdout.take();
            let stderr = child.stderr.take();

            // Shared with both reader threads, which start before the webview
            // has finished loading and therefore before anything is listening.
            let early = Arc::new(Mutex::new(Vec::new()));
            let listening = Arc::new(AtomicBool::new(false));

            if let Some(stdout) = stdout {
                let handle = app.handle().clone();
                let early = early.clone();
                let listening = listening.clone();
                std::thread::spawn(move || {
                    for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                        deliver(&handle, &early, &listening, "backend", line);
                    }
                    // The loop ends when the pipe closes, which means the
                    // backend exited. Say so rather than leaving the interface
                    // waiting for a reply that will never come.
                    deliver(&handle, &early, &listening, "backend-exit",
                            "the backend stopped".into());
                });
            }

            if let Some(stderr) = stderr {
                let handle = app.handle().clone();
                let early = early.clone();
                let listening = listening.clone();
                std::thread::spawn(move || {
                    for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                        // The backend writes only JSON to stdout, so anything
                        // on stderr is a real fault worth surfacing rather than
                        // discarding into a log nobody opens.
                        deliver(&handle, &early, &listening, "backend-error", line);
                    }
                });
            }

            app.manage(Backend {
                stdin: Mutex::new(stdin),
                child: Mutex::new(Some(child)),
                early,
                listening,
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                // Without this the backend outlives the window and lingers as
                // an orphan process holding the database file open.
                if let Some(state) = window.app_handle().try_state::<Backend>() {
                    if let Ok(mut guard) = state.child.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![call_backend, drain_backend])
        .run(tauri::generate_context!())
        .expect("error while running Planning Brain");
}
