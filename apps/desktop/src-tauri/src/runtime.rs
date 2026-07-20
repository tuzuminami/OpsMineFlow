use std::collections::HashMap;
use std::ffi::{CString, OsString};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

const LOCAL_API_HOST: &str = "127.0.0.1";
const LOCAL_API_PORT: u16 = 8765;
const READY_TIMEOUT: Duration = Duration::from_secs(5);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(3);
const ORPHAN_SELF_EXIT_TIMEOUT: Duration = Duration::from_secs(5);
const HEALTH_FAILURE_THRESHOLD: u8 = 3;
const API_PROXY_TIMEOUT: Duration = Duration::from_secs(3);
const FILE_TRANSFER_TIMEOUT: Duration = Duration::from_secs(20);
const MAX_API_PROXY_BODY_BYTES: usize = 1_048_576;
const MAX_API_PROXY_RESPONSE_BYTES: usize = 4_194_304;
const API_SESSION_HEADER: &str = "X-OpsMineFlow-Api-Session";
const DELETE_CHALLENGE_HEADER: &str = "X-OpsMineFlow-Delete-Challenge";
const PROJECT_HEADER: &str = "X-OpsMineFlow-Project";
const RUNTIME_PROBE_CHALLENGE_HEADER: &str = "X-OpsMineFlow-Runtime-Probe-Challenge";
const MAX_API_PROXY_HEADER_BYTES: usize = 16_384;
const FILE_SCOPE_TTL: Duration = Duration::from_secs(300);

#[derive(Clone, Serialize)]
pub struct RuntimeStatus {
    pub state: String,
    pub endpoint: String,
    pub recovery_action: String,
}

impl RuntimeStatus {
    fn ready() -> Self {
        Self {
            state: "ready".to_owned(),
            endpoint: endpoint().to_string(),
            recovery_action: "none".to_owned(),
        }
    }

    fn unavailable(recovery_action: &str) -> Self {
        Self {
            state: "unavailable".to_owned(),
            endpoint: endpoint().to_string(),
            recovery_action: recovery_action.to_owned(),
        }
    }

    fn port_collision() -> Self {
        Self {
            state: "port_collision".to_owned(),
            endpoint: endpoint().to_string(),
            recovery_action: "close_conflicting_app".to_owned(),
        }
    }

    fn stopped() -> Self {
        Self {
            state: "stopped".to_owned(),
            endpoint: endpoint().to_string(),
            recovery_action: "restart".to_owned(),
        }
    }
}

#[derive(Clone)]
pub struct RuntimeState {
    inner: Arc<Mutex<RuntimeInner>>,
}

struct RuntimeInner {
    child: Option<Child>,
    paths: Option<RuntimePaths>,
    endpoint: SocketAddr,
    runtime_nonce: Option<String>,
    session_secret: Option<SessionSecret>,
    runtime_probe_secret: Option<RuntimeProbeSecret>,
    file_scopes: HashMap<String, FileScope>,
    health_failure_count: u8,
    repair_in_progress: bool,
    status: RuntimeStatus,
}

struct SessionSecret(String);

struct RuntimeProbeSecret(String);

#[derive(Clone)]
struct FileScope {
    snapshot_path: PathBuf,
    format: String,
    byte_len: u64,
    sha256: String,
    expires_at: Instant,
}

#[derive(Serialize)]
pub struct SelectedFile {
    pub handle: String,
    pub display_name: String,
}

#[derive(Clone)]
struct RuntimePaths {
    log_dir: PathBuf,
    data_dir: PathBuf,
    import_staging_dir: PathBuf,
    export_staging_dir: PathBuf,
    marker_path: PathBuf,
}

struct ExportTarget {
    directory: File,
    filename: OsString,
    exists: bool,
}

enum DirectoryEntryKind {
    RegularFile,
    Other,
}

#[derive(Serialize, Deserialize)]
struct RuntimeMarker {
    pid: u32,
    nonce: String,
}

struct SidecarCommand {
    program: PathBuf,
    pythonpath: Option<String>,
    is_development: bool,
}

#[derive(Deserialize)]
struct HealthPayload {
    status: String,
    bind: String,
    local_only: bool,
    #[serde(default)]
    runtime: Option<RuntimeIdentity>,
}

#[derive(Deserialize)]
struct RuntimeIdentity {
    nonce: String,
    pid: u32,
    proof: String,
}

impl Default for RuntimeState {
    fn default() -> Self {
        Self {
            inner: Arc::new(Mutex::new(RuntimeInner {
                child: None,
                paths: None,
                endpoint: endpoint(),
                runtime_nonce: None,
                session_secret: None,
                runtime_probe_secret: None,
                file_scopes: HashMap::new(),
                health_failure_count: 0,
                repair_in_progress: false,
                status: RuntimeStatus::stopped(),
            })),
        }
    }
}

impl RuntimeState {
    pub fn initialize(&self, app: &AppHandle) {
        let paths = match RuntimePaths::prepare(app) {
            Ok(paths) => paths,
            Err(_) => {
                self.set_unavailable_without_paths("restart");
                return;
            }
        };
        if recover_owned_orphan(&paths).is_err() {
            self.set_unavailable(paths, "repair_runtime_state");
            return;
        }

        let sidecar = match resolve_sidecar(app) {
            Ok(sidecar) => sidecar,
            Err(recovery_action) => {
                self.set_unavailable(paths, &recovery_action);
                return;
            }
        };

        if endpoint_is_open() {
            if let Ok(mut inner) = self.inner.lock() {
                inner.paths = Some(paths);
                inner.runtime_nonce = None;
                inner.session_secret = None;
                inner.runtime_probe_secret = None;
                inner.health_failure_count = 0;
                inner.status = RuntimeStatus::port_collision();
            }
            return;
        }

        let nonce = match random_hex(32) {
            Ok(nonce) => nonce,
            Err(_) => {
                self.set_unavailable(paths, "restart");
                return;
            }
        };
        let secret = match random_hex(32) {
            Ok(secret) => secret,
            Err(_) => {
                self.set_unavailable(paths, "restart");
                return;
            }
        };
        let probe_secret = match random_hex(32) {
            Ok(secret) => secret,
            Err(_) => {
                self.set_unavailable(paths, "restart");
                return;
            }
        };
        let mut child = match spawn_sidecar(&sidecar, &paths, &nonce, &secret, &probe_secret) {
            Ok(child) => child,
            Err(_) => {
                self.set_unavailable(paths, "restart");
                return;
            }
        };

        let marker = RuntimeMarker {
            pid: child.id(),
            nonce: nonce.clone(),
        };
        if write_marker(&paths.marker_path, &marker).is_err() {
            let _ = graceful_stop(&mut child);
            self.set_unavailable(paths, "restart");
            return;
        }
        if !wait_for_ready(&mut child, &nonce, &probe_secret) {
            if graceful_stop(&mut child).is_ok() {
                let _ = fs::remove_file(&paths.marker_path);
            }
            self.set_unavailable(paths, "restart");
            return;
        }

        if let Ok(mut inner) = self.inner.lock() {
            inner.child = Some(child);
            inner.paths = Some(paths);
            inner.runtime_nonce = Some(nonce);
            inner.session_secret = Some(SessionSecret(secret));
            inner.runtime_probe_secret = Some(RuntimeProbeSecret(probe_secret));
            inner.health_failure_count = 0;
            inner.status = RuntimeStatus::ready();
        } else {
            if graceful_stop(&mut child).is_ok() {
                let _ = fs::remove_file(&paths.marker_path);
            }
        }
    }

    pub fn status(&self) -> RuntimeStatus {
        let Ok(mut inner) = self.inner.lock() else {
            return RuntimeStatus::unavailable("restart");
        };
        let exited = match inner.child.as_mut() {
            Some(child) => child
                .try_wait()
                .map(|status| status.is_some())
                .unwrap_or(false),
            None => false,
        };
        let health_result = match (
            inner.child.as_ref(),
            inner.runtime_nonce.as_deref(),
            inner.runtime_probe_secret.as_ref(),
            inner.status.state.as_str(),
        ) {
            (Some(child), Some(nonce), Some(probe_secret), "ready") => {
                match read_health_identity(&probe_secret.0) {
                    Some(identity) if identity.pid == child.id() && identity.nonce == nonce => {
                        HealthCheck::MatchesOwner
                    }
                    Some(_) => HealthCheck::OwnershipMismatch,
                    None => HealthCheck::Unavailable,
                }
            }
            (_, _, _, "ready") => HealthCheck::OwnershipMismatch,
            _ => HealthCheck::NotChecked,
        };
        let has_session_secret = inner
            .session_secret
            .as_ref()
            .is_some_and(|secret| !secret.0.is_empty());
        let should_stop_for_health = should_stop_after_health_check(
            &mut inner.health_failure_count,
            health_result,
            has_session_secret,
        );
        if exited || (inner.status.state == "ready" && should_stop_for_health) {
            let mut child = inner.child.take();
            let paths = inner.paths.clone();
            inner.session_secret = None;
            inner.runtime_probe_secret = None;
            inner.runtime_nonce = None;
            inner.health_failure_count = 0;
            inner.status = RuntimeStatus::unavailable("restart");
            if exited {
                if let Some(paths) = paths {
                    let _ = fs::remove_file(&paths.marker_path);
                }
            } else if let Some(child) = child.as_mut() {
                if graceful_stop(child).is_ok() {
                    if let Some(paths) = paths {
                        let _ = fs::remove_file(&paths.marker_path);
                    }
                }
            }
        }
        let mut status = inner.status.clone();
        status.endpoint = inner.endpoint.to_string();
        status
    }

    pub async fn status_async(&self) -> RuntimeStatus {
        let runtime = self.clone();
        tauri::async_runtime::spawn_blocking(move || runtime.status())
            .await
            .unwrap_or_else(|_| RuntimeStatus::unavailable("restart"))
    }

    pub fn shutdown(&self) {
        let Ok(mut inner) = self.inner.lock() else {
            return;
        };
        let paths = inner.paths.clone();
        let stopped = match inner.child.as_mut() {
            Some(child) => graceful_stop(child).is_ok(),
            None => true,
        };
        if stopped {
            inner.child = None;
            if let Some(paths) = paths {
                let _ = fs::remove_file(&paths.marker_path);
            }
        }
        inner.session_secret = None;
        inner.runtime_probe_secret = None;
        inner.runtime_nonce = None;
        inner.health_failure_count = 0;
        inner.repair_in_progress = false;
        clear_file_scopes(&mut inner.file_scopes);
        inner.status = if stopped {
            RuntimeStatus::stopped()
        } else {
            RuntimeStatus::unavailable("restart")
        };
    }

    pub fn repair(&self, app: &AppHandle) -> RuntimeStatus {
        {
            let Ok(mut inner) = self.inner.lock() else {
                return RuntimeStatus::unavailable("repair_runtime_state");
            };
            if inner.repair_in_progress
                || inner.status.recovery_action != "repair_runtime_state"
                || inner.child.is_some()
            {
                return status_for_inner(&inner);
            }
            if endpoint_is_open() {
                inner.status = RuntimeStatus::port_collision();
                return status_for_inner(&inner);
            }
            let Some(paths) = inner.paths.clone() else {
                return RuntimeStatus::unavailable("repair_runtime_state");
            };
            if quarantine_marker(&paths.marker_path).is_err() {
                return status_for_inner(&inner);
            }
            inner.repair_in_progress = true;
        }

        // The unverified record is retained as a private quarantine artifact.
        // A second startup check still detects a listener that appeared during repair.
        self.initialize(app);
        let Ok(mut inner) = self.inner.lock() else {
            return RuntimeStatus::unavailable("restart");
        };
        inner.repair_in_progress = false;
        status_for_inner(&inner)
    }

    pub async fn repair_async(&self, app: AppHandle) -> RuntimeStatus {
        let runtime = self.clone();
        tauri::async_runtime::spawn_blocking(move || runtime.repair(&app))
            .await
            .unwrap_or_else(|_| RuntimeStatus::unavailable("restart"))
    }

    pub async fn proxy_operation_async(
        &self,
        operation_name: String,
        payload: Option<Value>,
    ) -> Result<Value, String> {
        let runtime = self.clone();
        tauri::async_runtime::spawn_blocking(move || {
            runtime.proxy_operation(&operation_name, payload)
        })
        .await
        .map_err(|_| "local API operation did not complete".to_owned())?
    }

    fn proxy_operation(
        &self,
        operation_name: &str,
        payload: Option<Value>,
    ) -> Result<Value, String> {
        let operation = resolve_api_operation(operation_name)
            .ok_or_else(|| "local API operation is not allowed".to_owned())?;
        debug_assert!(!operation.name.is_empty());
        let (session_secret, probe_secret) = self.verified_runtime_secrets()?;
        if operation.requires_payload && payload.is_none() {
            return Err("local API operation requires a payload".to_owned());
        }
        let requires_project = operation_requires_project(operation.name);
        if !operation.requires_payload && payload.is_some() && !requires_project {
            return Err("local API operation does not accept a payload".to_owned());
        }
        let (request_payload, project_id) = if requires_project {
            let mut payload = payload.ok_or_else(|| "local API operation requires a project context".to_owned())?;
            let project_id = take_project_id(&mut payload)?;
            let request_payload = if operation.method == "GET" {
                None
            } else {
                Some(payload)
            };
            (request_payload, Some(project_id))
        } else {
            (payload, None)
        };
        let headers = project_id
            .as_deref()
            .map(|value| (PROJECT_HEADER, value))
            .into_iter()
            .collect::<Vec<_>>();
        send_local_api_request_with_headers(
            &session_secret,
            &probe_secret,
            operation.method,
            operation.path,
            request_payload.as_ref(),
            &headers,
        )
    }

    pub async fn delete_data_with_native_confirmation(
        &self,
        app: AppHandle,
        payload: Value,
    ) -> Result<Value, String> {
        let mut request_payload = payload;
        let project_id = take_project_id(&mut request_payload)?;
        if !request_native_delete_confirmation(app).await? {
            return Err("local data deletion was cancelled".to_owned());
        }
        let runtime = self.clone();
        tauri::async_runtime::spawn_blocking(move || {
            runtime.delete_data_after_native_confirmation(project_id, request_payload)
        })
        .await
        .map_err(|_| "local data deletion did not complete".to_owned())?
    }

    fn delete_data_after_native_confirmation(
        &self,
        project_id: String,
        request_payload: Value,
    ) -> Result<Value, String> {
        let (session_secret, probe_secret) = self.verified_runtime_secrets()?;
        let challenge_response = send_local_api_request_with_headers(
            &session_secret,
            &probe_secret,
            "POST",
            "/data/delete/challenge",
            Some(&json!({})),
            &[(PROJECT_HEADER, &project_id)],
        )?;
        let challenge = challenge_response
            .get("challenge")
            .and_then(Value::as_str)
            .filter(|value| {
                value.len() >= 32
                    && value.len() <= 256
                    && value
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
            })
            .ok_or_else(|| "local API delete challenge is invalid".to_owned())?;
        let response = send_local_api_request_with_headers(
            &session_secret,
            &probe_secret,
            "POST",
            "/data/delete",
            Some(&request_payload),
            &[(PROJECT_HEADER, &project_id), (DELETE_CHALLENGE_HEADER, challenge)],
        )?;
        if response.get("deleted").and_then(Value::as_bool) != Some(true) {
            return Err("local data deletion was not confirmed by the runtime".to_owned());
        }
        self.clear_transient_file_data_after_delete()?;
        Ok(response)
    }

    fn clear_transient_file_data_after_delete(&self) -> Result<(), String> {
        let paths = {
            let mut inner = self
                .inner
                .lock()
                .map_err(|_| "local runtime is unavailable".to_owned())?;
            clear_file_scopes(&mut inner.file_scopes);
            inner.paths.clone()
        };
        if let Some(paths) = paths {
            clean_private_staging_directory(&paths.import_staging_dir)?;
            clean_private_staging_directory(&paths.export_staging_dir)?;
        }
        Ok(())
    }

    fn verified_runtime_secrets(&self) -> Result<(String, String), String> {
        let mut child_to_stop = None;
        let result = {
            let mut inner = self
                .inner
                .lock()
                .map_err(|_| "local runtime is unavailable".to_owned())?;
            if inner.status.state != "ready" || inner.child.is_none() {
                return Err("local runtime is unavailable".to_owned());
            }
            let child_exited = inner
                .child
                .as_mut()
                .expect("checked child presence")
                .try_wait()
                .map_err(|_| "local runtime is unavailable".to_owned())?
                .is_some();
            if child_exited {
                child_to_stop = inner.child.take();
                invalidate_runtime_for_proxy(&mut inner);
                Err("local runtime is unavailable".to_owned())
            } else {
                let nonce = inner.runtime_nonce.clone();
                let probe_secret = inner
                    .runtime_probe_secret
                    .as_ref()
                    .map(|secret| secret.0.clone());
                let session_secret = inner.session_secret.as_ref().map(|secret| secret.0.clone());
                let expected_pid = inner.child.as_ref().expect("checked child presence").id();
                match (nonce, probe_secret, session_secret) {
                    (Some(nonce), Some(probe_secret), Some(session_secret))
                        if read_health_identity(&probe_secret).is_some_and(|identity| {
                            identity.pid == expected_pid && identity.nonce == nonce
                        }) =>
                    {
                        Ok((session_secret, probe_secret))
                    }
                    _ => {
                        child_to_stop = inner.child.take();
                        invalidate_runtime_for_proxy(&mut inner);
                        Err("local runtime ownership could not be verified".to_owned())
                    }
                }
            }
        };
        if let Some(mut child) = child_to_stop {
            let _ = graceful_stop(&mut child);
        }
        result
    }

    pub async fn choose_import_file(
        &self,
        app: AppHandle,
        format: String,
    ) -> Result<SelectedFile, String> {
        let runtime = self.clone();
        tauri::async_runtime::spawn_blocking(move || {
            runtime.choose_import_file_blocking(app, format)
        })
        .await
        .map_err(|_| "file selection did not complete".to_owned())?
    }

    fn choose_import_file_blocking(
        &self,
        app: AppHandle,
        format: String,
    ) -> Result<SelectedFile, String> {
        let normalized_format = normalize_import_format(&format)?;
        let selection = app
            .dialog()
            .file()
            .set_title("Choose a local data file")
            .add_filter(
                normalized_format.to_uppercase(),
                &[normalized_format.as_str()],
            )
            .blocking_pick_file()
            .ok_or_else(|| "file selection was cancelled".to_owned())?;
        let selected_path =
            selection
                .into_path()
                .map_err(|_| "selected file is not a local path".to_owned())?;
        let import_staging_dir = self
            .inner
            .lock()
            .map_err(|_| "local runtime is unavailable".to_owned())?
            .paths
            .as_ref()
            .map(|paths| paths.import_staging_dir.clone())
            .ok_or_else(|| "local runtime is unavailable".to_owned())?;
        let staged = stage_selected_import_file(selected_path, &normalized_format, &import_staging_dir)?;
        let handle = random_hex(16)?;
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| "local runtime is unavailable".to_owned())?;
        prune_file_scopes(&mut inner.file_scopes);
        inner.file_scopes.insert(
            handle.clone(),
            FileScope {
                snapshot_path: staged.path,
                format: normalized_format,
                byte_len: staged.byte_len,
                sha256: staged.sha256,
                expires_at: Instant::now() + FILE_SCOPE_TTL,
            },
        );
        Ok(SelectedFile {
            handle,
            display_name: staged.display_name,
        })
    }

    pub async fn preview_selected_import(
        &self,
        handle: String,
        payload: Value,
    ) -> Result<Value, String> {
        let runtime = self.clone();
        tauri::async_runtime::spawn_blocking(move || {
            runtime.call_scoped_import(&handle, "/import/preview", payload, false)
        })
        .await
        .map_err(|_| "import preview did not complete".to_owned())?
    }

    pub async fn import_selected_file(
        &self,
        handle: String,
        payload: Value,
    ) -> Result<Value, String> {
        let runtime = self.clone();
        tauri::async_runtime::spawn_blocking(move || {
            runtime.call_scoped_import(&handle, "", payload, true)
        })
        .await
        .map_err(|_| "file import did not complete".to_owned())?
    }

    pub async fn save_export_with_dialog(
        &self,
        app: AppHandle,
        payload: Value,
    ) -> Result<Value, String> {
        let runtime = self.clone();
        tauri::async_runtime::spawn_blocking(move || {
            runtime.save_export_with_dialog_blocking(app, payload)
        })
        .await
        .map_err(|_| "export save did not complete".to_owned())?
    }

    fn save_export_with_dialog_blocking(
        &self,
        app: AppHandle,
        payload: Value,
    ) -> Result<Value, String> {
        let mut request_payload = payload
            .as_object()
            .cloned()
            .ok_or_else(|| "export requires a project context".to_owned())?;
        let project_id = request_payload
            .remove("project_id")
            .and_then(|value| value.as_str().map(str::to_owned))
            .filter(|value| is_canonical_project_id(value))
            .ok_or_else(|| "export requires a valid project context".to_owned())?;
        let format = request_payload
            .get("format")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .ok_or_else(|| "export format is required".to_owned())?
            .to_owned();
        let (extension, suggested_name) = export_file_details(&format)?;
        let selection = app
            .dialog()
            .file()
            .set_title("Save local export")
            .set_file_name(suggested_name)
            .add_filter("OpsMineFlow export", &[extension])
            .blocking_save_file()
            .ok_or_else(|| "export save was cancelled".to_owned())?;
        let target = open_selected_export_target(
            selection
                .into_path()
                .map_err(|_| "selected save location is not a local path".to_owned())?,
            extension,
        )?;
        let overwrite_confirmed = if target.exists {
            app.dialog()
                .message(
                    "Replace the existing export file? This will overwrite its current contents.",
                )
                .title("Confirm export replacement")
                .kind(MessageDialogKind::Warning)
                .buttons(MessageDialogButtons::OkCancelCustom(
                    "Replace file".to_owned(),
                    "Keep existing file".to_owned(),
                ))
                .blocking_show()
        } else {
            false
        };
        if target.exists && !overwrite_confirmed {
            return Err("export save was cancelled".to_owned());
        }
        let (session_secret, probe_secret) = self.verified_runtime_secrets()?;
        let staging_path = self.create_export_staging_path(extension)?;
        request_payload.insert("format".to_owned(), Value::String(format));
        request_payload.insert(
            "path".to_owned(),
            Value::String(staging_path.to_string_lossy().into_owned()),
        );
        request_payload.insert("overwrite_confirmed".to_owned(), Value::Bool(false));
        let response = send_file_transfer_request_with_headers(
            &session_secret,
            &probe_secret,
            "POST",
            "/export/save",
            Some(&Value::Object(request_payload)),
            &[(PROJECT_HEADER, &project_id)],
        );
        let mut response = match response {
            Ok(response) => response,
            Err(error) => {
                let _ = fs::remove_file(&staging_path);
                return Err(error);
            }
        };
        let save_result = move_staged_export(&staging_path, &target, overwrite_confirmed);
        let _ = fs::remove_file(&staging_path);
        save_result?;
        if let Some(payload) = response.as_object_mut() {
            payload.insert(
                "filename".to_owned(),
                Value::String(target.filename.to_string_lossy().into_owned()),
            );
        }
        Ok(response)
    }

    fn create_export_staging_path(&self, extension: &str) -> Result<PathBuf, String> {
        let export_staging_dir = self
            .inner
            .lock()
            .map_err(|_| "local runtime is unavailable".to_owned())?
            .paths
            .as_ref()
            .map(|paths| paths.export_staging_dir.clone())
            .ok_or_else(|| "local runtime is unavailable".to_owned())?;
        Ok(export_staging_dir.join(format!("{}.{}", random_hex(16)?, extension)))
    }

    fn call_scoped_import(
        &self,
        handle: &str,
        route: &str,
        payload: Value,
        consume_scope: bool,
    ) -> Result<Value, String> {
        let scope = {
            let mut inner = self
                .inner
                .lock()
                .map_err(|_| "local runtime is unavailable".to_owned())?;
            prune_file_scopes(&mut inner.file_scopes);
            if consume_scope {
                inner
                    .file_scopes
                    .remove(handle)
                    .ok_or_else(|| "select the file again before continuing".to_owned())?
            } else {
                inner
                    .file_scopes
                    .get(handle)
                    .cloned()
                    .ok_or_else(|| "select the file again before continuing".to_owned())?
            }
        };
        let result = self.call_staged_import(&scope, route, payload, consume_scope);
        if consume_scope {
            let _ = fs::remove_file(&scope.snapshot_path);
        }
        result
    }

    fn call_staged_import(
        &self,
        scope: &FileScope,
        route: &str,
        payload: Value,
        consume_scope: bool,
    ) -> Result<Value, String> {
        validate_staged_import_file(scope)?;
        let mut payload = payload.as_object().cloned().unwrap_or_default();
        let project_id = payload
            .remove("project_id")
            .and_then(|value| value.as_str().map(str::to_owned))
            .filter(|value| is_canonical_project_id(value))
            .ok_or_else(|| "import requires a valid project context".to_owned())?;
        payload.insert("format".to_owned(), Value::String(scope.format.clone()));
        payload.insert(
            "path".to_owned(),
            Value::String(scope.snapshot_path.to_string_lossy().into_owned()),
        );
        let target = if consume_scope {
            if scope.format == "csv" {
                "/import/csv"
            } else {
                "/import/json"
            }
        } else {
            route
        };
        let (session_secret, probe_secret) = self.verified_runtime_secrets()?;
        send_file_transfer_request_with_headers(
            &session_secret,
            &probe_secret,
            "POST",
            target,
            Some(&Value::Object(payload)),
            &[(PROJECT_HEADER, &project_id)],
        )
    }

    fn set_unavailable(&self, paths: RuntimePaths, recovery_action: &str) {
        if let Ok(mut inner) = self.inner.lock() {
            inner.paths = Some(paths);
            inner.runtime_nonce = None;
            inner.session_secret = None;
            inner.runtime_probe_secret = None;
            inner.health_failure_count = 0;
            inner.status = RuntimeStatus::unavailable(recovery_action);
        }
    }

    fn set_unavailable_without_paths(&self, recovery_action: &str) {
        if let Ok(mut inner) = self.inner.lock() {
            inner.child = None;
            inner.runtime_nonce = None;
            inner.session_secret = None;
            inner.runtime_probe_secret = None;
            inner.health_failure_count = 0;
            inner.status = RuntimeStatus::unavailable(recovery_action);
        }
    }
}

async fn request_native_delete_confirmation(app: AppHandle) -> Result<bool, String> {
    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    app.dialog()
        .message("Delete all locally stored OpsMineFlow data? This cannot be undone.")
        .title("Confirm local data deletion")
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Delete data".to_owned(),
            "Keep data".to_owned(),
        ))
        .show(move |approved| {
            let _ = sender.send(approved);
        });
    tauri::async_runtime::spawn_blocking(move || {
        receiver
            .recv_timeout(Duration::from_secs(60))
            .map_err(|_| "local data deletion confirmation timed out".to_owned())
    })
    .await
    .map_err(|_| "local data deletion confirmation did not complete".to_owned())?
}

fn invalidate_runtime_for_proxy(inner: &mut RuntimeInner) {
    inner.session_secret = None;
    inner.runtime_probe_secret = None;
    inner.runtime_nonce = None;
    inner.health_failure_count = 0;
    clear_file_scopes(&mut inner.file_scopes);
    inner.status = RuntimeStatus::unavailable("restart");
}

fn normalize_import_format(format: &str) -> Result<String, String> {
    match format.trim().to_ascii_lowercase().as_str() {
        "csv" => Ok("csv".to_owned()),
        "json" => Ok("json".to_owned()),
        _ => Err("choose CSV or JSON before selecting a file".to_owned()),
    }
}

struct StagedImport {
    path: PathBuf,
    display_name: String,
    byte_len: u64,
    sha256: String,
}

fn open_selected_import_file(path: &Path, format: &str) -> Result<File, String> {
    let extension_matches = path
        .extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| extension.eq_ignore_ascii_case(format));
    if !extension_matches {
        return Err(format!("choose a .{format} file"));
    }
    let initial_metadata = fs::symlink_metadata(path)
        .map_err(|_| "the selected file is no longer available; choose it again".to_owned())?;
    if initial_metadata.file_type().is_symlink() || !initial_metadata.file_type().is_file() {
        return Err("choose a regular local file, not a folder or link".to_owned());
    }
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(|_| "the selected file could not be opened safely; choose it again".to_owned())?;
    let metadata = file
        .metadata()
        .map_err(|_| "the selected file is no longer available; choose it again".to_owned())?;
    if !metadata.is_file() || metadata.uid() != unsafe { libc::geteuid() } {
        return Err("choose a regular file owned by the signed-in macOS user".to_owned());
    }
    if metadata.len() > 100 * 1024 * 1024 {
        return Err("the selected file is larger than the 100 MB import limit".to_owned());
    }
    Ok(file)
}

fn stage_selected_import_file(
    path: PathBuf,
    format: &str,
    staging_dir: &Path,
) -> Result<StagedImport, String> {
    let display_name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("selected file")
        .to_owned();
    let mut source = open_selected_import_file(&path, format)?;
    let staged_path = staging_dir.join(format!("{}.{}", random_hex(16)?, format));
    let mut target = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&staged_path)
        .map_err(|_| "could not secure the selected import file".to_owned())?;
    let mut digest = Sha256::new();
    let mut byte_len = 0_u64;
    let mut buffer = [0_u8; 32 * 1024];
    let copy_result = (|| -> Result<(), String> {
        loop {
            let read = source
                .read(&mut buffer)
                .map_err(|_| "could not read the selected import file".to_owned())?;
            if read == 0 {
                break;
            }
            byte_len = byte_len.saturating_add(read as u64);
            if byte_len > 100 * 1024 * 1024 {
                return Err("the selected file is larger than the 100 MB import limit".to_owned());
            }
            target
                .write_all(&buffer[..read])
                .map_err(|_| "could not secure the selected import file".to_owned())?;
            digest.update(&buffer[..read]);
        }
        target
            .sync_all()
            .map_err(|_| "could not secure the selected import file".to_owned())?;
        fs::set_permissions(&staged_path, fs::Permissions::from_mode(0o400))
            .map_err(|_| "could not secure the selected import file".to_owned())?;
        Ok(())
    })();
    drop(target);
    if let Err(error) = copy_result {
        let _ = fs::remove_file(&staged_path);
        return Err(error);
    }
    Ok(StagedImport {
        path: staged_path,
        display_name,
        byte_len,
        sha256: hex_digest(digest.finalize().as_slice()),
    })
}

fn validate_staged_import_file(scope: &FileScope) -> Result<(), String> {
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&scope.snapshot_path)
        .map_err(|_| "the selected import snapshot is no longer available; choose it again".to_owned())?;
    let metadata = file
        .metadata()
        .map_err(|_| "the selected import snapshot is no longer available; choose it again".to_owned())?;
    if !metadata.is_file()
        || metadata.uid() != unsafe { libc::geteuid() }
        || metadata.len() != scope.byte_len
    {
        return Err("the selected import snapshot changed; choose the file again".to_owned());
    }
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 32 * 1024];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|_| "could not verify the selected import snapshot".to_owned())?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    if !constant_time_equals(hex_digest(digest.finalize().as_slice()).as_bytes(), scope.sha256.as_bytes()) {
        return Err("the selected import snapshot changed; choose the file again".to_owned());
    }
    Ok(())
}

fn hex_digest(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn lstat_at(
    directory_fd: std::os::fd::RawFd,
    filename: &std::ffi::OsStr,
) -> Result<Option<DirectoryEntryKind>, String> {
    let path = CString::new(filename.as_bytes())
        .map_err(|_| "choose a filename without an embedded NUL byte".to_owned())?;
    let mut stat_buffer = unsafe { std::mem::zeroed::<libc::stat>() };
    let result = unsafe {
        libc::fstatat(
            directory_fd,
            path.as_ptr(),
            &mut stat_buffer,
            libc::AT_SYMLINK_NOFOLLOW,
        )
    };
    if result != 0 {
        return match std::io::Error::last_os_error().raw_os_error() {
            Some(libc::ENOENT) => Ok(None),
            _ => Err("could not verify the selected export filename".to_owned()),
        };
    }
    if stat_buffer.st_mode & libc::S_IFMT == libc::S_IFREG {
        Ok(Some(DirectoryEntryKind::RegularFile))
    } else {
        Ok(Some(DirectoryEntryKind::Other))
    }
}

fn move_staged_export(
    staged_path: &Path,
    target: &ExportTarget,
    overwrite_confirmed: bool,
) -> Result<(), String> {
    let mut source = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(staged_path)
        .map_err(|_| "the prepared export is no longer available".to_owned())?;
    if !source
        .metadata()
        .map_err(|_| "the prepared export is no longer available".to_owned())?
        .is_file()
    {
        return Err("the prepared export is not a regular file".to_owned());
    }
    match lstat_at(target.directory.as_raw_fd(), &target.filename)? {
        Some(DirectoryEntryKind::RegularFile) if !overwrite_confirmed => {
            return Err("confirm replacement in the save dialog before overwriting an existing file".to_owned());
        }
        Some(DirectoryEntryKind::Other) => {
            return Err("the selected export filename changed; choose it again".to_owned());
        }
        _ => {}
    }
    let temporary_name = CString::new(format!(".opsmineflow-export.{}.tmp", random_hex(8)?))
        .map_err(|_| "could not prepare the export file".to_owned())?;
    let destination_name = CString::new(target.filename.as_bytes())
        .map_err(|_| "choose a filename without an embedded NUL byte".to_owned())?;
    let directory_fd = target.directory.as_raw_fd();
    let temporary_fd = unsafe {
        libc::openat(
            directory_fd,
            temporary_name.as_ptr(),
            libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW,
            0o600,
        )
    };
    if temporary_fd < 0 {
        return Err("could not prepare the selected export location".to_owned());
    }
    let mut temporary_file = unsafe { File::from_raw_fd(temporary_fd) };
    let write_result = std::io::copy(&mut source, &mut temporary_file)
        .and_then(|_| temporary_file.sync_all())
        .map_err(|_| "could not write the selected export location".to_owned());
    drop(temporary_file);
    if let Err(error) = write_result {
        let _ = unsafe { libc::unlinkat(directory_fd, temporary_name.as_ptr(), 0) };
        return Err(error);
    }
    let rename_result = if overwrite_confirmed {
        unsafe {
            libc::renameat(
                directory_fd,
                temporary_name.as_ptr(),
                directory_fd,
                destination_name.as_ptr(),
            )
        }
    } else {
        let link_result = unsafe {
            libc::linkat(
                directory_fd,
                temporary_name.as_ptr(),
                directory_fd,
                destination_name.as_ptr(),
                0,
            )
        };
        if link_result == 0 {
            unsafe { libc::unlinkat(directory_fd, temporary_name.as_ptr(), 0) }
        } else {
            link_result
        }
    };
    if rename_result != 0 {
        let _ = unsafe { libc::unlinkat(directory_fd, temporary_name.as_ptr(), 0) };
        return Err("the selected export filename changed; choose it again".to_owned());
    }
    target
        .directory
        .sync_all()
        .map_err(|_| "could not finalize the selected export location".to_owned())
}

fn export_file_details(format: &str) -> Result<(&'static str, &'static str), String> {
    match format {
        "markdown" => Ok(("md", "opsmineflow-report.md")),
        "json" => Ok(("json", "opsmineflow-export.json")),
        "csv" => Ok(("zip", "opsmineflow-events-with-analysis-receipt.zip")),
        "mermaid" => Ok(("mmd", "opsmineflow-flow.mmd")),
        "drawio" => Ok(("drawio", "opsmineflow-flow.drawio")),
        "llm-handoff" => Ok(("zip", "opsmineflow-mermaid-handoff.zip")),
        _ => Err("choose a supported export format".to_owned()),
    }
}

fn open_selected_export_target(path: PathBuf, extension: &str) -> Result<ExportTarget, String> {
    let parent = path
        .parent()
        .ok_or_else(|| "choose a local save location in Finder".to_owned())?;
    if !path
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case(extension))
    {
        return Err(format!("save the export with a .{extension} filename"));
    }
    let metadata = fs::symlink_metadata(parent)
        .map_err(|_| "the selected save folder is no longer available".to_owned())?;
    if metadata.file_type().is_symlink() || !metadata.file_type().is_dir() {
        return Err("choose a regular local folder, not a link".to_owned());
    }
    let directory = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(parent)
        .map_err(|_| "the selected save folder could not be opened safely".to_owned())?;
    if directory
        .metadata()
        .map_err(|_| "the selected save folder could not be inspected".to_owned())?
        .uid()
        != unsafe { libc::geteuid() }
    {
        return Err("choose a save folder owned by the signed-in macOS user".to_owned());
    }
    let filename = path
        .file_name()
        .ok_or_else(|| "choose a filename".to_owned())?
        .to_os_string();
    let existing = lstat_at(directory.as_raw_fd(), &filename)?;
    if let Some(entry) = existing.as_ref() {
        if !matches!(entry, DirectoryEntryKind::RegularFile) {
            return Err("choose a regular export filename, not a folder or link".to_owned());
        }
    }
    Ok(ExportTarget {
        directory,
        filename,
        exists: existing.is_some(),
    })
}

fn prune_file_scopes(scopes: &mut HashMap<String, FileScope>) {
    let now = Instant::now();
    scopes.retain(|_, scope| {
        if scope.expires_at >= now {
            return true;
        }
        let _ = fs::remove_file(&scope.snapshot_path);
        false
    });
}

fn clear_file_scopes(scopes: &mut HashMap<String, FileScope>) {
    for scope in scopes.drain().map(|(_, scope)| scope) {
        let _ = fs::remove_file(scope.snapshot_path);
    }
}

impl RuntimePaths {
    fn prepare(app: &AppHandle) -> Result<Self, String> {
        let root = app.path().app_local_data_dir().map_err(|error| {
            format!("could not resolve the application data directory: {error}")
        })?;
        let runtime_dir = root.join("runtime");
        let log_dir = root.join("logs");
        let data_dir = root.join("data");
        let import_staging_dir = runtime_dir.join("import-staging");
        let export_staging_dir = runtime_dir.join("export-staging");
        for directory in [
            &root,
            &runtime_dir,
            &log_dir,
            &data_dir,
            &import_staging_dir,
            &export_staging_dir,
        ] {
            create_private_directory(directory)?;
        }
        clean_private_staging_directory(&import_staging_dir)?;
        clean_private_staging_directory(&export_staging_dir)?;
        let marker_path = runtime_dir.join("sidecar-owner.json");
        verify_marker_writable(&marker_path)?;
        Ok(Self {
            marker_path,
            log_dir,
            data_dir,
            import_staging_dir,
            export_staging_dir,
        })
    }
}

fn clean_private_staging_directory(path: &Path) -> Result<(), String> {
    let _directory = open_private_directory(path)?;
    for entry in fs::read_dir(path).map_err(|_| "could not inspect private runtime staging".to_owned())? {
        let entry = entry.map_err(|_| "could not inspect private runtime staging".to_owned())?;
        let entry_path = entry.path();
        let metadata = fs::symlink_metadata(&entry_path)
            .map_err(|_| "could not inspect private runtime staging".to_owned())?;
        if metadata.file_type().is_file() && !metadata.file_type().is_symlink() {
            fs::remove_file(entry_path)
                .map_err(|_| "could not clear private runtime staging".to_owned())?;
        } else {
            return Err("private runtime staging contains an unsafe entry".to_owned());
        }
    }
    Ok(())
}

fn resolve_sidecar(app: &AppHandle) -> Result<SidecarCommand, String> {
    if cfg!(debug_assertions) {
        let program = std::env::var_os("OPSMINEFLOW_DEV_SIDECAR")
            .map(PathBuf::from)
            .ok_or_else(|| "development_setup".to_owned())?;
        let pythonpath = std::env::var("OPSMINEFLOW_DEV_PYTHONPATH")
            .map_err(|_| "development_setup".to_owned())?;
        if !program.is_absolute() || !program.is_file() || pythonpath.trim().is_empty() {
            return Err("development_setup".to_owned());
        }
        return Ok(SidecarCommand {
            program,
            pythonpath: Some(pythonpath),
            is_development: true,
        });
    }

    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|_| "reinstall".to_owned())?;
    let program = resource_dir.join("opsmineflow-local-api");
    let checksum_manifest = resource_dir.join("opsmineflow-local-api.sha256");
    if !verify_packaged_sidecar(&resource_dir, &program, &checksum_manifest) {
        return Err("reinstall".to_owned());
    }
    Ok(SidecarCommand {
        program,
        pythonpath: None,
        is_development: false,
    })
}

fn spawn_sidecar(
    sidecar: &SidecarCommand,
    paths: &RuntimePaths,
    nonce: &str,
    secret: &str,
    probe_secret: &str,
) -> Result<Child, String> {
    let log_path = paths.log_dir.join("local-api.log");
    let stdout = private_append_file(&log_path)?;
    let stderr = private_append_file(&log_path)?;
    let mut command = Command::new(&sidecar.program);
    command
        .env_clear()
        .env("OPSMINEFLOW_API_HOST", LOCAL_API_HOST)
        .env("OPSMINEFLOW_API_PORT", LOCAL_API_PORT.to_string())
        .env("OPSMINEFLOW_DATA_DIR", &paths.data_dir)
        .env("OPSMINEFLOW_LOG_DIR", &paths.log_dir)
        .env("OPSMINEFLOW_RUNTIME_NONCE", nonce)
        .env("OPSMINEFLOW_RUNTIME_SECRET", secret)
        .env("OPSMINEFLOW_RUNTIME_PROBE_SECRET", probe_secret)
        .env("OPSMINEFLOW_PARENT_PID", std::process::id().to_string())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    if sidecar.is_development {
        command.args(["-m", "opsmineflow_api"]).env(
            "PYTHONPATH",
            sidecar.pythonpath.as_deref().unwrap_or_default(),
        );
    }
    command
        .spawn()
        .map_err(|_| "could not start the local runtime".to_owned())
}

fn recover_owned_orphan(paths: &RuntimePaths) -> Result<(), String> {
    verify_marker_is_regular_file(&paths.marker_path)?;
    let contents = match fs::read_to_string(&paths.marker_path) {
        Ok(contents) => contents,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(format!("could not read previous runtime state: {error}")),
    };
    let marker = serde_json::from_str::<RuntimeMarker>(&contents)
        .map_err(|_| "could not verify previous runtime ownership state".to_owned())?;
    if !pid_exists(marker.pid)? {
        return remove_marker(&paths.marker_path);
    }
    if wait_for_orphan_to_exit(marker.pid)? {
        return remove_marker(&paths.marker_path);
    }
    Err("a previous local runtime is still running; refusing to terminate an unverifiable process".to_owned())
}

fn wait_for_orphan_to_exit(pid: u32) -> Result<bool, String> {
    let started_at = Instant::now();
    while started_at.elapsed() < ORPHAN_SELF_EXIT_TIMEOUT {
        if !pid_exists(pid)? {
            return Ok(true);
        }
        thread::sleep(Duration::from_millis(100));
    }
    Ok(!pid_exists(pid)?)
}

fn wait_for_ready(child: &mut Child, nonce: &str, probe_secret: &str) -> bool {
    let started_at = Instant::now();
    while started_at.elapsed() < READY_TIMEOUT {
        if let Some(identity) = read_health_identity(probe_secret) {
            if identity.nonce == nonce && identity.pid == child.id() {
                return true;
            }
        }
        if child.try_wait().ok().flatten().is_some() {
            return false;
        }
        thread::sleep(Duration::from_millis(100));
    }
    false
}

fn graceful_stop(child: &mut Child) -> Result<(), String> {
    terminate_pid(child.id())?;
    let started_at = Instant::now();
    while started_at.elapsed() < SHUTDOWN_TIMEOUT {
        if child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_some()
        {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(100));
    }
    child.kill().map_err(|error| error.to_string())?;
    child.wait().map_err(|error| error.to_string())?;
    Ok(())
}

fn terminate_pid(pid: u32) -> Result<(), String> {
    let pid = checked_pid(pid)?;
    let result = unsafe { libc::kill(pid as i32, libc::SIGTERM) };
    if result == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error().to_string())
    }
}

fn pid_exists(pid: u32) -> Result<bool, String> {
    let pid = checked_pid(pid)?;
    let result = unsafe { libc::kill(pid as i32, 0) };
    if result == 0 {
        return Ok(true);
    }
    match std::io::Error::last_os_error().raw_os_error() {
        Some(libc::ESRCH) => Ok(false),
        Some(libc::EPERM) => Ok(true),
        _ => Err("could not check whether the previous runtime still exists".to_owned()),
    }
}

fn checked_pid(pid: u32) -> Result<u32, String> {
    if pid == 0 || pid > i32::MAX as u32 {
        return Err("runtime ownership state contains an invalid process identifier".to_owned());
    }
    Ok(pid)
}

fn remove_marker(path: &Path) -> Result<(), String> {
    fs::remove_file(path).map_err(|error| format!("could not clear stale runtime state: {error}"))
}

fn quarantine_marker(path: &Path) -> Result<PathBuf, String> {
    let quarantined_path =
        path.with_file_name(format!("sidecar-owner.quarantined.{}.json", random_hex(8)?));
    verify_marker_is_regular_file(path)?;
    set_private_file_permissions(path)?;
    fs::rename(path, &quarantined_path)
        .map_err(|error| format!("could not quarantine unverified runtime state: {error}"))?;
    Ok(quarantined_path)
}

fn verify_marker_is_regular_file(path: &Path) -> Result<(), String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_file() && !metadata.file_type().is_symlink() => {
            Ok(())
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        _ => Err("runtime ownership state is not a private regular file".to_owned()),
    }
}

fn endpoint_is_open() -> bool {
    endpoint_is_open_at(endpoint())
}

fn endpoint_is_open_at(address: SocketAddr) -> bool {
    TcpStream::connect_timeout(&address, Duration::from_millis(150)).is_ok()
}

fn read_health_identity(probe_secret: &str) -> Option<RuntimeIdentity> {
    read_health_identity_at(endpoint(), probe_secret)
}

fn read_health_identity_at(address: SocketAddr, probe_secret: &str) -> Option<RuntimeIdentity> {
    let challenge = random_hex(32).ok()?;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(250)).ok()?;
    stream
        .set_read_timeout(Some(Duration::from_millis(250)))
        .ok()?;
    stream
        .set_write_timeout(Some(Duration::from_millis(250)))
        .ok()?;
    let request = format!(
        "GET /runtime/health HTTP/1.1\r\nHost: {LOCAL_API_HOST}:{LOCAL_API_PORT}\r\n{RUNTIME_PROBE_CHALLENGE_HEADER}: {challenge}\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    let (_, body) = response.split_once("\r\n\r\n")?;
    let health: HealthPayload = serde_json::from_str(body).ok()?;
    if health.status != "ok" || health.bind != LOCAL_API_HOST || !health.local_only {
        return None;
    }
    let identity = health.runtime?;
    let expected_proof = runtime_probe_proof(probe_secret, &challenge)?;
    if !constant_time_equals(identity.proof.as_bytes(), expected_proof.as_bytes()) {
        return None;
    }
    Some(identity)
}

fn runtime_probe_proof(probe_secret: &str, challenge: &str) -> Option<String> {
    let mut mac = Hmac::<Sha256>::new_from_slice(probe_secret.as_bytes()).ok()?;
    mac.update(challenge.as_bytes());
    Some(
        mac.finalize()
            .into_bytes()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect(),
    )
}

fn constant_time_equals(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.iter()
        .zip(right)
        .fold(0_u8, |diff, (a, b)| diff | (a ^ b))
        == 0
}

fn write_marker(path: &Path, marker: &RuntimeMarker) -> Result<(), String> {
    verify_marker_is_regular_file(path)?;
    let temporary_path = path.with_file_name(format!(
        ".sidecar-owner.{}.tmp",
        random_hex(8)?
    ));
    let contents = serde_json::to_vec(marker).map_err(|error| error.to_string())?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temporary_path)
        .map_err(|error| format!("could not write runtime ownership state: {error}"))?;
    let result = file
        .write_all(&contents)
        .and_then(|_| file.sync_all())
        .map_err(|error| format!("could not write runtime ownership state: {error}"));
    drop(file);
    if let Err(error) = result {
        let _ = fs::remove_file(&temporary_path);
        return Err(error);
    }
    fs::rename(&temporary_path, path)
        .and_then(|_| fsync_directory(path.parent().unwrap_or(Path::new("."))))
        .map_err(|error| format!("could not store runtime ownership state: {error}"))
}

fn fsync_directory(path: &Path) -> std::io::Result<()> {
    File::open(path)?.sync_all()
}

fn verify_marker_writable(path: &Path) -> Result<(), String> {
    let probe_name = format!(
        ".{}.{}.probe",
        path.file_stem()
            .and_then(|name| name.to_str())
            .unwrap_or("runtime"),
        random_hex(8)?
    );
    let probe_path = path.with_file_name(probe_name);
    let _probe = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&probe_path)
        .map_err(|error| format!("could not prepare private runtime state: {error}"))?;
    fs::remove_file(probe_path)
        .map_err(|error| format!("could not clear runtime state probe: {error}"))
}

fn random_hex(byte_count: usize) -> Result<String, String> {
    let mut bytes = vec![0_u8; byte_count];
    File::open("/dev/urandom")
        .and_then(|mut random| random.read_exact(&mut bytes))
        .map_err(|error| format!("could not obtain runtime entropy: {error}"))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn create_private_directory(path: &Path) -> Result<(), String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() && !metadata.file_type().is_symlink() => {}
        Ok(_) => return Err("runtime directory is not a private regular directory".to_owned()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(path)
                .map_err(|error| format!("could not create runtime directory: {error}"))?;
        }
        Err(error) => return Err(format!("could not inspect runtime directory: {error}")),
    }
    let directory = open_private_directory(path)?;
    let metadata = directory
        .metadata()
        .map_err(|_| "could not inspect runtime directory".to_owned())?;
    if !metadata.is_dir() || metadata.uid() != unsafe { libc::geteuid() } {
        return Err("runtime directory is not owned by the signed-in macOS user".to_owned());
    }
    directory
        .set_permissions(fs::Permissions::from_mode(0o700))
        .map_err(|error| format!("could not secure runtime directory: {error}"))
}

fn open_private_directory(path: &Path) -> Result<File, String> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(path)
        .map_err(|_| "runtime directory is not a private regular directory".to_owned())
}

fn private_append_file(path: &Path) -> Result<File, String> {
    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(path)
        .map_err(|error| format!("could not open local runtime log: {error}"))?;
    set_private_file_permissions(path)?;
    Ok(file)
}

fn set_private_file_permissions(path: &Path) -> Result<(), String> {
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
        .map_err(|error| format!("could not secure runtime file: {error}"))
}

fn verify_packaged_sidecar(resource_dir: &Path, program: &Path, checksum_manifest: &Path) -> bool {
    let resource_dir = match resource_dir.canonicalize() {
        Ok(path) => path,
        Err(_) => return false,
    };
    let program_metadata = match fs::symlink_metadata(program) {
        Ok(metadata) if metadata.file_type().is_file() && !metadata.file_type().is_symlink() => {
            metadata
        }
        _ => return false,
    };
    let manifest_metadata = match fs::symlink_metadata(checksum_manifest) {
        Ok(metadata) if metadata.file_type().is_file() && !metadata.file_type().is_symlink() => {
            metadata
        }
        _ => return false,
    };
    if program_metadata.permissions().mode() & 0o022 != 0
        || manifest_metadata.permissions().mode() & 0o022 != 0
    {
        return false;
    }
    let program = match program.canonicalize() {
        Ok(path) if path.starts_with(&resource_dir) => path,
        _ => return false,
    };
    let expected = match fs::read_to_string(checksum_manifest)
        .ok()
        .and_then(|contents| contents.split_whitespace().next().map(str::to_owned))
    {
        Some(value)
            if value.len() == 64
                && value.chars().all(|character| character.is_ascii_hexdigit()) =>
        {
            value
        }
        _ => return false,
    };
    sha256_file(&program)
        .map(|actual| actual.eq_ignore_ascii_case(&expected))
        .unwrap_or(false)
}

fn sha256_file(path: &Path) -> Result<String, std::io::Error> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    std::io::copy(&mut file, &mut hasher)?;
    Ok(hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

struct ApiOperation<'a> {
    name: &'a str,
    method: &'static str,
    path: &'static str,
    requires_payload: bool,
}

fn resolve_api_operation(name: &str) -> Option<ApiOperation<'_>> {
    let operation = match name {
        "health" => ApiOperation {
            name,
            method: "GET",
            path: "/health",
            requires_payload: false,
        },
        "projects" => ApiOperation {
            name,
            method: "GET",
            path: "/projects",
            requires_payload: false,
        },
        "project_create" => ApiOperation {
            name,
            method: "POST",
            path: "/projects",
            requires_payload: true,
        },
        "project_select" => ApiOperation {
            name,
            method: "POST",
            path: "/projects/select",
            requires_payload: true,
        },
        "project_rename" => ApiOperation {
            name,
            method: "POST",
            path: "/projects/rename",
            requires_payload: true,
        },
        "project_delete" => ApiOperation {
            name,
            method: "POST",
            path: "/projects/delete",
            requires_payload: true,
        },
        "diagnostics" => ApiOperation {
            name,
            method: "GET",
            path: "/diagnostics",
            requires_payload: false,
        },
        "recording_status" => ApiOperation {
            name,
            method: "GET",
            path: "/recording/status",
            requires_payload: false,
        },
        "settings" => ApiOperation {
            name,
            method: "GET",
            path: "/settings",
            requires_payload: false,
        },
        "import_history" => ApiOperation {
            name,
            method: "GET",
            path: "/import/history",
            requires_payload: false,
        },
        "events_page" => ApiOperation {
            name,
            method: "POST",
            path: "/events/page",
            requires_payload: true,
        },
        "event_quality" => ApiOperation {
            name,
            method: "GET",
            path: "/analytics/event-quality",
            requires_payload: false,
        },
        "summary" => ApiOperation {
            name,
            method: "GET",
            path: "/analytics/summary",
            requires_payload: false,
        },
        "process_map" => ApiOperation {
            name,
            method: "GET",
            path: "/analytics/process-map",
            requires_payload: false,
        },
        "automation_candidates" => ApiOperation {
            name,
            method: "GET",
            path: "/analytics/automation-candidates",
            requires_payload: false,
        },
        "app_switching" => ApiOperation {
            name,
            method: "GET",
            path: "/analytics/app-switching",
            requires_payload: false,
        },
        "report_markdown" => ApiOperation {
            name,
            method: "GET",
            path: "/reports/markdown",
            requires_payload: false,
        },
        "diagnostics_checks" => ApiOperation {
            name,
            method: "POST",
            path: "/diagnostics/checks",
            requires_payload: true,
        },
        "recording_start" => ApiOperation {
            name,
            method: "POST",
            path: "/recording/start",
            requires_payload: true,
        },
        "recording_stop" => ApiOperation {
            name,
            method: "POST",
            path: "/recording/stop",
            requires_payload: true,
        },
        "recording_pause" => ApiOperation {
            name,
            method: "POST",
            path: "/recording/pause",
            requires_payload: true,
        },
        "recording_resume" => ApiOperation {
            name,
            method: "POST",
            path: "/recording/resume",
            requires_payload: true,
        },
        "activitywatch_preview" => ApiOperation {
            name,
            method: "POST",
            path: "/import/activitywatch-preview",
            requires_payload: true,
        },
        "activitywatch_import" => ApiOperation {
            name,
            method: "POST",
            path: "/import/activitywatch-local",
            requires_payload: true,
        },
        "settings_update" => ApiOperation {
            name,
            method: "POST",
            path: "/settings",
            requires_payload: true,
        },
        "automation_review" => ApiOperation {
            name,
            method: "POST",
            path: "/automation/review",
            requires_payload: true,
        },
        "event_label" => ApiOperation {
            name,
            method: "POST",
            path: "/events/label",
            requires_payload: true,
        },
        "event_activity" => ApiOperation {
            name,
            method: "POST",
            path: "/events/activity",
            requires_payload: true,
        },
        "event_case_correlation" => ApiOperation {
            name,
            method: "POST",
            path: "/events/case-correlation",
            requires_payload: true,
        },
        "event_exclude" => ApiOperation {
            name,
            method: "POST",
            path: "/events/exclude",
            requires_payload: true,
        },
        "event_quality_review" => ApiOperation {
            name,
            method: "POST",
            path: "/events/quality-review",
            requires_payload: true,
        },
        "event_split" => ApiOperation {
            name,
            method: "POST",
            path: "/events/split",
            requires_payload: true,
        },
        "event_merge" => ApiOperation {
            name,
            method: "POST",
            path: "/events/merge",
            requires_payload: true,
        },
        "export_preview" => ApiOperation {
            name,
            method: "POST",
            path: "/export/preview",
            requires_payload: true,
        },
        _ => return None,
    };
    Some(operation)
}

fn operation_requires_project(name: &str) -> bool {
    !matches!(
        name,
        "health" | "projects" | "project_create" | "project_select" | "project_rename" | "project_delete"
    )
}

fn take_project_id(payload: &mut Value) -> Result<String, String> {
    let project_id = payload
        .as_object_mut()
        .and_then(|object| object.remove("project_id"))
        .and_then(|value| value.as_str().map(str::to_owned))
        .ok_or_else(|| "local API operation requires a project context".to_owned())?;
    if !is_canonical_project_id(&project_id) {
        return Err("local API project context is invalid".to_owned());
    }
    Ok(project_id)
}

fn is_canonical_project_id(value: &str) -> bool {
    value.len() == 36
        && value
            .bytes()
            .enumerate()
            .all(|(index, byte)| match index {
                8 | 13 | 18 | 23 => byte == b'-',
                _ => byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase(),
            })
}

fn send_local_api_request_with_headers(
    session_secret: &str,
    probe_secret: &str,
    method: &str,
    path: &str,
    payload: Option<&Value>,
    extra_headers: &[(&str, &str)],
) -> Result<Value, String> {
    send_local_api_request_with_timeout_and_headers(
        session_secret,
        probe_secret,
        method,
        path,
        payload,
        extra_headers,
        API_PROXY_TIMEOUT,
    )
}

fn send_file_transfer_request_with_headers(
    session_secret: &str,
    probe_secret: &str,
    method: &str,
    path: &str,
    payload: Option<&Value>,
    extra_headers: &[(&str, &str)],
) -> Result<Value, String> {
    send_local_api_request_with_timeout_and_headers(
        session_secret,
        probe_secret,
        method,
        path,
        payload,
        extra_headers,
        FILE_TRANSFER_TIMEOUT,
    )
}

fn send_local_api_request_with_timeout_and_headers(
    session_secret: &str,
    probe_secret: &str,
    method: &str,
    path: &str,
    payload: Option<&Value>,
    extra_headers: &[(&str, &str)],
    timeout: Duration,
) -> Result<Value, String> {
    send_local_api_request_at_with_timeout_and_headers(
        session_secret,
        probe_secret,
        endpoint(),
        method,
        path,
        payload,
        extra_headers,
        timeout,
    )
}

#[cfg(test)]
fn send_local_api_request_at(
    session_secret: &str,
    probe_secret: &str,
    address: SocketAddr,
    method: &str,
    path: &str,
    payload: Option<&Value>,
    extra_header: Option<(&str, &str)>,
) -> Result<Value, String> {
    send_local_api_request_at_with_timeout(
        session_secret,
        probe_secret,
        address,
        method,
        path,
        payload,
        extra_header,
        API_PROXY_TIMEOUT,
    )
}

#[cfg(test)]
fn send_local_api_request_at_with_timeout(
    session_secret: &str,
    probe_secret: &str,
    address: SocketAddr,
    method: &str,
    path: &str,
    payload: Option<&Value>,
    extra_header: Option<(&str, &str)>,
    timeout: Duration,
) -> Result<Value, String> {
    let headers = extra_header.into_iter().collect::<Vec<_>>();
    send_local_api_request_at_with_timeout_and_headers(
        session_secret,
        probe_secret,
        address,
        method,
        path,
        payload,
        &headers,
        timeout,
    )
}

fn send_local_api_request_at_with_timeout_and_headers(
    session_secret: &str,
    probe_secret: &str,
    address: SocketAddr,
    method: &str,
    path: &str,
    payload: Option<&Value>,
    extra_headers: &[(&str, &str)],
    timeout: Duration,
) -> Result<Value, String> {
    let deadline = Instant::now() + timeout;
    let body = if method == "POST" {
        serde_json::to_vec(payload.unwrap_or(&json!({})))
            .map_err(|_| "could not encode local API request".to_owned())?
    } else {
        Vec::new()
    };
    if body.len() > MAX_API_PROXY_BODY_BYTES {
        return Err("local API request is too large".to_owned());
    }
    let mut stream = TcpStream::connect_timeout(&address, timeout)
        .map_err(|_| "could not connect to the local runtime".to_owned())?;
    configure_proxy_timeouts(&stream, deadline)?;
    verify_same_connection_sidecar(&mut stream, probe_secret, deadline)?;
    let mut request = format!(
        "{method} {path} HTTP/1.1\r\nHost: {LOCAL_API_HOST}:{LOCAL_API_PORT}\r\n{API_SESSION_HEADER}: {session_secret}\r\nConnection: close\r\n"
    );
    if method == "POST" {
        request.push_str(&format!(
            "Content-Type: application/json\r\nContent-Length: {}\r\n",
            body.len()
        ));
    }
    for (name, value) in extra_headers {
        request.push_str(name);
        request.push_str(": ");
        request.push_str(value);
        request.push_str("\r\n");
    }
    request.push_str("\r\n");
    stream
        .write_all(request.as_bytes())
        .and_then(|_| stream.write_all(&body))
        .map_err(|_| "could not send local API request".to_owned())?;
    let (status_code, response_body) = read_http_response(&mut stream, deadline)?;
    if !(200..300).contains(&status_code) {
        return Err(format!(
            "local API request failed with status {status_code}"
        ));
    }
    serde_json::from_slice(&response_body).map_err(|_| "local API returned invalid JSON".to_owned())
}

fn verify_same_connection_sidecar(
    stream: &mut TcpStream,
    probe_secret: &str,
    deadline: Instant,
) -> Result<(), String> {
    let challenge = random_hex(32)?;
    let request = format!(
        "GET /runtime/health HTTP/1.1\r\nHost: {LOCAL_API_HOST}:{LOCAL_API_PORT}\r\n{RUNTIME_PROBE_CHALLENGE_HEADER}: {challenge}\r\nConnection: keep-alive\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|_| "could not verify the local runtime".to_owned())?;
    let (status_code, body) = read_http_response(stream, deadline)?;
    if status_code != 200 {
        return Err("local runtime ownership could not be verified".to_owned());
    }
    let health: HealthPayload = serde_json::from_slice(&body)
        .map_err(|_| "local runtime ownership could not be verified".to_owned())?;
    let identity = health
        .runtime
        .ok_or_else(|| "local runtime ownership could not be verified".to_owned())?;
    let expected_proof = runtime_probe_proof(probe_secret, &challenge)
        .ok_or_else(|| "local runtime ownership could not be verified".to_owned())?;
    if health.status != "ok"
        || health.bind != LOCAL_API_HOST
        || !health.local_only
        || !constant_time_equals(identity.proof.as_bytes(), expected_proof.as_bytes())
    {
        return Err("local runtime ownership could not be verified".to_owned());
    }
    Ok(())
}

fn configure_proxy_timeouts(stream: &TcpStream, deadline: Instant) -> Result<(), String> {
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Err("local API request timed out".to_owned());
    }
    stream
        .set_read_timeout(Some(remaining))
        .and_then(|_| stream.set_write_timeout(Some(remaining)))
        .map_err(|_| "could not configure the local runtime connection".to_owned())
}

fn read_http_response(stream: &mut TcpStream, deadline: Instant) -> Result<(u16, Vec<u8>), String> {
    let mut response = Vec::new();
    let mut chunk = [0_u8; 8192];
    let header_end = loop {
        configure_proxy_timeouts(stream, deadline)?;
        let read = stream
            .read(&mut chunk)
            .map_err(|_| "could not read local API response".to_owned())?;
        if read == 0 {
            return Err("local API returned an incomplete response".to_owned());
        }
        if response.len().saturating_add(read)
            > MAX_API_PROXY_HEADER_BYTES + MAX_API_PROXY_RESPONSE_BYTES
        {
            return Err("local API response is too large".to_owned());
        }
        response.extend_from_slice(&chunk[..read]);
        if let Some(header_end) = response.windows(4).position(|window| window == b"\r\n\r\n") {
            break header_end + 4;
        }
        if response.len() > MAX_API_PROXY_HEADER_BYTES {
            return Err("local API response headers are too large".to_owned());
        }
    };
    let header_text = std::str::from_utf8(&response[..header_end])
        .map_err(|_| "local API returned invalid headers".to_owned())?;
    let mut header_lines = header_text.split("\r\n");
    let status = header_lines
        .next()
        .ok_or_else(|| "local API returned an invalid response".to_owned())?;
    let status_code = match status.split_once(' ') {
        Some(("HTTP/1.0" | "HTTP/1.1", rest)) => rest
            .split_whitespace()
            .next()
            .and_then(|value| value.parse::<u16>().ok())
            .ok_or_else(|| "local API returned an invalid response".to_owned())?,
        _ => return Err("local API returned an invalid response".to_owned()),
    };
    let mut content_length = None;
    for line in header_lines {
        if line.is_empty() {
            continue;
        }
        let (name, value) = line
            .split_once(':')
            .ok_or_else(|| "local API returned invalid headers".to_owned())?;
        if name.eq_ignore_ascii_case("transfer-encoding") {
            return Err("local API returned unsupported response framing".to_owned());
        }
        if name.eq_ignore_ascii_case("content-length") {
            if content_length.is_some() {
                return Err("local API returned conflicting response lengths".to_owned());
            }
            let length = value
                .trim()
                .parse::<usize>()
                .map_err(|_| "local API returned invalid response length".to_owned())?;
            if length > MAX_API_PROXY_RESPONSE_BYTES {
                return Err("local API response is too large".to_owned());
            }
            content_length = Some(length);
        }
    }
    let content_length =
        content_length.ok_or_else(|| "local API response length is required".to_owned())?;
    while response.len().saturating_sub(header_end) < content_length {
        configure_proxy_timeouts(stream, deadline)?;
        let read = stream
            .read(&mut chunk)
            .map_err(|_| "could not read local API response".to_owned())?;
        if read == 0 {
            return Err("local API returned an incomplete response".to_owned());
        }
        if response.len().saturating_add(read) > header_end + content_length {
            return Err("local API returned invalid response framing".to_owned());
        }
        response.extend_from_slice(&chunk[..read]);
    }
    if response.len() != header_end + content_length {
        return Err("local API returned invalid response framing".to_owned());
    }
    Ok((status_code, response[header_end..].to_vec()))
}

fn endpoint() -> SocketAddr {
    SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), LOCAL_API_PORT)
}

fn status_for_inner(inner: &RuntimeInner) -> RuntimeStatus {
    let mut status = inner.status.clone();
    status.endpoint = inner.endpoint.to_string();
    status
}

enum HealthCheck {
    MatchesOwner,
    OwnershipMismatch,
    Unavailable,
    NotChecked,
}

fn should_stop_after_health_check(
    failure_count: &mut u8,
    health_check: HealthCheck,
    has_session_secret: bool,
) -> bool {
    match health_check {
        HealthCheck::MatchesOwner => {
            *failure_count = 0;
            !has_session_secret
        }
        HealthCheck::OwnershipMismatch => true,
        HealthCheck::Unavailable => {
            *failure_count = failure_count.saturating_add(1);
            *failure_count >= HEALTH_FAILURE_THRESHOLD
        }
        HealthCheck::NotChecked => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn runtime_status_never_serializes_a_runtime_secret() {
        let serialized = serde_json::to_string(&RuntimeStatus::ready()).expect("status serializes");

        assert!(!serialized.contains("secret"));
        assert!(!serialized.contains("nonce"));
        assert!(!serialized.contains("pid"));
    }

    #[test]
    fn response_parser_rejects_bytes_beyond_content_length() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("bind parser server");
        let address = listener.local_addr().expect("parser server address");
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept parser client");
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}extra")
                .expect("write malformed response");
        });
        let mut stream = TcpStream::connect(address).expect("connect parser client");

        assert!(read_http_response(&mut stream, Instant::now() + Duration::from_secs(1)).is_err());
        server.join().expect("parser server joins");
    }

    #[test]
    fn local_api_proxy_uses_a_fixed_allowlist_without_path_forwarding() {
        let event_split = resolve_api_operation("event_split").expect("allowlisted operation");

        assert_eq!(event_split.method, "POST");
        assert_eq!(event_split.path, "/events/split");
        assert!(event_split.requires_payload);
        assert!(resolve_api_operation("/data/delete").is_none());
        assert!(resolve_api_operation("unknown_operation").is_none());
    }

    #[test]
    fn project_scoped_operations_extract_only_a_canonical_project_id() {
        let project_id = "b01eecad-1e18-5e88-bf34-8e8e8358cfcb";
        let mut payload = json!({
            "project_id": project_id,
            "expected_revision": 4,
        });

        assert!(operation_requires_project("events_page"));
        assert!(!operation_requires_project("projects"));
        assert!(!operation_requires_project("project_create"));
        assert_eq!(take_project_id(&mut payload).expect("project context"), project_id);
        assert_eq!(payload, json!({"expected_revision": 4}));
        assert!(take_project_id(&mut json!({
            "project_id": "B01EECAD-1E18-5E88-BF34-8E8E8358CFCB"
        }))
        .is_err());
    }

    #[test]
    fn local_api_proxy_does_not_allow_path_based_file_operations() {
        assert!(resolve_api_operation("import_csv").is_none());
        assert!(resolve_api_operation("import_json").is_none());
        assert!(resolve_api_operation("export_save").is_none());
    }

    #[test]
    fn local_api_proxy_keeps_the_session_token_inside_rust_headers() {
        let listener =
            TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("bind local proxy server");
        let address = listener.local_addr().expect("read proxy server address");
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept proxy request");
            let mut request = [0_u8; 2048];
            let read = stream.read(&mut request).expect("read proxy request");
            let request = std::str::from_utf8(&request[..read]).expect("request is text");
            assert!(request.starts_with("GET /runtime/health HTTP/1.1"));
            assert!(!request.contains("rust-only-token"));
            let challenge = request
                .lines()
                .find_map(|line| line.strip_prefix("X-OpsMineFlow-Runtime-Probe-Challenge: "))
                .expect("probe challenge");
            let proof = runtime_probe_proof("rust-only-probe", challenge).expect("probe proof");
            let health = format!(
                r#"{{"status":"ok","bind":"127.0.0.1","local_only":true,"runtime":{{"nonce":"owner-nonce","pid":4242,"proof":"{proof}"}}}}"#
            );
            stream
                .write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{}",
                        health.len(),
                        health
                    )
                    .as_bytes(),
                )
                .expect("write health response");
            let mut proxied_request = [0_u8; 2048];
            let read = stream
                .read(&mut proxied_request)
                .expect("read proxied request");
            let request = std::str::from_utf8(&proxied_request[..read]).expect("request is text");
            assert!(request.starts_with("GET /events HTTP/1.1"));
            assert!(request.contains("X-OpsMineFlow-Api-Session: rust-only-token"));
            let body = r#"{"events":[]}"#;
            stream
                .write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                        body.len(),
                        body
                    )
                    .as_bytes(),
                )
                .expect("write proxy response");
        });

        let response = send_local_api_request_at(
            "rust-only-token",
            "rust-only-probe",
            address,
            "GET",
            "/events",
            None,
            None,
        )
        .expect("proxy response parses");
        server.join().expect("proxy server joins");

        assert_eq!(response, json!({"events": []}));
        assert!(!serde_json::to_string(&response)
            .expect("response serializes")
            .contains("rust-only-token"));
    }

    #[test]
    fn runtime_status_uses_a_loopback_endpoint() {
        let status = RuntimeStatus::ready();

        assert_eq!(status.endpoint, "127.0.0.1:8765");
        assert_eq!(status.recovery_action, "none");
    }

    #[test]
    fn runtime_marker_does_not_persist_a_probe_secret() {
        let serialized = serde_json::to_string(&RuntimeMarker {
            pid: 42,
            nonce: "owner-nonce".to_owned(),
        })
        .expect("serialize marker");

        assert!(serialized.contains("owner-nonce"));
        assert!(!serialized.contains("secret"));
    }

    #[test]
    fn import_scope_uses_a_private_snapshot_after_the_source_is_replaced() {
        let root = std::env::temp_dir().join(format!(
            "opsmineflow-import-snapshot-test-{}",
            random_hex(8).expect("entropy")
        ));
        let staging_dir = root.join("staging");
        fs::create_dir_all(&staging_dir).expect("create staging directory");
        fs::set_permissions(&staging_dir, fs::Permissions::from_mode(0o700))
            .expect("secure staging directory");
        let source_path = root.join("selected.csv");
        fs::write(&source_path, b"timestamp_start,activity\n2026-01-01T00:00:00+00:00,original\n")
            .expect("write source");

        let staged = stage_selected_import_file(source_path.clone(), "csv", &staging_dir)
            .expect("stage selected file");
        fs::write(&source_path, b"timestamp_start,activity\n2026-01-01T00:00:00+00:00,replaced\n")
            .expect("replace source");
        let scope = FileScope {
            snapshot_path: staged.path.clone(),
            format: "csv".to_owned(),
            byte_len: staged.byte_len,
            sha256: staged.sha256,
            expires_at: Instant::now() + FILE_SCOPE_TTL,
        };

        validate_staged_import_file(&scope).expect("verify staged snapshot");
        assert!(String::from_utf8(fs::read(&staged.path).expect("read snapshot"))
            .expect("snapshot is text")
            .contains("original"));
        assert!(!String::from_utf8(fs::read(&staged.path).expect("read snapshot"))
            .expect("snapshot is text")
            .contains("replaced"));

        fs::remove_dir_all(&root).expect("remove test directory");
    }

    #[test]
    fn import_scope_detects_a_tampered_snapshot() {
        let root = std::env::temp_dir().join(format!(
            "opsmineflow-import-tamper-test-{}",
            random_hex(8).expect("entropy")
        ));
        let staging_dir = root.join("staging");
        fs::create_dir_all(&staging_dir).expect("create staging directory");
        let source_path = root.join("selected.json");
        fs::write(&source_path, b"[]").expect("write source");
        let staged = stage_selected_import_file(source_path, "json", &staging_dir)
            .expect("stage selected file");
        fs::set_permissions(&staged.path, fs::Permissions::from_mode(0o600))
            .expect("allow test mutation");
        fs::write(&staged.path, b"[{}]").expect("tamper staged file");
        let scope = FileScope {
            snapshot_path: staged.path,
            format: "json".to_owned(),
            byte_len: staged.byte_len,
            sha256: staged.sha256,
            expires_at: Instant::now() + FILE_SCOPE_TTL,
        };

        assert!(validate_staged_import_file(&scope).is_err());

        fs::remove_dir_all(&root).expect("remove test directory");
    }

    #[test]
    fn import_selection_rejects_a_symlink_and_an_oversized_file() {
        use std::os::unix::fs::symlink;

        let root = std::env::temp_dir().join(format!(
            "opsmineflow-import-validation-test-{}",
            random_hex(8).expect("entropy")
        ));
        fs::create_dir_all(&root).expect("create test directory");
        let source_path = root.join("source.csv");
        fs::write(&source_path, b"timestamp_start,activity\n").expect("write source");
        let symlink_path = root.join("linked.csv");
        symlink(&source_path, &symlink_path).expect("create symlink");
        let oversized_path = root.join("oversized.csv");
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&oversized_path)
            .expect("create oversized file")
            .set_len(100 * 1024 * 1024 + 1)
            .expect("grow sparse file");

        assert!(open_selected_import_file(&symlink_path, "csv").is_err());
        assert!(open_selected_import_file(&oversized_path, "csv").is_err());

        fs::remove_dir_all(&root).expect("remove test directory");
    }

    #[test]
    fn export_target_uses_the_opened_directory_after_the_visible_path_is_replaced() {
        use std::os::unix::fs::symlink;

        let root = std::env::temp_dir().join(format!(
            "opsmineflow-export-directory-test-{}",
            random_hex(8).expect("entropy")
        ));
        let selected_parent = root.join("selected");
        let moved_parent = root.join("selected-original");
        let elsewhere = root.join("elsewhere");
        fs::create_dir_all(&selected_parent).expect("create selected directory");
        fs::create_dir_all(&elsewhere).expect("create alternate directory");
        let target = open_selected_export_target(selected_parent.join("flow.md"), "md")
            .expect("open selected target");
        let staged = root.join("prepared.md");
        fs::write(&staged, b"verified export").expect("write staged export");

        fs::rename(&selected_parent, &moved_parent).expect("move selected directory");
        symlink(&elsewhere, &selected_parent).expect("replace visible path with link");
        move_staged_export(&staged, &target, false).expect("write through retained directory fd");

        assert_eq!(
            fs::read(moved_parent.join("flow.md")).expect("read selected export"),
            b"verified export"
        );
        assert!(!elsewhere.join("flow.md").exists());

        fs::remove_dir_all(&root).expect("remove test directory");
    }

    #[test]
    fn export_target_rejects_a_symlinked_or_directory_filename() {
        use std::os::unix::fs::symlink;

        let root = std::env::temp_dir().join(format!(
            "opsmineflow-export-filename-test-{}",
            random_hex(8).expect("entropy")
        ));
        fs::create_dir_all(&root).expect("create test directory");
        let directory_target = root.join("existing.md");
        fs::create_dir_all(&directory_target).expect("create directory target");
        let linked_target = root.join("linked.md");
        symlink(&directory_target, &linked_target).expect("create directory symlink");

        assert!(open_selected_export_target(directory_target, "md").is_err());
        assert!(open_selected_export_target(linked_target, "md").is_err());

        fs::remove_dir_all(&root).expect("remove test directory");
    }

    #[test]
    fn runtime_cleanup_removes_unconsumed_import_snapshots() {
        let root = std::env::temp_dir().join(format!(
            "opsmineflow-import-cleanup-test-{}",
            random_hex(8).expect("entropy")
        ));
        fs::create_dir_all(&root).expect("create test directory");
        let snapshot_path = root.join("selected.csv");
        fs::write(&snapshot_path, b"timestamp_start,activity\n").expect("write snapshot");
        let mut scopes = HashMap::from([(
            "scope".to_owned(),
            FileScope {
                snapshot_path: snapshot_path.clone(),
                format: "csv".to_owned(),
                byte_len: 0,
                sha256: String::new(),
                expires_at: Instant::now() + FILE_SCOPE_TTL,
            },
        )]);

        clear_file_scopes(&mut scopes);

        assert!(scopes.is_empty());
        assert!(!snapshot_path.exists());
        fs::remove_dir_all(&root).expect("remove test directory");
    }

    #[test]
    fn successful_delete_clears_scopes_and_both_private_staging_directories() {
        let root = std::env::temp_dir().join(format!(
            "opsmineflow-delete-transient-data-test-{}",
            random_hex(8).expect("entropy")
        ));
        let import_staging_dir = root.join("import-staging");
        let export_staging_dir = root.join("export-staging");
        for directory in [&root, &import_staging_dir, &export_staging_dir] {
            fs::create_dir_all(directory).expect("create runtime directory");
            fs::set_permissions(directory, fs::Permissions::from_mode(0o700))
                .expect("secure runtime directory");
        }
        let snapshot_path = import_staging_dir.join("selected.csv");
        let export_path = export_staging_dir.join("prepared.md");
        fs::write(&snapshot_path, b"timestamp_start,activity\n").expect("write snapshot");
        fs::write(&export_path, b"prepared export").expect("write export");
        let state = RuntimeState::default();
        {
            let mut inner = state.inner.lock().expect("runtime lock");
            inner.paths = Some(RuntimePaths {
                log_dir: root.join("logs"),
                data_dir: root.join("data"),
                import_staging_dir: import_staging_dir.clone(),
                export_staging_dir: export_staging_dir.clone(),
                marker_path: root.join("sidecar-owner.json"),
            });
            inner.file_scopes.insert(
                "scope".to_owned(),
                FileScope {
                    snapshot_path: snapshot_path.clone(),
                    format: "csv".to_owned(),
                    byte_len: 0,
                    sha256: String::new(),
                    expires_at: Instant::now() + FILE_SCOPE_TTL,
                },
            );
        }

        state
            .clear_transient_file_data_after_delete()
            .expect("clear transient data after delete");

        assert!(!snapshot_path.exists());
        assert!(!export_path.exists());
        assert!(state.inner.lock().expect("runtime lock").file_scopes.is_empty());
        fs::remove_dir_all(&root).expect("remove test directory");
    }

    #[test]
    fn startup_staging_sweep_removes_regular_files_and_rejects_links() {
        use std::os::unix::fs::symlink;

        let root = std::env::temp_dir().join(format!(
            "opsmineflow-staging-sweep-test-{}",
            random_hex(8).expect("entropy")
        ));
        fs::create_dir_all(&root).expect("create test directory");
        let stale_path = root.join("stale.json");
        fs::write(&stale_path, b"{}").expect("write stale artifact");

        clean_private_staging_directory(&root).expect("sweep stale artifact");
        assert!(!stale_path.exists());

        let target = root.with_file_name(format!(
            "opsmineflow-staging-sweep-target-{}",
            random_hex(8).expect("entropy")
        ));
        fs::write(&target, b"do not follow").expect("write target");
        symlink(&target, root.join("unexpected-link")).expect("create unexpected link");
        assert!(clean_private_staging_directory(&root).is_err());
        assert!(target.exists());

        fs::remove_dir_all(&root).expect("remove test directory");
        fs::remove_file(&target).expect("remove target");
    }

    #[test]
    fn private_runtime_directory_rejects_a_preexisting_symlink() {
        use std::os::unix::fs::symlink;

        let root = std::env::temp_dir().join(format!(
            "opsmineflow-private-directory-test-{}",
            random_hex(8).expect("entropy")
        ));
        let target = root.with_file_name(format!(
            "opsmineflow-private-directory-target-{}",
            random_hex(8).expect("entropy")
        ));
        fs::create_dir_all(&target).expect("create target directory");
        let protected_file = target.join("keep.txt");
        fs::write(&protected_file, b"do not clean").expect("write protected file");
        symlink(&target, &root).expect("create runtime directory symlink");

        assert!(create_private_directory(&root).is_err());
        assert!(protected_file.exists());

        fs::remove_file(&root).expect("remove symlink");
        fs::remove_dir_all(&target).expect("remove target directory");
    }

    #[test]
    fn local_listener_is_detected_as_a_port_collision_candidate() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("bind loopback listener");

        assert!(endpoint_is_open_at(
            listener.local_addr().expect("listener address")
        ));
    }

    #[test]
    fn runtime_health_identity_requires_the_constant_time_payload() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("bind loopback listener");
        let address = listener.local_addr().expect("listener address");
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept health request");
            let mut request = [0_u8; 512];
            let read = stream.read(&mut request).expect("read health request");
            let request = std::str::from_utf8(&request[..read]).expect("health request is text");
            assert!(request.contains("Host: 127.0.0.1:8765"));
            let challenge = request
                .lines()
                .find_map(|line| line.strip_prefix("X-OpsMineFlow-Runtime-Probe-Challenge: "))
                .expect("probe challenge");
            let proof = runtime_probe_proof("health-probe", challenge).expect("probe proof");
            let body = format!(
                r#"{{"status":"ok","bind":"127.0.0.1","local_only":true,"runtime":{{"nonce":"owner-nonce","pid":4242,"proof":"{proof}"}}}}"#
            );
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            stream
                .write_all(response.as_bytes())
                .expect("write health response");
        });

        let identity =
            read_health_identity_at(address, "health-probe").expect("parse runtime identity");
        server.join().expect("health server joins");

        assert_eq!(identity.nonce, "owner-nonce");
        assert_eq!(identity.pid, 4242);
    }

    #[test]
    fn unexpected_child_exit_revokes_the_runtime_state() {
        let state = RuntimeState::default();
        let child = Command::new("/usr/bin/true")
            .spawn()
            .expect("start short child");
        thread::sleep(Duration::from_millis(50));
        {
            let mut inner = state.inner.lock().expect("runtime lock");
            inner.child = Some(child);
            inner.runtime_nonce = Some("owner-nonce".to_owned());
            inner.session_secret = Some(SessionSecret("session-secret".to_owned()));
            inner.runtime_probe_secret = Some(RuntimeProbeSecret("probe-secret".to_owned()));
            inner.status = RuntimeStatus::ready();
        }

        let status = state.status();

        assert_eq!(status.state, "unavailable");
        assert_eq!(status.recovery_action, "restart");
    }

    #[test]
    fn transient_health_failures_do_not_stop_a_live_sidecar() {
        let state = RuntimeState::default();
        let child = Command::new("/bin/sh")
            .args(["-c", "trap 'exit 0' TERM; while :; do sleep 1; done"])
            .spawn()
            .expect("start long-running child");
        {
            let mut inner = state.inner.lock().expect("runtime lock");
            inner.child = Some(child);
            inner.runtime_nonce = Some("owner-nonce".to_owned());
            inner.session_secret = Some(SessionSecret("session-secret".to_owned()));
            inner.runtime_probe_secret = Some(RuntimeProbeSecret("probe-secret".to_owned()));
            inner.status = RuntimeStatus::ready();
        }

        let first = state.status();
        let second = state.status();
        let third = state.status();

        assert_eq!(first.state, "ready");
        assert_eq!(second.state, "ready");
        assert_eq!(third.state, "unavailable");
    }

    #[test]
    fn successful_health_check_resets_transient_failure_count() {
        let mut failure_count = HEALTH_FAILURE_THRESHOLD - 1;

        assert!(!should_stop_after_health_check(
            &mut failure_count,
            HealthCheck::MatchesOwner,
            true,
        ));
        assert_eq!(failure_count, 0);
        assert!(!should_stop_after_health_check(
            &mut failure_count,
            HealthCheck::Unavailable,
            true,
        ));
        assert_eq!(failure_count, 1);
    }

    #[test]
    fn unverifiable_orphan_marker_is_retained_for_safe_recovery() {
        let root = std::env::temp_dir().join(format!(
            "opsmineflow-runtime-marker-test-{}",
            random_hex(8).expect("entropy")
        ));
        fs::create_dir_all(&root).expect("create test directory");
        let marker_path = root.join("sidecar-owner.json");
        fs::write(&marker_path, b"not valid JSON").expect("write corrupt marker");
        let paths = RuntimePaths {
            log_dir: root.join("logs"),
            data_dir: root.join("data"),
            import_staging_dir: root.join("import-staging"),
            export_staging_dir: root.join("export-staging"),
            marker_path: marker_path.clone(),
        };

        assert!(recover_owned_orphan(&paths).is_err());
        assert!(marker_path.exists());

        fs::remove_dir_all(&root).expect("remove test directory");
    }

    #[test]
    fn owned_orphan_waits_for_a_self_terminating_sidecar() {
        let mut child = Command::new("/usr/bin/true")
            .spawn()
            .expect("start short child");
        let pid = child.id();
        child.wait().expect("reap short child");

        assert!(wait_for_orphan_to_exit(pid).expect("wait for child exit"));
    }

    #[test]
    fn quarantining_unverified_marker_preserves_the_original_record() {
        let root = std::env::temp_dir().join(format!(
            "opsmineflow-runtime-quarantine-test-{}",
            random_hex(8).expect("entropy")
        ));
        fs::create_dir_all(&root).expect("create test directory");
        let marker_path = root.join("sidecar-owner.json");
        fs::write(&marker_path, b"not valid JSON").expect("write corrupt marker");

        let quarantined_path = quarantine_marker(&marker_path).expect("quarantine marker");

        assert!(!marker_path.exists());
        assert_eq!(
            fs::read(&quarantined_path).expect("read quarantined marker"),
            b"not valid JSON"
        );

        fs::remove_dir_all(&root).expect("remove test directory");
    }

    #[test]
    fn repair_refuses_to_follow_a_marker_symlink() {
        use std::os::unix::fs::symlink;

        let root = std::env::temp_dir().join(format!(
            "opsmineflow-runtime-symlink-test-{}",
            random_hex(8).expect("entropy")
        ));
        fs::create_dir_all(&root).expect("create test directory");
        let target_path = root.join("unrelated.json");
        let marker_path = root.join("sidecar-owner.json");
        fs::write(&target_path, b"not valid JSON").expect("write target file");
        symlink(&target_path, &marker_path).expect("create marker symlink");

        assert!(quarantine_marker(&marker_path).is_err());
        assert!(marker_path.is_symlink());
        assert_eq!(
            fs::read(&target_path).expect("read target file"),
            b"not valid JSON"
        );

        fs::remove_dir_all(&root).expect("remove test directory");
    }

    #[test]
    fn current_process_is_reported_as_existing() {
        assert!(pid_exists(std::process::id()).expect("check current process"));
    }

    #[test]
    fn graceful_stop_waits_for_the_child_to_exit() {
        let mut child = Command::new("/bin/sh")
            .args(["-c", "trap 'exit 0' TERM; while :; do sleep 1; done"])
            .spawn()
            .expect("start long-running child");

        graceful_stop(&mut child).expect("graceful child shutdown");

        assert!(child.try_wait().expect("read child status").is_some());
    }

    #[test]
    fn packaged_sidecar_checksum_rejects_modified_contents() {
        let root = std::env::temp_dir().join(format!(
            "opsmineflow-runtime-test-{}",
            random_hex(8).expect("entropy")
        ));
        fs::create_dir_all(&root).expect("create test resource directory");
        let program = root.join("opsmineflow-local-api");
        let manifest = root.join("opsmineflow-local-api.sha256");
        fs::write(&program, b"trusted sidecar").expect("write sidecar");
        fs::set_permissions(&program, fs::Permissions::from_mode(0o755))
            .expect("secure sidecar permissions");
        let checksum = sha256_file(&program).expect("hash sidecar");
        fs::write(&manifest, format!("{checksum}  opsmineflow-local-api\n"))
            .expect("write manifest");
        fs::set_permissions(&manifest, fs::Permissions::from_mode(0o644))
            .expect("secure manifest permissions");

        assert!(verify_packaged_sidecar(&root, &program, &manifest));
        fs::write(&program, b"modified sidecar").expect("modify sidecar");
        assert!(!verify_packaged_sidecar(&root, &program, &manifest));

        fs::remove_dir_all(&root).expect("remove test resource directory");
    }
}
