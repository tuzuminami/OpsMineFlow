from __future__ import annotations

import base64
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
    export_llm_handoff_payload,
    import_activitywatch_into_store,
    import_path_into_store,
    project_response,
    projects_response,
    recording_status_to_api_dict,
    run_diagnostic_checks,
    save_export_artifact,
)
from .auth import (
    DELETE_CHALLENGE_HEADER,
    PROJECT_HEADER,
    RUNTIME_PROBE_CHALLENGE_HEADER,
    LocalApiPolicy,
    RequestRejected,
)
from .recording import recording_manager
from .storage import ProjectConflictError, ProjectNotFoundError, StorageCommitError, default_store

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
        if path == "/projects":
            self._send_json(projects_response())
            return
        try:
            store = self._project_store()
        except (ValueError, ProjectNotFoundError) as exc:
            self._send_json({"error": str(exc) or "Project was not found."}, status=400 if isinstance(exc, ValueError) else 404)
            return
        if path == "/diagnostics":
            self._send_json(project_response(store, create_diagnostics(store)))
            return
        if path == "/settings":
            self._send_json(project_response(store, store.get_settings()))
            return
        if path == "/import/history":
            self._send_json(project_response(store, {"imports": store.list_import_history()}))
            return
        if path == "/recording/status":
            self._send_json(project_response(store, recording_status_to_api_dict(recording_manager.status(store.project_id))))
            return
        if path == "/events":
            self._send_json(project_response(store, {"events": create_event_page(0, 500, store)["events"]}))
            return
        if path == "/analytics/summary":
            self._send_json(project_response(store, create_summary(store)))
            return
        if path == "/analytics/app-switching":
            self._send_json(project_response(store, create_app_switching(store)))
            return
        if path == "/analytics/automation-candidates":
            self._send_json(project_response(store, create_automation_candidates(store)))
            return
        if path == "/analytics/event-quality":
            self._send_json(project_response(store, create_event_quality_report(store)))
            return
        if path == "/analytics/process-map":
            self._send_json(project_response(store, create_process_map(store)))
            return
        if path == "/reports/markdown":
            self._send_json(project_response(store, {"markdown": create_markdown_report(store)}))
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._authorize_request(path):
            return
        if path == "/data/delete":
            payload = self._read_json()
            if not DELETE_CHALLENGES.consume(self.headers.get(DELETE_CHALLENGE_HEADER, "")):
                self._send_json({"error": "delete challenge is invalid or expired"}, status=403)
                return
            try:
                store = self._project_store(payload)
                if recording_manager.status(store.project_id).get("active"):
                    recording_manager.stop(store, record_import=False)
                store.clear()
            except StorageCommitError as exc:
                self._send_json({"error": exc.to_api_dict()}, status=503)
                return
            except ProjectConflictError as exc:
                self._send_json({"error": str(exc)}, status=409)
                return
            except (ProjectNotFoundError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=404 if isinstance(exc, ProjectNotFoundError) else 400)
                return
            self._send_json(project_response(store, {"deleted": True}))
            return
        try:
            payload = self._read_json()
            if path == "/projects":
                project = default_store().create_project(str(payload.get("display_name") or ""))
                self._send_json({**projects_response(), "project": project.to_api_dict()})
                return
            if path == "/projects/select":
                project = default_store().select_project(str(payload.get("project_id") or ""))
                self._send_json({**projects_response(), "project": project.to_api_dict()})
                return
            if path == "/projects/rename":
                project = default_store().rename_project(
                    str(payload.get("project_id") or ""),
                    str(payload.get("display_name") or ""),
                    expected_revision=payload.get("expected_revision"),
                )
                self._send_json({**projects_response(), "project": project.to_api_dict()})
                return
            if path == "/projects/delete":
                project_id = str(payload.get("project_id") or "")
                with recording_manager.project_deletion_guard(project_id):
                    replacement_project_id = default_store().delete_project(
                        project_id,
                        expected_revision=payload.get("expected_revision"),
                    )
                self._send_json(
                    {
                        **projects_response(),
                        "deleted_project_id": project_id,
                        "replacement_project_id": replacement_project_id,
                    }
                )
                return
            if path == "/recording/start":
                store = self._project_store(payload)
                self._send_json(
                    project_response(
                        store,
                        recording_status_to_api_dict(
                            recording_manager.start(
                                str(payload.get("case_id") or ""),
                                str(payload.get("activity_label") or ""),
                                bool(payload.get("consent")),
                                store=store,
                            )
                        ),
                    )
                )
                return
            if path == "/recording/stop":
                store = self._project_store()
                self._send_json(project_response(store, recording_status_to_api_dict(recording_manager.stop(store))))
                return
            if path == "/recording/pause":
                store = self._project_store()
                self._send_json(
                    project_response(
                        store,
                        recording_status_to_api_dict(
                            recording_manager.pause(str(payload.get("reason") or ""), project_id=store.project_id)
                        ),
                    )
                )
                return
            if path == "/recording/resume":
                store = self._project_store()
                self._send_json(
                    project_response(store, recording_status_to_api_dict(recording_manager.resume(project_id=store.project_id)))
                )
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
            if path == "/data/delete/challenge":
                self._send_json({"challenge": DELETE_CHALLENGES.issue()})
                return
            store = self._project_store(payload)
            if path == "/events/page":
                self._send_json(project_response(
                    store,
                    create_event_page(
                        int(payload.get("offset") or 0),
                        int(payload.get("limit") or 250),
                        store,
                    ),
                ))
                return
            if path == "/import/preview":
                self._send_json(project_response(
                    store,
                    create_import_preview(
                        str(payload.get("format") or ""),
                        str(payload.get("path") or ""),
                        payload.get("mapping") if isinstance(payload.get("mapping"), dict) else None,
                        str(payload.get("date_format") or ""),
                        str(payload.get("timezone") or "UTC"),
                    ),
                ))
                return
            if path == "/import/activitywatch-preview":
                self._send_json(project_response(
                    store,
                    create_activitywatch_preview(
                        bool(payload.get("enabled")),
                        str(payload.get("base_url") or "http://127.0.0.1:5600"),
                        store,
                    ),
                ))
                return
            if path == "/import/csv":
                self._send_json(project_response(
                    store,
                    import_path_into_store(
                        "csv",
                        str(payload.get("path") or ""),
                        store=store,
                        mapping=payload.get("mapping") if isinstance(payload.get("mapping"), dict) else None,
                        date_format=str(payload.get("date_format") or ""),
                        timezone_name=str(payload.get("timezone") or "UTC"),
                    ),
                ))
                return
            if path == "/import/json":
                self._send_json(project_response(store, import_path_into_store("json", str(payload.get("path") or ""), store=store)))
                return
            if path == "/import/activitywatch-local":
                self._send_json(project_response(
                    store,
                    import_activitywatch_into_store(
                        bool(payload.get("enabled")),
                        str(payload.get("base_url") or "http://127.0.0.1:5600"),
                        str(payload.get("mode") or "replace"),
                        store=store,
                    ),
                ))
                return
            if path == "/events/label":
                try:
                    store.set_label(str(payload.get("event_id") or ""), str(payload.get("label") or ""))
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                    return
                self._send_json(
                    project_response(
                        store,
                        {
                            "event_id": store.event_reference_for_input(payload.get("event_id")),
                            "label": payload.get("label"),
                        },
                    )
                )
                return
            if path == "/events/activity":
                try:
                    event = store.update_event_activity(
                        str(payload.get("event_id") or ""),
                        str(payload.get("activity") or ""),
                    )
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                    return
                self._send_json(project_response(store, {"event": event}))
                return
            if path == "/events/case-correlation":
                try:
                    event = store.update_event_case_correlation(
                        str(payload.get("event_id") or ""),
                        str(payload.get("case_id") or ""),
                        str(payload.get("reason") or ""),
                    )
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                    return
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                from .app import event_to_api_dict

                self._send_json(project_response(store, {"event": event_to_api_dict(event, store.get_settings())}))
                return
            if path == "/events/exclude":
                try:
                    self._send_json(project_response(store, store.exclude_event(str(payload.get("event_id") or ""))))
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                return
            if path == "/events/quality-review":
                try:
                    self._send_json(
                        project_response(store, store.set_event_quality_review(
                            str(payload.get("event_id") or ""),
                            str(payload.get("status") or "approved"),
                        ))
                    )
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                return
            if path == "/events/split":
                try:
                    self._send_json(
                        project_response(store, store.split_event(
                            str(payload.get("event_id") or ""),
                            float(payload.get("split_after_seconds") or 0),
                            str(payload.get("first_activity") or ""),
                            str(payload.get("second_activity") or ""),
                        ))
                    )
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                return
            if path == "/events/merge":
                try:
                    self._send_json(
                        project_response(store, store.merge_adjacent_events(
                            str(payload.get("first_event_id") or ""),
                            str(payload.get("second_event_id") or ""),
                            str(payload.get("activity") or ""),
                        ))
                    )
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                return
            if path == "/settings":
                updates = dict(payload)
                updates.pop("expected_revision", None)
                self._send_json(project_response(store, store.update_settings(updates)))
                return
            if path == "/diagnostics/checks":
                self._send_json(project_response(store, run_diagnostic_checks()))
                return
            if path == "/automation/review":
                self._send_json(
                    project_response(store, store.set_automation_review(
                        str(payload.get("activity") or ""),
                        str(payload.get("status") or ""),
                        str(payload.get("note") or ""),
                    ))
                )
                return
            if path == "/export/mermaid":
                self._send_json(project_response(store, {"mermaid": create_export_artifact("mermaid", store)["content"]}))
                return
            if path == "/export/drawio":
                self._send_json(project_response(store, {"drawio": create_export_artifact("drawio", store)["content"]}))
                return
            if path == "/export/svg":
                self._send_json(project_response(store, {"status": "planned", "message": "SVG export will use a local renderer."}))
                return
            if path == "/export/csv":
                artifact = create_export_artifact("csv", store)
                content = artifact["content"]
                if not isinstance(content, bytes):
                    raise RuntimeError("CSV export must be a ZIP bundle.")
                self._send_json(project_response(
                    store,
                    {
                        "filename": artifact["filename"],
                        "zip_base64": base64.b64encode(content).decode("ascii"),
                    },
                ))
                return
            if path == "/export/json":
                artifact = create_export_artifact("json", store)
                self._send_json(project_response(store, {"json": artifact["content"]}))
                return
            if path == "/export/llm-handoff":
                self._send_json(project_response(store, export_llm_handoff_payload(store)))
                return
            if path == "/export/preview":
                artifact = create_export_artifact(str(payload.get("format") or ""), store)
                self._send_json(project_response(store, {key: artifact[key] for key in ("format", "filename", "byte_size", "preview", "confidential_count", "warning")}))
                return
            if path == "/export/save":
                self._send_json(project_response(
                    store,
                    save_export_artifact(
                        str(payload.get("format") or ""),
                        str(payload.get("path") or ""),
                        store=store,
                        overwrite_confirmed=bool(payload.get("overwrite_confirmed")),
                    ),
                ))
                return
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=404)
            return
        except StorageCommitError as exc:
            self._send_json({"error": exc.to_api_dict()}, status=503)
            return
        except ProjectConflictError as exc:
            self._send_json({"error": str(exc)}, status=409)
            return
        except ProjectNotFoundError:
            self._send_json({"error": "Project was not found."}, status=404)
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

    def _project_store(self, payload: dict[str, Any] | None = None):
        project_id = self.headers.get(PROJECT_HEADER, "").strip()
        if not project_id:
            raise ValueError("Project context is required.")
        expected_revision = (payload or {}).get("expected_revision")
        if expected_revision is not None and (isinstance(expected_revision, bool) or not isinstance(expected_revision, int)):
            raise ValueError("Project revision must be an integer.")
        return default_store().for_project(project_id, expected_revision=expected_revision)

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
        self.send_header("Access-Control-Allow-Headers", f"content-type, {DELETE_CHALLENGE_HEADER}, {PROJECT_HEADER}")


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
