fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["runtime_status", "repair_runtime_state"]),
    ))
    .expect("failed to build Tauri capabilities");
}
