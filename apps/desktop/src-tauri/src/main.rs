#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod runtime;

use tauri::{Manager, RunEvent};

#[tauri::command]
fn runtime_status(state: tauri::State<'_, runtime::RuntimeState>) -> runtime::RuntimeStatus {
    state.status()
}

#[tauri::command]
fn repair_runtime_state(
    state: tauri::State<'_, runtime::RuntimeState>,
    app: tauri::AppHandle,
) -> runtime::RuntimeStatus {
    state.repair(&app)
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .manage(runtime::RuntimeState::default())
        .setup(|app| {
            app.state::<runtime::RuntimeState>()
                .initialize(&app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            runtime_status,
            repair_runtime_state
        ])
        .build(tauri::generate_context!())
        .expect("failed to build OpsMineFlow desktop app");
    app.run(|app, event| {
        if matches!(event, RunEvent::Exit) {
            app.state::<runtime::RuntimeState>().shutdown();
        }
    })
}
