from __future__ import annotations

import asyncio
import http.client
import importlib
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

api_app_module = importlib.import_module("opsmineflow_api.app")
fastapi_app = api_app_module.app
from opsmineflow_api.auth import API_SESSION_HEADER, PROJECT_HEADER, DeleteChallengeStore, LocalApiPolicy, RequestRejected
from opsmineflow_api.server import LocalApiHandler
from opsmineflow_api.server import _start_parent_watchdog
from opsmineflow_api.storage import StorageCommitError


class LocalApiPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = LocalApiPolicy(
            api_session_token="a" * 64,
            port=8765,
            allowed_origins={"http://127.0.0.1:5173"},
            development_mode=False,
        )

    def _authorize(self, method: str, path: str, headers: dict[str, str], content_length: str | None = None) -> None:
        self.policy.authorize(method, path, headers, content_length)

    def test_protected_route_rejects_missing_wrong_and_recording_tokens(self) -> None:
        for method, path in (("GET", "/events"), ("POST", "/export/llm-handoff"), ("POST", "/events/case-correlation")):
            for token in ("", "wrong", "recording-token"):
                with self.subTest(method=method, path=path, token=token):
                    headers = {"Host": "127.0.0.1:8765", API_SESSION_HEADER: token}
                    if method == "POST":
                        headers["Content-Type"] = "application/json"
                    with self.assertRaises(RequestRejected) as rejected:
                        self._authorize(method, path, headers, "2" if method == "POST" else None)
                    self.assertEqual(rejected.exception.status_code, 401)

    def test_protected_route_accepts_only_the_runtime_session_token(self) -> None:
        for method, path in (("GET", "/events"), ("POST", "/export/llm-handoff"), ("POST", "/events/case-correlation")):
            with self.subTest(method=method, path=path):
                headers = {"Host": "127.0.0.1:8765", API_SESSION_HEADER: "a" * 64}
                if method == "POST":
                    headers["Content-Type"] = "application/json"
                self._authorize(method, path, headers, "2" if method == "POST" else None)

    def test_policy_rejects_hostile_origin_simple_post_and_oversized_body_before_dispatch(self) -> None:
        headers = {
            "Host": "127.0.0.1:8765",
            API_SESSION_HEADER: "a" * 64,
            "Content-Type": "application/json",
        }
        for changed_headers, length, expected in (
            ({"Origin": "opaque-hostile-origin"}, "2", 403),
            ({"Origin": "null"}, "2", 403),
            ({"Host": "localhost:8765"}, "2", 400),
            ({"Content-Type": "text/plain"}, "2", 415),
            ({}, "1048577", 413),
            ({"Transfer-Encoding": "chunked"}, "2", 400),
        ):
            with self.subTest(changed_headers=changed_headers, length=length):
                with self.assertRaises(RequestRejected) as rejected:
                    self._authorize("POST", "/data/delete", {**headers, **changed_headers}, length)
                self.assertEqual(rejected.exception.status_code, expected)

    def test_public_health_and_recording_agent_routes_use_separate_access_scopes(self) -> None:
        headers = {"Host": "127.0.0.1:8765"}

        self._authorize("GET", "/health", headers)
        self._authorize(
            "POST",
            "/recording/events",
            {**headers, "Content-Type": "application/json"},
            "2",
        )
        with self.assertRaises(RequestRejected):
            self._authorize("POST", "/recording/start", {**headers, "Content-Type": "application/json"}, "2")

    def test_policy_rejects_duplicate_security_headers(self) -> None:
        if fastapi_app is None:
            self.skipTest("FastAPI is unavailable")
        from starlette.datastructures import Headers

        headers = Headers(
            raw=[
                (b"host", b"127.0.0.1:8765"),
                (b"host", b"127.0.0.1:8765"),
                (API_SESSION_HEADER.lower().encode("ascii"), b"a" * 64),
            ]
        )

        with self.assertRaises(RequestRejected) as rejected:
            self.policy.authorize("GET", "/events", headers, None)
        self.assertEqual(rejected.exception.status_code, 400)

    def test_delete_challenge_is_single_use(self) -> None:
        challenges = DeleteChallengeStore(ttl_seconds=60)
        challenge = challenges.issue()

        self.assertTrue(challenges.consume(challenge))
        self.assertFalse(challenges.consume(challenge))
        self.assertFalse(challenges.consume("wrong-challenge"))


class HandwrittenServerPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), LocalApiHandler)
        self.server.security_policy = LocalApiPolicy(  # type: ignore[attr-defined]
            api_session_token="b" * 64,
            port=self.server.server_port,
            allowed_origins=set(),
            development_mode=False,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)

    def tearDown(self) -> None:
        self.connection.close()
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def _request(self, method: str, path: str, payload: dict[str, object] | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        self.connection.request(method, path, body=body, headers=headers or {})
        response = self.connection.getresponse()
        return response.status, json.loads(response.read())

    def test_rejection_happens_before_snapshot_generation(self) -> None:
        with patch("opsmineflow_api.server.create_api_snapshot", side_effect=AssertionError("must not dispatch")):
            status, payload = self._request("GET", "/events")

        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "local API authorization failed")

    def test_project_scoped_route_rejects_a_missing_project_header_before_reading_data(self) -> None:
        headers = {API_SESSION_HEADER: "b" * 64}
        with patch("opsmineflow_api.server.create_event_page", side_effect=AssertionError("must not read another project")):
            status, payload = self._request("GET", "/events", headers=headers)

        self.assertEqual(status, 400)
        self.assertEqual(payload, {"error": "Project context is required."})

    def test_public_health_is_minimal_and_does_not_create_snapshot(self) -> None:
        with patch("opsmineflow_api.server.create_api_snapshot", side_effect=AssertionError("must not analyze")):
            status, payload = self._request("GET", "/health")

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok", "bind": "127.0.0.1", "local_only": True, "llm_supported": False})

    def test_delete_challenge_is_required_and_single_use_on_the_http_boundary(self) -> None:
        project_id = "b01eecad-1e18-5e88-bf34-8e8e8358cfcb"
        session_headers = {
            API_SESSION_HEADER: "b" * 64,
            "Content-Type": "application/json",
            PROJECT_HEADER: project_id,
        }
        with (
            patch("opsmineflow_api.server.DELETE_CHALLENGES", DeleteChallengeStore()),
            patch("opsmineflow_api.server.default_store") as default_store,
            patch("opsmineflow_api.server.recording_manager.stop") as stop_recording,
            patch("opsmineflow_api.server.recording_manager.status", return_value={"active": True}),
        ):
            scoped_store = default_store.return_value.for_project.return_value
            scoped_store.snapshot.return_value = SimpleNamespace(project_id=project_id, project_revision=3)
            status, issued = self._request("POST", "/data/delete/challenge", {}, session_headers)
            self.assertEqual(status, 200)
            challenge = str(issued["challenge"])

            status, deleted = self._request(
                "POST",
                "/data/delete",
                {},
                {**session_headers, "X-OpsMineFlow-Delete-Challenge": challenge},
            )
            self.assertEqual(status, 200)
            self.assertEqual(deleted, {"deleted": True, "project_id": project_id, "project_revision": 3})

            status, rejected = self._request(
                "POST",
                "/data/delete",
                {},
                {**session_headers, "X-OpsMineFlow-Delete-Challenge": challenge},
            )

        self.assertEqual(status, 403)
        self.assertEqual(rejected["error"], "delete challenge is invalid or expired")
        default_store.return_value.for_project.assert_called_with(project_id, expected_revision=None)
        stop_recording.assert_called_once_with(scoped_store, record_import=False)
        scoped_store.clear.assert_called_once()

    def test_delete_of_another_project_does_not_stop_an_unrelated_recording(self) -> None:
        project_id = "b01eecad-1e18-5e88-bf34-8e8e8358cfcb"
        session_headers = {
            API_SESSION_HEADER: "b" * 64,
            "Content-Type": "application/json",
            PROJECT_HEADER: project_id,
        }
        with (
            patch("opsmineflow_api.server.DELETE_CHALLENGES", DeleteChallengeStore()),
            patch("opsmineflow_api.server.default_store") as default_store,
            patch("opsmineflow_api.server.recording_manager.stop") as stop_recording,
            patch("opsmineflow_api.server.recording_manager.status", return_value={"active": False}),
        ):
            default_store.return_value.for_project.return_value.snapshot.return_value = SimpleNamespace(
                project_id=project_id,
                project_revision=8,
            )
            status, issued = self._request("POST", "/data/delete/challenge", {}, session_headers)
            self.assertEqual(status, 200)
            status, deleted = self._request(
                "POST",
                "/data/delete",
                {},
                {**session_headers, "X-OpsMineFlow-Delete-Challenge": str(issued["challenge"])},
            )

        self.assertEqual(status, 200)
        self.assertEqual(deleted, {"deleted": True, "project_id": project_id, "project_revision": 8})
        stop_recording.assert_not_called()
        default_store.return_value.for_project.return_value.clear.assert_called_once()

    def test_storage_commit_failure_has_a_stable_handwritten_http_contract(self) -> None:
        headers = {
            API_SESSION_HEADER: "b" * 64,
            "Content-Type": "application/json",
            PROJECT_HEADER: "b01eecad-1e18-5e88-bf34-8e8e8358cfcb",
        }
        with patch("opsmineflow_api.server.default_store") as default_store:
            default_store.return_value.for_project.return_value.set_label.side_effect = StorageCommitError("storage_busy")
            status, payload = self._request(
                "POST",
                "/events/label",
                {"event_id": "event-1", "label": "Reviewed"},
                headers,
            )

        self.assertEqual(status, 503)
        self.assertEqual(
            payload,
            {
                "error": {
                    "code": "storage_busy",
                    "message": "Local storage could not be updated. No changes were applied.",
                    "retryable": True,
                    "recovery_action": "retry",
                }
            },
        )


class ParentWatchdogTests(unittest.TestCase):
    def test_sidecar_stops_when_its_desktop_parent_exits(self) -> None:
        stopped = threading.Event()

        class FakeServer:
            def shutdown(self) -> None:
                stopped.set()

        with patch("opsmineflow_api.server.os.getppid", side_effect=[4242, 1]):
            _start_parent_watchdog(FakeServer(), "4242")  # type: ignore[arg-type]
            self.assertTrue(stopped.wait(timeout=1))

    def test_missing_or_invalid_parent_configuration_does_not_start_a_watchdog(self) -> None:
        class FakeServer:
            def shutdown(self) -> None:
                raise AssertionError("a watchdog must not be started")

        _start_parent_watchdog(FakeServer(), "")  # type: ignore[arg-type]
        _start_parent_watchdog(FakeServer(), "not-a-pid")  # type: ignore[arg-type]


class FastApiPolicyTests(unittest.TestCase):
    def test_fastapi_uses_the_same_rejection_contract(self) -> None:
        if fastapi_app is None:
            self.skipTest("FastAPI is unavailable")
        from starlette.requests import Request

        policy = LocalApiPolicy("c" * 64, 8765, set(), False)
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/events",
                "headers": [(b"host", b"127.0.0.1:8765")],
                "scheme": "http",
                "query_string": b"",
                "server": ("127.0.0.1", 8765),
                "client": ("127.0.0.1", 1),
            }
        )

        async def must_not_dispatch(_: Request):
            raise AssertionError("middleware must reject before route dispatch")

        with patch("opsmineflow_api.app.LOCAL_API_POLICY", policy):
            response = asyncio.run(api_app_module.enforce_local_api_policy(request, must_not_dispatch))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.body), {"error": "local API authorization failed"})

    def test_fastapi_storage_commit_failure_has_the_same_stable_contract(self) -> None:
        if fastapi_app is None:
            self.skipTest("FastAPI is unavailable")
        from starlette.requests import Request

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/events/label",
                "headers": [(b"host", b"127.0.0.1:8765")],
                "scheme": "http",
                "query_string": b"",
                "server": ("127.0.0.1", 8765),
                "client": ("127.0.0.1", 1),
            }
        )
        response = asyncio.run(api_app_module.storage_commit_error(request, StorageCommitError("storage_busy")))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            json.loads(response.body),
            {
                "error": {
                    "code": "storage_busy",
                    "message": "Local storage could not be updated. No changes were applied.",
                    "retryable": True,
                    "recovery_action": "retry",
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
