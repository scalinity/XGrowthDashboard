//! Tauri v2 shell for the X Growth Dashboard native app (spec §31.2).
//!
//! Responsibilities:
//!   1. Spawn the Python FastAPI sidecar as a child process.
//!      - dev (debug): `uv run python -m app.service`, cwd = repo root.
//!      - release: the bundled frozen sidecar binary in the app's resource dir
//!        (produced by PyInstaller in Phase 11.12).
//!   2. Parse the sidecar's two-line stdout handshake — `XGROWTH_PORT=<n>` and
//!      `XGROWTH_TOKEN=<t>` — and store it so the frontend can reach the API on
//!      127.0.0.1:<port> with `Authorization: Bearer <token>`.
//!   3. Expose `get_sidecar_info` to the frontend and emit `sidecar://ready`.
//!   4. Kill the sidecar when the app exits (no orphaned Python).

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Emitter, Manager, RunEvent};

const PORT_PREFIX: &str = "XGROWTH_PORT=";
const TOKEN_PREFIX: &str = "XGROWTH_TOKEN=";
/// Name of the bundled frozen sidecar binary (Phase 11.12 / PyInstaller).
const SIDECAR_BIN: &str = "xgrowth-sidecar";

#[derive(Default, Clone, Serialize)]
pub struct SidecarInfo {
    pub port: Option<u16>,
    pub token: Option<String>,
    pub ready: bool,
}

#[derive(Default)]
struct SidecarState {
    info: Mutex<SidecarInfo>,
    child: Mutex<Option<Child>>,
}

/// Frontend calls this to learn where to reach the API and which token to send.
#[tauri::command]
fn get_sidecar_info(state: tauri::State<'_, SidecarState>) -> SidecarInfo {
    state.info.lock().unwrap().clone()
}

fn repo_root() -> PathBuf {
    // CARGO_MANIFEST_DIR = <repo>/desktop/src-tauri at compile time.
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
}

fn build_sidecar_command(app: &tauri::App) -> Command {
    if cfg!(debug_assertions) {
        // Dev: run the sidecar from the repo via uv (inherits the dev shell PATH).
        let mut cmd = Command::new("uv");
        cmd.args(["run", "python", "-m", "app.service"])
            .current_dir(repo_root());
        cmd
    } else {
        // Release: the PyInstaller-frozen binary shipped in Contents/Resources
        // (Phase 11.12). Fall back to a bare name on PATH if not yet bundled.
        let resource = app
            .path()
            .resource_dir()
            .ok()
            .map(|d| d.join(SIDECAR_BIN));
        match resource {
            Some(path) if path.exists() => Command::new(path),
            _ => Command::new(SIDECAR_BIN),
        }
    }
}

fn spawn_sidecar(app: &tauri::App) {
    let mut cmd = build_sidecar_command(app);
    cmd.stdout(Stdio::piped()).stderr(Stdio::inherit());

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            log::error!("failed to spawn Python sidecar: {e}");
            return;
        }
    };

    let stdout = child.stdout.take().expect("piped stdout");
    let handle = app.handle().clone();

    // Reader thread: parse the handshake, then keep draining so the pipe never
    // fills (uvicorn logs at warning level — minimal).
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().map_while(Result::ok) {
            if let Some(rest) = line.strip_prefix(PORT_PREFIX) {
                if let Ok(port) = rest.trim().parse::<u16>() {
                    let state = handle.state::<SidecarState>();
                    let mut info = state.info.lock().unwrap();
                    info.port = Some(port);
                    finalize(&handle, &mut info);
                }
            } else if let Some(rest) = line.strip_prefix(TOKEN_PREFIX) {
                let state = handle.state::<SidecarState>();
                let mut info = state.info.lock().unwrap();
                info.token = Some(rest.trim().to_string());
                finalize(&handle, &mut info);
            } else {
                log::info!("[sidecar] {line}");
            }
        }
        log::warn!("sidecar stdout closed");
    });

    app.state::<SidecarState>()
        .child
        .lock()
        .unwrap()
        .replace(child);
}

/// When both port + token are known, mark ready once and notify the frontend.
fn finalize(handle: &tauri::AppHandle, info: &mut SidecarInfo) {
    if !info.ready && info.port.is_some() && info.token.is_some() {
        info.ready = true;
        let _ = handle.emit("sidecar://ready", info.clone());
        log::info!("sidecar ready on 127.0.0.1:{:?}", info.port);
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(SidecarState::default())
        .invoke_handler(tauri::generate_handler![get_sidecar_info])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            spawn_sidecar(app);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                // Kill the sidecar so no orphaned Python survives the app.
                if let Some(mut child) = app_handle
                    .state::<SidecarState>()
                    .child
                    .lock()
                    .unwrap()
                    .take()
                {
                    let _ = child.kill();
                }
            }
        });
}
