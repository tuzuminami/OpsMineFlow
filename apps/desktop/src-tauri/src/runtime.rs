use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Manager};

const LOCAL_API_HOST: &str = "127.0.0.1";
const LOCAL_API_PORT: u16 = 8765;
const READY_TIMEOUT: Duration = Duration::from_secs(5);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(3);
const HEALTH_FAILURE_THRESHOLD: u8 = 3;

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

pub struct RuntimeState {
    inner: Mutex<RuntimeInner>,
}

struct RuntimeInner {
    child: Option<Child>,
    paths: Option<RuntimePaths>,
    endpoint: SocketAddr,
    runtime_nonce: Option<String>,
    session_secret: Option<SessionSecret>,
    health_failure_count: u8,
    repair_in_progress: bool,
    status: RuntimeStatus,
}

struct SessionSecret(String);

#[derive(Clone)]
struct RuntimePaths {
    log_dir: PathBuf,
    data_dir: PathBuf,
    marker_path: PathBuf,
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
}

impl Default for RuntimeState {
    fn default() -> Self {
        Self {
            inner: Mutex::new(RuntimeInner {
                child: None,
                paths: None,
                endpoint: endpoint(),
                runtime_nonce: None,
                session_secret: None,
                health_failure_count: 0,
                repair_in_progress: false,
                status: RuntimeStatus::stopped(),
            }),
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
        let mut child = match spawn_sidecar(&sidecar, &paths, &nonce, &secret) {
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
        if !wait_for_ready(&mut child, &nonce) {
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
            inner.status.state.as_str(),
        ) {
            (Some(child), Some(nonce), "ready") => match read_health_identity() {
                Some(identity) if identity.pid == child.id() && identity.nonce == nonce => {
                    HealthCheck::MatchesOwner
                }
                Some(_) => HealthCheck::OwnershipMismatch,
                None => HealthCheck::Unavailable,
            },
            (_, _, "ready") => HealthCheck::OwnershipMismatch,
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
        inner.runtime_nonce = None;
        inner.health_failure_count = 0;
        inner.repair_in_progress = false;
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

    fn set_unavailable(&self, paths: RuntimePaths, recovery_action: &str) {
        if let Ok(mut inner) = self.inner.lock() {
            inner.paths = Some(paths);
            inner.runtime_nonce = None;
            inner.session_secret = None;
            inner.health_failure_count = 0;
            inner.status = RuntimeStatus::unavailable(recovery_action);
        }
    }

    fn set_unavailable_without_paths(&self, recovery_action: &str) {
        if let Ok(mut inner) = self.inner.lock() {
            inner.child = None;
            inner.runtime_nonce = None;
            inner.session_secret = None;
            inner.health_failure_count = 0;
            inner.status = RuntimeStatus::unavailable(recovery_action);
        }
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
        for directory in [&root, &runtime_dir, &log_dir, &data_dir] {
            create_private_directory(directory)?;
        }
        let marker_path = runtime_dir.join("sidecar-owner.json");
        verify_marker_writable(&marker_path)?;
        Ok(Self {
            marker_path,
            log_dir,
            data_dir,
        })
    }
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
    let identity = read_health_identity()
        .ok_or_else(|| "could not verify ownership of the previous local runtime".to_owned())?;
    if identity.pid != marker.pid || identity.nonce != marker.nonce {
        return Err("previous local runtime ownership does not match its marker".to_owned());
    }
    terminate_pid(marker.pid)?;
    wait_for_endpoint_to_close()?;
    if pid_exists(marker.pid)? {
        return Err("the previous local runtime did not exit after shutdown".to_owned());
    }
    remove_marker(&paths.marker_path)
}

fn wait_for_ready(child: &mut Child, nonce: &str) -> bool {
    let started_at = Instant::now();
    while started_at.elapsed() < READY_TIMEOUT {
        if let Some(identity) = read_health_identity() {
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

fn wait_for_endpoint_to_close() -> Result<(), String> {
    let started_at = Instant::now();
    while started_at.elapsed() < SHUTDOWN_TIMEOUT {
        if !endpoint_is_open() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(100));
    }
    Err("the previous local runtime did not stop in time".to_owned())
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

fn read_health_identity() -> Option<RuntimeIdentity> {
    read_health_identity_at(endpoint())
}

fn read_health_identity_at(address: SocketAddr) -> Option<RuntimeIdentity> {
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(250)).ok()?;
    stream
        .set_read_timeout(Some(Duration::from_millis(250)))
        .ok()?;
    stream
        .set_write_timeout(Some(Duration::from_millis(250)))
        .ok()?;
    stream
        .write_all(b"GET /runtime/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    let (_, body) = response.split_once("\r\n\r\n")?;
    let health: HealthPayload = serde_json::from_str(body).ok()?;
    if health.status != "ok" || health.bind != LOCAL_API_HOST || !health.local_only {
        return None;
    }
    health.runtime
}

fn write_marker(path: &Path, marker: &RuntimeMarker) -> Result<(), String> {
    let temporary_path = path.with_extension("json.tmp");
    fs::write(
        &temporary_path,
        serde_json::to_vec(marker).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    set_private_file_permissions(&temporary_path)?;
    fs::rename(&temporary_path, path).map_err(|error| error.to_string())
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
    fs::create_dir_all(path)
        .map_err(|error| format!("could not create runtime directory: {error}"))?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(|error| format!("could not secure runtime directory: {error}"))
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
    fn runtime_status_uses_a_loopback_endpoint() {
        let status = RuntimeStatus::ready();

        assert_eq!(status.endpoint, "127.0.0.1:8765");
        assert_eq!(status.recovery_action, "none");
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
            let _ = stream.read(&mut request);
            let body = r#"{"status":"ok","bind":"127.0.0.1","local_only":true,"runtime":{"nonce":"owner-nonce","pid":4242}}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            stream
                .write_all(response.as_bytes())
                .expect("write health response");
        });

        let identity = read_health_identity_at(address).expect("parse runtime identity");
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
            marker_path: marker_path.clone(),
        };

        assert!(recover_owned_orphan(&paths).is_err());
        assert!(marker_path.exists());

        fs::remove_dir_all(&root).expect("remove test directory");
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
