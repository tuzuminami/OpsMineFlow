from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

from .activitywatch import import_activitywatch_local
from .app import (
    create_api_snapshot,
    create_diagnostics,
    create_export_artifact,
    create_import_preview,
    import_path_into_store,
    save_export_artifact,
)
from .storage import default_store

HOST = os.environ.get("OPSMINEFLOW_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPSMINEFLOW_API_PORT", "8765"))


class LocalApiHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        snapshot = create_api_snapshot()
        routes: dict[str, Any] = {
            "/health": snapshot["health"],
            "/diagnostics": create_diagnostics(),
            "/settings": default_store().get_settings(),
            "/import/history": default_store().list_import_history(),
            "/events": snapshot["events"],
            "/analytics/summary": snapshot["summary"],
            "/analytics/app-switching": snapshot["app_switching"],
            "/analytics/automation-candidates": snapshot["automation_candidates"],
            "/analytics/process-map": snapshot["process_map"],
            "/reports/markdown": {"markdown": snapshot["markdown_report"]},
        }
        if path not in routes:
            self._send_json({"error": "not found"}, status=404)
            return
        self._send_json(routes[path])

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/import/preview":
                self._send_json(create_import_preview(str(payload.get("format") or ""), str(payload.get("path") or "")))
                return
            if path == "/import/csv":
                self._send_json(import_path_into_store("csv", str(payload.get("path") or "")))
                return
            if path == "/import/json":
                self._send_json(import_path_into_store("json", str(payload.get("path") or "")))
                return
            if path == "/import/activitywatch-local":
                if not payload.get("enabled"):
                    self._send_json({"imported_events": 0, "message": "ActivityWatch import is disabled until explicitly enabled."})
                    return
                events = import_activitywatch_local(str(payload.get("base_url") or "http://127.0.0.1:5600"))
                default_store().replace(events, import_source="activitywatch_local", import_path=str(payload.get("base_url") or "http://127.0.0.1:5600"))
                self._send_json({"imported_events": len(events), "source": "activitywatch_local"})
                return
            if path == "/events/label":
                try:
                    default_store().set_label(str(payload.get("event_id") or ""), str(payload.get("label") or ""))
                except KeyError:
                    self._send_json({"error": "Event was not found"}, status=404)
                    return
                self._send_json({"event_id": payload.get("event_id"), "label": payload.get("label")})
                return
            if path == "/settings":
                self._send_json(default_store().update_settings(payload))
                return
            if path == "/automation/review":
                self._send_json(
                    default_store().set_automation_review(
                        str(payload.get("activity") or ""),
                        str(payload.get("status") or ""),
                    )
                )
                return
            if path == "/data/delete":
                default_store().clear()
                self._send_json({"deleted": True})
                return
            export_routes: dict[str, Any] = {
                "/export/mermaid": {"mermaid": create_export_artifact("mermaid")["content"]},
                "/export/drawio": {"drawio": create_export_artifact("drawio")["content"]},
                "/export/svg": {"status": "planned", "message": "SVG export will use a local renderer."},
                "/export/csv": {"csv": create_export_artifact("csv")["content"], "events": create_api_snapshot()["events"]},
                "/export/json": {"json": create_export_artifact("json")["content"], "snapshot": create_api_snapshot()},
            }
            if path in export_routes:
                self._send_json(export_routes[path])
                return
            if path == "/export/preview":
                artifact = create_export_artifact(str(payload.get("format") or ""))
                self._send_json({key: artifact[key] for key in ("format", "filename", "byte_size", "preview", "confidential_count", "warning")})
                return
            if path == "/export/save":
                self._send_json(save_export_artifact(str(payload.get("format") or ""), str(payload.get("path") or "")))
                return
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=404)
            return
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
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

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5173")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type")


def main() -> None:
    server = HTTPServer((HOST, PORT), LocalApiHandler)
    print(f"OpsMineFlow local API listening on http://127.0.0.1:{PORT}")
    server.serve_forever()
