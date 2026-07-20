from __future__ import annotations

import atexit
import os
import platform
import secrets
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opsmineflow_mining import build_native_app_event

from .child_process import recording_agent_environment as _recording_agent_environment
from .child_process import sanitized_subprocess_environment
from .storage import EventStore, StorageCommitError, default_data_dir, default_store

TOKEN_TTL_SECONDS = 12 * 60 * 60
INGEST_RATE_WINDOW_SECONDS = 60
INGEST_RATE_LIMIT = 240


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def native_event_from_payload(payload: dict[str, Any], session: dict[str, Any]):
    required = ("session_id", "sequence", "app_name", "timestamp_start", "timestamp_end", "duration_seconds")
    missing = [name for name in required if payload.get(name) in (None, "")]
    if missing:
        raise ValueError(f"Recording event is missing: {', '.join(missing)}")
    if str(payload["session_id"]) != str(session["session_id"]):
        raise ValueError("Recording session does not match the active session.")
    app_name = str(payload["app_name"]).strip()
    if not app_name or len(app_name) > 200:
        raise ValueError("Frontmost application name is invalid.")
    sequence = int(payload["sequence"])
    if sequence < 1:
        raise ValueError("Recording event sequence is invalid.")
    duration = float(payload["duration_seconds"])
    if duration < 0 or duration > 86400:
        raise ValueError("Recording event duration is invalid.")
    return build_native_app_event(
        session_id=str(session["session_id"]),
        sequence=int(payload["sequence"]),
        case_id=str(session["case_id"]),
        activity=f"{session['activity_label']} / {app_name}",
        app_name=app_name,
        app_bundle_id=str(payload.get("app_bundle_id") or "")[:300],
        timestamp_start=str(payload["timestamp_start"]),
        timestamp_end=str(payload["timestamp_end"]),
        duration_seconds=duration,
    )


class RecordingManager:
    def __init__(self, agent_path: Path | None = None, platform_name: str | None = None) -> None:
        root_dir = Path(__file__).resolve().parents[4]
        self.agent_path = agent_path or root_dir / "mac-agent" / "bin" / "opsmineflow-agent"
        self.platform_name = platform_name or platform.system()
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: Any = None
        self._token = ""
        self._token_issued_at = 0.0
        self._stop_file: Path | None = None
        self._session: dict[str, Any] | None = None
        self._last_error = ""
        self._seen_sequences: set[int] = set()
        self._recent_ingest_times: list[float] = []
        self._agent_version_cache = ""
        self._agent_version_mtime = 0.0

    def availability(self) -> dict[str, Any]:
        supported = self.platform_name == "Darwin"
        installed = self.agent_path.is_file() and os.access(self.agent_path, os.X_OK)
        remediation = ""
        if not supported:
            remediation = "Native recording is available only on macOS."
        elif not installed:
            remediation = "Run ./scripts/install_mac.sh to build the local macOS recording agent."
        return {
            "supported": supported,
            "installed": installed,
            "available": supported and installed,
            "remediation": remediation,
            "agent_path": str(self.agent_path),
            "agent_version": self._agent_version() if supported and installed else "",
            "log_path": str(default_data_dir() / "recording-agent.log"),
            "token_ttl_seconds": TOKEN_TTL_SECONDS,
            "rate_limit_per_minute": INGEST_RATE_LIMIT,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_process_state()
            availability = self.availability()
            if self._session is None:
                return {
                    **availability,
                    "active": False,
                    "paused": False,
                    "session_id": "",
                    "case_id": "",
                    "activity_label": "",
                    "started_at": "",
                    "paused_at": "",
                    "pause_reason": "",
                    "pause_intervals": [],
                    "capture_ended": False,
                    "current_app": "",
                    "recorded_events": 0,
                    "last_heartbeat_at": "",
                    "last_error": self._last_error,
                    "capture_scope": "frontmost_app_only",
                }
            return {**availability, **self._session, "last_error": self._last_error, "capture_scope": "frontmost_app_only"}

    def start(self, case_id: str, activity_label: str, consent: bool) -> dict[str, Any]:
        with self._lock:
            self._refresh_process_state()
            if self._session and self._session.get("active"):
                raise ValueError("A recording session is already active.")
            if not consent:
                raise ValueError("Explicit recording consent is required.")
            normalized_case = case_id.strip()
            normalized_activity = activity_label.strip()
            if not normalized_case or not normalized_activity:
                raise ValueError("Case name and work label are required.")
            availability = self.availability()
            if not availability["available"]:
                raise RuntimeError(str(availability["remediation"]))

            session_id = f"rec_{uuid.uuid4().hex}"
            runtime_dir = default_data_dir() / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            stop_file = runtime_dir / f"{session_id}.stop"
            stop_file.unlink(missing_ok=True)
            log_path = default_data_dir() / "recording-agent.log"
            self._log_handle = log_path.open("ab")
            self._token = secrets.token_urlsafe(32)
            self._token_issued_at = time.monotonic()
            self._seen_sequences = set()
            self._recent_ingest_times = []
            environment = _recording_agent_environment(self._token)
            api_port = os.environ.get("OPSMINEFLOW_API_PORT", "8765")
            self._process = subprocess.Popen(
                [
                    str(self.agent_path),
                    "--api-port", api_port,
                    "--session-id", session_id,
                    "--stop-file", str(stop_file),
                    "--parent-pid", str(os.getpid()),
                    "--interval", "2",
                ],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=self._log_handle,
            )
            self._stop_file = stop_file
            self._last_error = ""
            self._session = {
                "active": True,
                "session_id": session_id,
                "case_id": normalized_case[:200],
                "activity_label": normalized_activity[:200],
                "started_at": _now_iso(),
                "paused": False,
                "paused_at": "",
                    "pause_reason": "",
                    "pause_intervals": [],
                    "capture_ended": False,
                    "current_app": "",
                "recorded_events": 0,
                "last_heartbeat_at": "",
            }
            return self.status()

    def pause(self, reason: str = "") -> dict[str, Any]:
        with self._lock:
            self._refresh_process_state()
            if self._session is None or not self._session.get("active"):
                raise ValueError("No recording session is active.")
            if self._session.get("paused"):
                return self.status()
            self._session["paused"] = True
            self._session["paused_at"] = _now_iso()
            self._session["pause_reason"] = reason.strip()[:200] or "manual_pause"
            self._session["current_app"] = ""
            return self.status()

    def resume(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_process_state()
            if self._session is None or not self._session.get("active"):
                raise ValueError("No recording session is active.")
            if not self._session.get("paused"):
                return self.status()
            self._close_pause_interval()
            return self.status()

    def heartbeat(self, token: str, session_id: str, current_app: str) -> dict[str, Any]:
        with self._lock:
            self._authorize(token, session_id)
            assert self._session is not None
            self._session["current_app"] = "" if self._session.get("paused") else current_app.strip()[:200]
            self._session["last_heartbeat_at"] = _now_iso()
            return {"accepted": True}

    def ingest(self, token: str, payload: dict[str, Any], store: EventStore | None = None) -> dict[str, Any]:
        with self._lock:
            session_id = str(payload.get("session_id") or "")
            self._authorize(token, session_id)
            accepted_at = self._check_rate_limit()
            assert self._session is not None
            sequence = int(payload.get("sequence") or 0)
            if sequence in self._seen_sequences:
                raise ValueError("Recording event sequence was already accepted.")
            if self._session.get("paused"):
                self._seen_sequences.add(sequence)
                self._record_rate_acceptance(accepted_at)
                self._session["last_heartbeat_at"] = _now_iso()
                return {"accepted": True, "appended": 0, "paused": True, "event_id": ""}
            event = native_event_from_payload(payload, self._session)
            active_store = store or default_store()
            appended = active_store.append([event])
            # Storage may reject a write. Do not consume a sequence or rate
            # budget until its durable outcome is known, so a retry can replay.
            self._seen_sequences.add(sequence)
            self._record_rate_acceptance(accepted_at)
            self._session["recorded_events"] = int(self._session["recorded_events"]) + appended
            self._session["current_app"] = event.app_name
            return {"accepted": True, "appended": appended, "event_id": event.event_id}

    def stop(self, store: EventStore | None = None, *, record_import: bool = True) -> dict[str, Any]:
        with self._lock:
            self._refresh_process_state()
            if self._session is None or not self._session.get("active"):
                return self.status()
            process = self._process
            if self._stop_file is not None:
                self._stop_file.touch(exist_ok=True)
        if process is not None:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        with self._lock:
            if self._session is None:
                return self.status()
            if self._session.get("paused"):
                self._close_pause_interval()
            self._session["capture_ended"] = True
            recorded_events = int(self._session.get("recorded_events", 0))
            if record_import and recorded_events > 0:
                try:
                    (store or default_store()).record_import(
                        "native_recording",
                        str(self._session["case_id"]),
                        recorded_events,
                        operation_id=str(self._session["session_id"]),
                    )
                except StorageCommitError:
                    self._last_error = "Recording audit could not be saved. Retry stopping the session."
                    raise
            self._session["active"] = False
            self._session["current_app"] = ""
            self._cleanup_process()
            return self.status()

    def shutdown(self) -> None:
        try:
            self.stop()
        except Exception:
            pass

    def _authorize(self, token: str, session_id: str) -> None:
        if not self._session or not self._session.get("active"):
            raise PermissionError("No recording session is active.")
        if not token or not secrets.compare_digest(token, self._token):
            raise PermissionError("Recording session token is invalid.")
        if session_id != self._session.get("session_id"):
            raise PermissionError("Recording session is invalid.")
        if self._token_issued_at and time.monotonic() - self._token_issued_at > TOKEN_TTL_SECONDS:
            self._last_error = "Recording session token expired."
            raise PermissionError("Recording session token expired.")

    def _check_rate_limit(self) -> float:
        now = time.monotonic()
        cutoff = now - INGEST_RATE_WINDOW_SECONDS
        self._recent_ingest_times = [item for item in self._recent_ingest_times if item >= cutoff]
        if len(self._recent_ingest_times) >= INGEST_RATE_LIMIT:
            self._last_error = "Recording event rate limit exceeded."
            raise PermissionError("Recording event rate limit exceeded.")
        return now

    def _record_rate_acceptance(self, accepted_at: float) -> None:
        self._recent_ingest_times.append(accepted_at)

    def _refresh_process_state(self) -> None:
        if self._process is None or self._process.poll() is None:
            return
        if self._session and self._session.get("active"):
            # Keep the session finalizable. The agent may already be stopped
            # when its audit-history commit fails, and callers must be able to
            # retry ``stop`` without losing the durable event count.
            self._session["capture_ended"] = True
            self._session["current_app"] = ""
            if self._session.get("paused"):
                self._close_pause_interval()
            if self._process.returncode != 0:
                self._last_error = f"Recording agent exited with code {self._process.returncode}."
        self._cleanup_process()

    def _close_pause_interval(self) -> None:
        assert self._session is not None
        paused_at = str(self._session.get("paused_at") or "")
        if paused_at:
            intervals = self._session.setdefault("pause_intervals", [])
            if isinstance(intervals, list):
                intervals.append(
                    {
                        "started_at": paused_at,
                        "ended_at": _now_iso(),
                        "reason": str(self._session.get("pause_reason") or "manual_pause"),
                    }
                )
        self._session["paused"] = False
        self._session["paused_at"] = ""
        self._session["pause_reason"] = ""

    def _cleanup_process(self) -> None:
        self._process = None
        self._token = ""
        self._token_issued_at = 0.0
        self._recent_ingest_times = []
        if self._stop_file is not None:
            self._stop_file.unlink(missing_ok=True)
        self._stop_file = None
        if self._log_handle is not None:
            self._log_handle.close()
        self._log_handle = None

    def _agent_version(self) -> str:
        try:
            mtime = self.agent_path.stat().st_mtime
        except OSError:
            return ""
        if self._agent_version_cache and self._agent_version_mtime == mtime:
            return self._agent_version_cache
        try:
            result = subprocess.run(
                [str(self.agent_path), "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
                env=sanitized_subprocess_environment(),
            )
        except Exception:
            return "unknown"
        output = (result.stdout or result.stderr).strip()
        version = output if result.returncode == 0 and output else "unknown"
        self._agent_version_cache = version
        self._agent_version_mtime = mtime
        return version
recording_manager = RecordingManager()
atexit.register(recording_manager.shutdown)
