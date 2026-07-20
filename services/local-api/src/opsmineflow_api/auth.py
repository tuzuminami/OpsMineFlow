from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass

API_SESSION_HEADER = "X-OpsMineFlow-Api-Session"
DELETE_CHALLENGE_HEADER = "X-OpsMineFlow-Delete-Challenge"
RUNTIME_PROBE_CHALLENGE_HEADER = "X-OpsMineFlow-Runtime-Probe-Challenge"
MAX_REQUEST_BODY_BYTES = 1_048_576
DELETE_CHALLENGE_TTL_SECONDS = 60

PUBLIC_ROUTES = {
    ("GET", "/health"),
    ("GET", "/runtime/health"),
}
RECORDING_AGENT_ROUTES = {
    ("POST", "/recording/events"),
    ("POST", "/recording/heartbeat"),
}
PROTECTED_ROUTES = {
    ("GET", "/diagnostics"),
    ("GET", "/settings"),
    ("GET", "/import/history"),
    ("GET", "/recording/status"),
    ("GET", "/events"),
    ("GET", "/analytics/summary"),
    ("GET", "/analytics/app-switching"),
    ("GET", "/analytics/automation-candidates"),
    ("GET", "/analytics/event-quality"),
    ("GET", "/analytics/process-map"),
    ("GET", "/reports/markdown"),
    ("POST", "/diagnostics/checks"),
    ("POST", "/recording/start"),
    ("POST", "/recording/stop"),
    ("POST", "/recording/pause"),
    ("POST", "/recording/resume"),
    ("POST", "/import/preview"),
    ("POST", "/import/activitywatch-preview"),
    ("POST", "/settings"),
    ("POST", "/import/csv"),
    ("POST", "/import/json"),
    ("POST", "/import/activitywatch-local"),
    ("POST", "/events/label"),
    ("POST", "/events/activity"),
    ("POST", "/events/case-correlation"),
    ("POST", "/events/exclude"),
    ("POST", "/events/quality-review"),
    ("POST", "/events/split"),
    ("POST", "/events/merge"),
    ("POST", "/events/page"),
    ("POST", "/automation/review"),
    ("POST", "/data/delete/challenge"),
    ("POST", "/data/delete"),
    ("POST", "/export/mermaid"),
    ("POST", "/export/drawio"),
    ("POST", "/export/svg"),
    ("POST", "/export/csv"),
    ("POST", "/export/json"),
    ("POST", "/export/llm-handoff"),
    ("POST", "/export/preview"),
    ("POST", "/export/save"),
}
KNOWN_ROUTES = PUBLIC_ROUTES | RECORDING_AGENT_ROUTES | PROTECTED_ROUTES
SINGLE_VALUE_HEADERS = (
    "Host",
    "Content-Length",
    "Transfer-Encoding",
    "Origin",
    "Content-Type",
    API_SESSION_HEADER,
    DELETE_CHALLENGE_HEADER,
    RUNTIME_PROBE_CHALLENGE_HEADER,
    "Access-Control-Request-Method",
    "Access-Control-Request-Headers",
)


@dataclass(frozen=True)
class RuntimeCredentials:
    nonce: str
    api_session_token: str
    runtime_probe_secret: str


class RequestRejected(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def consume_runtime_credentials() -> RuntimeCredentials:
    """Capture launcher credentials once, then remove them from child environments."""

    return RuntimeCredentials(
        nonce=os.environ.pop("OPSMINEFLOW_RUNTIME_NONCE", "").strip(),
        api_session_token=os.environ.pop("OPSMINEFLOW_RUNTIME_SECRET", "").strip(),
        runtime_probe_secret=os.environ.pop("OPSMINEFLOW_RUNTIME_PROBE_SECRET", "").strip(),
    )


class LocalApiPolicy:
    def __init__(
        self,
        api_session_token: str,
        port: int,
        allowed_origins: set[str],
        development_mode: bool,
    ) -> None:
        self._api_session_token = api_session_token
        self._expected_host = f"127.0.0.1:{port}"
        self._allowed_origins = allowed_origins
        self._development_mode = development_mode

    def authorize(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        content_length: str | None,
    ) -> None:
        normalized_method = method.upper()
        self._reject_duplicate_security_headers(headers)
        if normalized_method == "OPTIONS":
            self._authorize_preflight(path, headers, content_length)
            return
        route = (normalized_method, path)
        if route not in KNOWN_ROUTES:
            raise RequestRejected(404, "not found")
        self._authorize_metadata(normalized_method, headers, content_length)
        if route in PUBLIC_ROUTES or route in RECORDING_AGENT_ROUTES:
            return
        if self._development_mode:
            return
        supplied_token = headers.get(API_SESSION_HEADER, "")
        if (
            not self._api_session_token
            or len(supplied_token) != len(self._api_session_token)
            or not hmac.compare_digest(supplied_token, self._api_session_token)
        ):
            raise RequestRejected(401, "local API authorization failed")

    def _reject_duplicate_security_headers(self, headers: Mapping[str, str]) -> None:
        for name in SINGLE_VALUE_HEADERS:
            if len(_header_values(headers, name)) > 1:
                raise RequestRejected(400, "duplicate local API header is not allowed")

    def _authorize_preflight(
        self,
        path: str,
        headers: Mapping[str, str],
        content_length: str | None,
    ) -> None:
        if path not in {route_path for _, route_path in KNOWN_ROUTES}:
            raise RequestRejected(404, "not found")
        if not self._development_mode:
            raise RequestRejected(403, "cross-origin local API access is disabled")
        self._authorize_metadata("OPTIONS", headers, content_length, require_origin=True)
        requested_method = headers.get("Access-Control-Request-Method", "").upper()
        if requested_method not in {"GET", "POST"}:
            raise RequestRejected(400, "local API method is not allowed")
        requested_headers = {
            value.strip().lower()
            for value in headers.get("Access-Control-Request-Headers", "").split(",")
            if value.strip()
        }
        if not requested_headers.issubset({"content-type", DELETE_CHALLENGE_HEADER.lower()}):
            raise RequestRejected(400, "local API header is not allowed")

    def _authorize_metadata(
        self,
        method: str,
        headers: Mapping[str, str],
        content_length: str | None,
        require_origin: bool = False,
    ) -> None:
        if headers.get("Host", "") != self._expected_host:
            raise RequestRejected(400, "invalid local API host")
        if headers.get("Transfer-Encoding"):
            raise RequestRejected(400, "transfer encoding is not allowed")
        origin = headers.get("Origin")
        if origin is None:
            if require_origin:
                raise RequestRejected(403, "local API origin is required")
        elif not self._development_mode or origin == "null" or origin not in self._allowed_origins:
            raise RequestRejected(403, "local API origin is not allowed")
        if method == "POST":
            content_type = headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise RequestRejected(415, "local API requests must use application/json")
            length = _parse_content_length(content_length)
            if length > MAX_REQUEST_BODY_BYTES:
                raise RequestRejected(413, "local API request is too large")
        elif content_length not in (None, "", "0"):
            raise RequestRejected(400, "unexpected local API request body")


def _parse_content_length(value: str | None) -> int:
    if value is None or not value.isdecimal():
        raise RequestRejected(400, "local API content length is required")
    length = int(value)
    if length < 0:
        raise RequestRejected(400, "invalid local API content length")
    return length


def _header_values(headers: Mapping[str, str], name: str) -> list[str]:
    getlist = getattr(headers, "getlist", None)
    if callable(getlist):
        return [str(value) for value in getlist(name)]
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        return [str(value) for value in (get_all(name) or [])]
    value = headers.get(name)
    return [] if value is None else [str(value)]


class DeleteChallengeStore:
    def __init__(self, ttl_seconds: int = DELETE_CHALLENGE_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._entries: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        challenge = secrets.token_urlsafe(32)
        with self._lock:
            self._prune_locked(time.monotonic())
            self._entries[challenge] = time.monotonic() + self._ttl_seconds
        return challenge

    def consume(self, supplied_challenge: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            matching_challenge = ""
            for challenge in self._entries:
                if len(supplied_challenge) == len(challenge) and hmac.compare_digest(supplied_challenge, challenge):
                    matching_challenge = challenge
            if not matching_challenge:
                return False
            expires_at = self._entries.pop(matching_challenge)
            return expires_at >= now

    def _prune_locked(self, now: float) -> None:
        for challenge, expires_at in list(self._entries.items()):
            if expires_at < now:
                del self._entries[challenge]
