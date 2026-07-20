from __future__ import annotations

import asyncio
import http.client
import importlib
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

api_app_module = importlib.import_module("opsmineflow_api.app")
fastapi_app = api_app_module.app
from opsmineflow_api.auth import API_SESSION_HEADER, DeleteChallengeStore, LocalApiPolicy, RequestRejected
from opsmineflow_api.server import LocalApiHandler
from opsmineflow_api.server import _start_parent_watchdog


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
        headers = {"Host": "127.0.0.1:8765"}
        for token in ("", "wrong", "recording-token"):
            with self.subTest(token=token):
                with self.assertRaises(RequestRejected) as rejected:
                    self._authorize("GET", "/events", {**headers, API_SESSION_HEADER: token})
                self.assertEqual(rejected.exception.status_code, 401)

    def test_protected_route_accepts_only_the_runtime_session_token(self) -> None:
        self._authorize(
            "GET",
            "/events",
            {"Host": "127.0.0.1:8765", API_SESSION_HEADER: "a" * 64},
        )

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

    def test_public_health_is_minimal_and_does_not_create_snapshot(self) -> None:
        with patch("opsmineflow_api.server.create_api_snapshot", side_effect=AssertionError("must not analyze")):
            status, payload = self._request("GET", "/health")

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok", "bind": "127.0.0.1", "local_only": True, "llm_supported": False})

    def test_delete_challenge_is_required_and_single_use_on_the_http_boundary(self) -> None:
        session_headers = {API_SESSION_HEADER: "b" * 64, "Content-Type": "application/json"}
        with (
            patch("opsmineflow_api.server.DELETE_CHALLENGES", DeleteChallengeStore()),
            patch("opsmineflow_api.server.default_store") as default_store,
            patch("opsmineflow_api.server.recording_manager.stop") as stop_recording,
        ):
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
            self.assertEqual(deleted, {"deleted": True})

            status, rejected = self._request(
                "POST",
                "/data/delete",
                {},
                {**session_headers, "X-OpsMineFlow-Delete-Challenge": challenge},
            )

        self.assertEqual(status, 403)
        self.assertEqual(rejected["error"], "delete challenge is invalid or expired")
        stop_recording.assert_called_once()
        default_store.return_value.clear.assert_called_once()


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


if __name__ == "__main__":
    unittest.main()
