from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from .app import create_api_snapshot

HOST = "127.0.0.1"
PORT = 8765


class LocalApiHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        snapshot = create_api_snapshot()
        routes: dict[str, Any] = {
            "/health": snapshot["health"],
            "/events": snapshot["events"],
            "/analytics/summary": snapshot["summary"],
            "/analytics/app-switching": snapshot["app_switching"],
            "/analytics/automation-candidates": snapshot["automation_candidates"],
            "/analytics/process-map": snapshot["process_map"],
            "/reports/markdown": {"markdown": snapshot["markdown_report"]},
        }
        if self.path not in routes:
            self._send_json({"error": "not found"}, status=404)
            return
        self._send_json(routes[self.path])

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = HTTPServer((HOST, PORT), LocalApiHandler)
    print(f"OpsMineFlow local API listening on http://127.0.0.1:{PORT}")
    server.serve_forever()
