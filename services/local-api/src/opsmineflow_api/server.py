from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .app import (
    DELETE_CHALLENGES,
    LOCAL_API_POLICY,
    allowed_webui_origins,
    create_api_snapshot,
    create_activitywatch_preview,
    create_app_switching,
    create_automation_candidates,
    create_diagnostics,
    create_event_quality_report,
    create_event_page,
    create_export_artifact,
    create_import_preview,
    create_markdown_report,
    create_process_map,
    create_public_health,
    create_runtime_health,
    create_summary,
    import_activitywatch_into_store,
    import_path_into_store,
    run_diagnostic_checks,
    save_export_artifact,
)
from .auth import (
    DELETE_CHALLENGE_HEADER,
    RUNTIME_PROBE_CHALLENGE_HEADER,
    LocalApiPolicy,
    RequestRejected,
)
from .recording import recording_manager
from .storage import default_store

HOST = os.environ.get("OPSMINEFLOW_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPSMINEFLOW_API_PORT", "8765"))


class LocalApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_OPTIONS(self) -> None:
        if not self._authorize_request():
            return
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if not self._authorize_request(path):
            return
        if path == "/health":
            self._send_json(create_public_health())
            return
        if path == "/runtime/health":
            self._send_json(create_runtime_health(self.headers.get(RUNTIME_PROBE_CHALLENGE_HEADER, "")))
            return
        if path == "/diagnostics":
            self._send_json(create_diagnostics())
            return
        if path == "/settings":
            self._send_json(default_store().get_settings())
            return
        if path == "/import/history":
            self._send_json(default_store().list_import_history())
            return
        if path == "/recording/status":
            self._send_json(recording_manager.status())
            return
        if path == "/events":
            self._send_json(create_event_page(0, 500)["events"])
            return
        if path == "/analytics/summary":
            self._send_json(create_summary())
            return
        if path == "/analytics/app-switching":
            self._send_json(create_app_switching())
            return
        if path == "/analytics/automation-candidates":
            self._send_json(create_automation_candidates())
            return
        if path == "/analytics/event-quality":
            self._send_json(create_event_quality_report())
            return
        if path == "/analytics/process-map":
            self._send_json(create_process_map())
            return
        if path == "/reports/markdown":
            self._send_json({"markdown": create_markdown_report()})
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._authorize_request(path):
            return
        if path == "/data/delete":
            self._read_json()
            if not DELETE_CHALLENGES.consume(self.headers.get(DELETE_CHALLENGE_HEADER, "")):
                self._send_json({"error": "delete challenge is invalid or expired"}, status=403)
                return
            recording_manager.stop(default_store())
            default_store().clear()
            self._send_json({"deleted": True})
            return
        try:
            payload = self._read_json()
            if path == "/recording/start":
                self._send_json(
                    recording_manager.start(
                        str(payload.get("case_id") or ""),
                        str(payload.get("activity_label") or ""),
                        bool(payload.get("consent")),
                    )
                )
                return
            if path == "/recording/stop":
                self._send_json(recording_manager.stop(default_store()))
                return
            if path == "/recording/pause":
                self._send_json(recording_manager.pause(str(payload.get("reason") or "")))
                return
            if path == "/recording/resume":
                self._send_json(recording_manager.resume())
                return
            if path == "/recording/events":
                self._send_json(
                    recording_manager.ingest(
                        self.headers.get("X-OpsMineFlow-Session", ""),
                        payload,
                        default_store(),
                    )
                )
                return
            if path == "/recording/heartbeat":
                self._send_json(
                    recording_manager.heartbeat(
                        self.headers.get("X-OpsMineFlow-Session", ""),
                        str(payload.get("session_id") or ""),
                        str(payload.get("current_app") or ""),
                    )
                )
                return
            if path == "/events/page":
                self._send_json(
                    create_event_page(
                        int(payload.get("offset") or 0),
                        int(payload.get("limit") or 250),
                    )
                )
                return
            if path == "/import/preview":
                self._send_json(
                    create_import_preview(
                        str(payload.get("format") or ""),
                        str(payload.get("path") or ""),
                        payload.get("mapping") if isinstance(payload.get("mapping"), dict) else None,
                        str(payload.get("date_format") or ""),
                        str(payload.get("timezone") or "UTC"),
                    )
                )
                return
            if path == "/import/activitywatch-preview":
                self._send_json(
                    create_activitywatch_preview(
                        bool(payload.get("enabled")),
                        str(payload.get("base_url") or "http://127.0.0.1:5600"),
                    )
                )
                return
            if path == "/import/csv":
                self._send_json(
                    import_path_into_store(
                        "csv",
                        str(payload.get("path") or ""),
                        mapping=payload.get("mapping") if isinstance(payload.get("mapping"), dict) else None,
                        date_format=str(payload.get("date_format") or ""),
                        timezone_name=str(payload.get("timezone") or "UTC"),
                    )
                )
                return
            if path == "/import/json":
                self._send_json(import_path_into_store("json", str(payload.get("path") or "")))
                return
            if path == "/import/activitywatch-local":
                self._send_json(
                    import_activitywatch_into_store(
                        bool(payload.get("enabled")),
                        str(payload.get("base_url") or "http://127.0.0.1:5600"),
                        str(payload.get("mode") or "replace"),
                    )
                )
                return
            if path == "/events/label":
                try:
                    default_store().set_label(str(payload.get("event_id") or ""), str(payload.get("label") or ""))
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                    return
                self._send_json({"event_id": payload.get("event_id"), "label": payload.get("label")})
                return
            if path == "/events/activity":
                try:
                    event = default_store().update_event_activity(
                        str(payload.get("event_id") or ""),
                        str(payload.get("activity") or ""),
                    )
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                    return
                self._send_json({"event": event})
                return
            if path == "/events/exclude":
                try:
                    self._send_json(default_store().exclude_event(str(payload.get("event_id") or "")))
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                return
            if path == "/events/quality-review":
                try:
                    self._send_json(
                        default_store().set_event_quality_review(
                            str(payload.get("event_id") or ""),
                            str(payload.get("status") or "approved"),
                        )
                    )
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                return
            if path == "/events/split":
                try:
                    self._send_json(
                        default_store().split_event(
                            str(payload.get("event_id") or ""),
                            float(payload.get("split_after_seconds") or 0),
                            str(payload.get("first_activity") or ""),
                            str(payload.get("second_activity") or ""),
                        )
                    )
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                return
            if path == "/events/merge":
                try:
                    self._send_json(
                        default_store().merge_adjacent_events(
                            str(payload.get("first_event_id") or ""),
                            str(payload.get("second_event_id") or ""),
                            str(payload.get("activity") or ""),
                        )
                    )
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                return
            if path == "/settings":
                self._send_json(default_store().update_settings(payload))
                return
            if path == "/diagnostics/checks":
                self._send_json(run_diagnostic_checks())
                return
            if path == "/automation/review":
                self._send_json(
                    default_store().set_automation_review(
                        str(payload.get("activity") or ""),
                        str(payload.get("status") or ""),
                        str(payload.get("note") or ""),
                    )
                )
                return
            if path == "/data/delete/challenge":
                self._send_json({"challenge": DELETE_CHALLENGES.issue()})
                return
            if path == "/export/mermaid":
                self._send_json({"mermaid": create_export_artifact("mermaid")["content"]})
                return
            if path == "/export/drawio":
                self._send_json({"drawio": create_export_artifact("drawio")["content"]})
                return
            if path == "/export/svg":
                self._send_json({"status": "planned", "message": "SVG export will use a local renderer."})
                return
            if path == "/export/csv":
                artifact = create_export_artifact("csv")
                self._send_json({"csv": artifact["content"]})
                return
            if path == "/export/json":
                artifact = create_export_artifact("json")
                self._send_json({"json": artifact["content"]})
                return
            if path == "/export/preview":
                artifact = create_export_artifact(str(payload.get("format") or ""))
                self._send_json({key: artifact[key] for key in ("format", "filename", "byte_size", "preview", "confidential_count", "warning")})
                return
            if path == "/export/save":
                self._send_json(
                    save_export_artifact(
                        str(payload.get("format") or ""),
                        str(payload.get("path") or ""),
                        overwrite_confirmed=bool(payload.get("overwrite_confirmed")),
                    )
                )
                return
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=404)
            return
        except (ValueError, RuntimeError) as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, status=403)
            return
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)
            return
        self._send_json({"error": "not found"}, status=404)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body)

    def _authorize_request(self, path: str | None = None) -> bool:
        policy = getattr(self.server, "security_policy", LOCAL_API_POLICY)
        assert isinstance(policy, LocalApiPolicy)
        try:
            policy.authorize(
                self.command,
                path if path is not None else urlparse(self.path).path,
                self.headers,
                self.headers.get("Content-Length"),
            )
        except RequestRejected as exc:
            self.close_connection = True
            self._send_json({"error": exc.message}, status=exc.status_code)
            return False
        return True

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin in allowed_webui_origins():
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", f"content-type, {DELETE_CHALLENGE_HEADER}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), LocalApiHandler)
    server.security_policy = LOCAL_API_POLICY  # type: ignore[attr-defined]
    _start_parent_watchdog(server, os.environ.get("OPSMINEFLOW_PARENT_PID", ""))
    print(f"OpsMineFlow local API listening on http://127.0.0.1:{PORT}")
    server.serve_forever()


def _start_parent_watchdog(server: ThreadingHTTPServer, parent_pid_value: str) -> None:
    try:
        parent_pid = int(parent_pid_value)
    except ValueError:
        return
    if parent_pid <= 0:
        return

    def stop_after_parent_exit() -> None:
        while os.getppid() == parent_pid:
            time.sleep(0.25)
        server.shutdown()

    threading.Thread(target=stop_after_parent_exit, name="opsmineflow-parent-watchdog", daemon=True).start()
