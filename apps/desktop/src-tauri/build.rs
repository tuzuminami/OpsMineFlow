fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "runtime_status",
            "repair_runtime_state",
            "local_api_operation",
            "delete_local_data",
            "choose_import_file",
            "preview_selected_import",
            "import_selected_file",
            "save_export_with_dialog",
        ]),
    ))
    .expect("failed to build Tauri capabilities");
}
