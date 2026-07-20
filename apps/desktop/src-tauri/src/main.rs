#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod runtime;

use tauri::{Manager, RunEvent};

#[tauri::command]
async fn runtime_status(
    state: tauri::State<'_, runtime::RuntimeState>,
) -> Result<runtime::RuntimeStatus, String> {
    let runtime = state.inner().clone();
    Ok(runtime.status_async().await)
}

#[tauri::command]
async fn repair_runtime_state(
    state: tauri::State<'_, runtime::RuntimeState>,
    app: tauri::AppHandle,
) -> Result<runtime::RuntimeStatus, String> {
    let runtime = state.inner().clone();
    Ok(runtime.repair_async(app).await)
}

#[tauri::command]
async fn local_api_operation(
    state: tauri::State<'_, runtime::RuntimeState>,
    operation: String,
    payload: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    state.proxy_operation_async(operation, payload).await
}

#[tauri::command]
async fn delete_local_data(
    state: tauri::State<'_, runtime::RuntimeState>,
    app: tauri::AppHandle,
) -> Result<serde_json::Value, String> {
    state.delete_data_with_native_confirmation(app).await
}

#[tauri::command]
async fn choose_import_file(
    state: tauri::State<'_, runtime::RuntimeState>,
    app: tauri::AppHandle,
    format: String,
) -> Result<runtime::SelectedFile, String> {
    state.choose_import_file(app, format).await
}

#[tauri::command]
async fn preview_selected_import(
    state: tauri::State<'_, runtime::RuntimeState>,
    handle: String,
    payload: serde_json::Value,
) -> Result<serde_json::Value, String> {
    state.preview_selected_import(handle, payload).await
}

#[tauri::command]
async fn import_selected_file(
    state: tauri::State<'_, runtime::RuntimeState>,
    handle: String,
    payload: serde_json::Value,
) -> Result<serde_json::Value, String> {
    state.import_selected_file(handle, payload).await
}

#[tauri::command]
async fn save_export_with_dialog(
    state: tauri::State<'_, runtime::RuntimeState>,
    app: tauri::AppHandle,
    format: String,
) -> Result<serde_json::Value, String> {
    state.save_export_with_dialog(app, format).await
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .manage(runtime::RuntimeState::default())
        .setup(|app| {
            app.state::<runtime::RuntimeState>()
                .initialize(&app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            runtime_status,
            repair_runtime_state,
            local_api_operation,
            delete_local_data,
            choose_import_file,
            preview_selected_import,
            import_selected_file,
            save_export_with_dialog
        ])
        .build(tauri::generate_context!())
        .expect("failed to build OpsMineFlow desktop app");
    app.run(|app, event| {
        if matches!(event, RunEvent::Exit) {
            app.state::<runtime::RuntimeState>().shutdown();
        }
    })
}
