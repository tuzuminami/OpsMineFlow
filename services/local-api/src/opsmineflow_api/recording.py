from __future__ import annotations

import atexit
import os
import platform
import secrets
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opsmineflow_mining import build_native_app_event

from .storage import EventStore, default_data_dir, default_store


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
        self._stop_file: Path | None = None
        self._session: dict[str, Any] | None = None
        self._last_error = ""

    def availability(self) -> dict[str, Any]:
        supported = self.platform_name == "Darwin"
        installed = self.agent_path.is_file() and os.access(self.agent_path, os.X_OK)
        remediation = ""
        if not supported:
            remediation = "Native recording is available only on macOS."
        elif not installed:
            remediation = "Run ./scripts/install_mac.sh to build the local macOS recording agent."
        return {"supported": supported, "installed": installed, "available": supported and installed, "remediation": remediation}

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_process_state()
            availability = self.availability()
            if self._session is None:
                return {
                    **availability,
                    "active": False,
                    "session_id": "",
                    "case_id": "",
                    "activity_label": "",
                    "started_at": "",
                    "current_app": "",
                    "recorded_events": 0,
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
            environment = dict(os.environ)
            environment["OPSMINEFLOW_RECORDING_TOKEN"] = self._token
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
                "current_app": "",
                "recorded_events": 0,
            }
            return self.status()

    def heartbeat(self, token: str, session_id: str, current_app: str) -> dict[str, Any]:
        with self._lock:
            self._authorize(token, session_id)
            assert self._session is not None
            self._session["current_app"] = current_app.strip()[:200]
            return {"accepted": True}

    def ingest(self, token: str, payload: dict[str, Any], store: EventStore | None = None) -> dict[str, Any]:
        with self._lock:
            session_id = str(payload.get("session_id") or "")
            self._authorize(token, session_id)
            assert self._session is not None
            event = native_event_from_payload(payload, self._session)
            active_store = store or default_store()
            appended = active_store.append([event])
            self._session["recorded_events"] = int(self._session["recorded_events"]) + appended
            self._session["current_app"] = event.app_name
            return {"accepted": True, "appended": appended, "event_id": event.event_id}

    def stop(self, store: EventStore | None = None) -> dict[str, Any]:
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
            recorded_events = int(self._session.get("recorded_events", 0))
            if recorded_events > 0:
                (store or default_store()).record_import("native_recording", str(self._session["case_id"]), recorded_events)
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

    def _refresh_process_state(self) -> None:
        if self._process is None or self._process.poll() is None:
            return
        if self._session and self._session.get("active"):
            self._session["active"] = False
            self._session["current_app"] = ""
            if self._process.returncode != 0:
                self._last_error = f"Recording agent exited with code {self._process.returncode}."
        self._cleanup_process()

    def _cleanup_process(self) -> None:
        self._process = None
        self._token = ""
        if self._stop_file is not None:
            self._stop_file.unlink(missing_ok=True)
        self._stop_file = None
        if self._log_handle is not None:
            self._log_handle.close()
        self._log_handle = None


recording_manager = RecordingManager()
atexit.register(recording_manager.shutdown)
