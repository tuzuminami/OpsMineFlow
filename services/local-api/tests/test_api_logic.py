from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from opsmineflow_api.app import (
    allowed_webui_origins,
    create_api_snapshot,
    create_activitywatch_preview,
    create_diagnostics,
    create_event_page,
    create_event_quality_report,
    create_export_artifact,
    create_import_preview,
    create_runtime_health,
    import_activitywatch_into_store,
    import_path_into_store,
    run_diagnostic_checks,
    save_export_artifact,
)
from opsmineflow_api.child_process import sanitized_subprocess_environment
from opsmineflow_api.auth import LocalApiPolicy
from opsmineflow_api.recording import RecordingManager, _recording_agent_environment, native_event_from_payload
from opsmineflow_api.server import LocalApiHandler
from opsmineflow_api.storage import EventStore
from opsmineflow_mining import load_events_from_csv


class ApiLogicTests(unittest.TestCase):
    def test_native_recording_event_appends_and_persists(self) -> None:
        session = {
            "session_id": "rec-test",
            "case_id": "CASE-RECORDED",
            "activity_label": "請求処理",
        }
        payload = {
            "session_id": "rec-test",
            "sequence": 1,
            "app_name": "Safari",
            "app_bundle_id": "com.apple.Safari",
            "timestamp_start": "2026-06-21T01:00:00+00:00",
            "timestamp_end": "2026-06-21T01:00:10+00:00",
            "duration_seconds": 10,
        }
        event = native_event_from_payload(payload, session)

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            store = EventStore(db_path=db_path)
            self.assertEqual(store.append([event]), 1)
            self.assertEqual(store.append([event]), 0)
            reopened = EventStore(db_path=db_path)

        self.assertEqual(len(reopened.events), 1)
        self.assertEqual(reopened.events[0].activity_raw, "請求処理 / Safari")
        self.assertEqual(reopened.events[0].app_name, "Safari")
        self.assertEqual(reopened.events[0].window_title, "")
        self.assertEqual(reopened.events[0].url, "")
        self.assertIn("frontmost_app_only", reopened.events[0].metadata_json)

    def test_native_recording_respects_excluded_apps(self) -> None:
        session = {"session_id": "rec-test", "case_id": "CASE", "activity_label": "Work"}
        payload = {
            "session_id": "rec-test",
            "sequence": 1,
            "app_name": "Safari",
            "app_bundle_id": "com.apple.Safari",
            "timestamp_start": "2026-06-21T01:00:00+00:00",
            "timestamp_end": "2026-06-21T01:00:10+00:00",
            "duration_seconds": 10,
        }
        store = EventStore()
        store.update_settings({"excluded_apps": ["Safari"]})

        self.assertEqual(store.append([native_event_from_payload(payload, session)]), 0)
        self.assertEqual(store.events, [])

    def test_recording_manager_requires_consent_and_stops_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_path = root / "fake-agent.sh"
            agent_path.write_text(
                "#!/bin/bash\n"
                "if [[ ${1:-} == --version ]]; then echo 'opsmineflow-agent test'; exit 0; fi\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  if [[ $1 == --stop-file ]]; then stop_file=$2; shift 2; else shift; fi\n"
                "done\n"
                "while [[ ! -e $stop_file ]]; do sleep 0.05; done\n",
                encoding="utf-8",
            )
            agent_path.chmod(0o755)
            manager = RecordingManager(agent_path=agent_path, platform_name="Darwin")
            with self.assertRaises(ValueError):
                manager.start("CASE", "Work", False)
            with patch.dict("os.environ", {"OPSMINEFLOW_DATA_DIR": str(root), "OPSMINEFLOW_API_PORT": "8765"}):
                started = manager.start("CASE", "Work", True)
                with self.assertRaises(PermissionError):
                    manager.heartbeat("invalid-token", started["session_id"], "Safari")
                stopped = manager.stop(EventStore())

        self.assertTrue(started["active"])
        self.assertIn("agent_path", started)
        self.assertIn("token_ttl_seconds", started)
        self.assertFalse(stopped["active"])

    def test_recording_manager_rejects_replayed_sequences(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_path = root / "fake-agent.sh"
            agent_path.write_text(
                "#!/bin/bash\n"
                "if [[ ${1:-} == --version ]]; then echo 'opsmineflow-agent test'; exit 0; fi\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  if [[ $1 == --stop-file ]]; then stop_file=$2; shift 2; else shift; fi\n"
                "done\n"
                "while [[ ! -e $stop_file ]]; do sleep 0.05; done\n",
                encoding="utf-8",
            )
            agent_path.chmod(0o755)
            manager = RecordingManager(agent_path=agent_path, platform_name="Darwin")
            store = EventStore()
            with patch.dict("os.environ", {"OPSMINEFLOW_DATA_DIR": str(root), "OPSMINEFLOW_API_PORT": "8765"}):
                started = manager.start("CASE", "Work", True)
                payload = {
                    "session_id": started["session_id"],
                    "sequence": 1,
                    "app_name": "Safari",
                    "app_bundle_id": "com.apple.Safari",
                    "timestamp_start": "2026-06-21T01:00:00+00:00",
                    "timestamp_end": "2026-06-21T01:00:10+00:00",
                    "duration_seconds": 10,
                }
                first = manager.ingest(manager._token, payload, store)
                with self.assertRaises(ValueError):
                    manager.ingest(manager._token, payload, store)
                manager.stop(store)

        self.assertEqual(first["appended"], 1)
        self.assertEqual(len(store.events), 1)

    def test_recording_manager_pause_resume_excludes_paused_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_path = root / "fake-agent.sh"
            agent_path.write_text(
                "#!/bin/bash\n"
                "if [[ ${1:-} == --version ]]; then echo 'opsmineflow-agent test'; exit 0; fi\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  if [[ $1 == --stop-file ]]; then stop_file=$2; shift 2; else shift; fi\n"
                "done\n"
                "while [[ ! -e $stop_file ]]; do sleep 0.05; done\n",
                encoding="utf-8",
            )
            agent_path.chmod(0o755)
            manager = RecordingManager(agent_path=agent_path, platform_name="Darwin")
            store = EventStore()
            with patch.dict("os.environ", {"OPSMINEFLOW_DATA_DIR": str(root), "OPSMINEFLOW_API_PORT": "8765"}):
                started = manager.start("CASE", "Work", True)
                base_payload = {
                    "session_id": started["session_id"],
                    "app_name": "Safari",
                    "app_bundle_id": "com.apple.Safari",
                    "timestamp_start": "2026-06-21T01:00:00+00:00",
                    "timestamp_end": "2026-06-21T01:00:10+00:00",
                    "duration_seconds": 10,
                }
                first = manager.ingest(manager._token, {**base_payload, "sequence": 1}, store)
                paused = manager.pause("break")
                skipped = manager.ingest(manager._token, {**base_payload, "sequence": 2}, store)
                resumed = manager.resume()
                third = manager.ingest(
                    manager._token,
                    {
                        **base_payload,
                        "sequence": 3,
                        "timestamp_start": "2026-06-21T01:00:20+00:00",
                        "timestamp_end": "2026-06-21T01:00:30+00:00",
                    },
                    store,
                )
                stopped = manager.stop(store)

        self.assertEqual(first["appended"], 1)
        self.assertTrue(paused["paused"])
        self.assertEqual(skipped["appended"], 0)
        self.assertTrue(skipped["paused"])
        self.assertFalse(resumed["paused"])
        self.assertEqual(len(resumed["pause_intervals"]), 1)
        self.assertEqual(third["appended"], 1)
        self.assertFalse(stopped["active"])
        self.assertEqual(len(store.events), 2)

    def test_cors_origins_follow_configured_local_webui_port(self) -> None:
        with patch.dict("os.environ", {"OPSMINEFLOW_WEBUI_PORT": "5273"}):
            origins = allowed_webui_origins()

        self.assertEqual(origins[0], "http://127.0.0.1:5273")
        self.assertEqual(origins[1], "http://localhost:5273")
        self.assertEqual(origins[2], "tauri://localhost")

    def test_snapshot_contains_local_only_health_and_exports(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        snapshot = create_api_snapshot(EventStore(events=events))

        self.assertTrue(snapshot["health"]["local_only"])
        self.assertFalse(snapshot["health"]["llm_supported"])
        self.assertEqual(snapshot["summary"]["total_events"], 7)
        self.assertIn("flowchart LR", snapshot["mermaid"])
        self.assertIn("<mxfile", snapshot["drawio"])

    def test_runtime_health_identity_is_present_only_for_an_owned_sidecar(self) -> None:
        with (
            patch("opsmineflow_api.app._RUNTIME_NONCE", "sidecar-owner-nonce"),
            patch("opsmineflow_api.app._RUNTIME_PROBE_SECRET", "probe-secret"),
        ):
            health = create_runtime_health("a" * 64)
            public_health = create_runtime_health()

        self.assertEqual(health["runtime"]["nonce"], "sidecar-owner-nonce")
        self.assertIsInstance(health["runtime"]["pid"], int)
        self.assertEqual(len(health["runtime"]["proof"]), 64)
        self.assertNotIn("runtime", public_health)
        self.assertNotIn("storage_mode", health)
        self.assertNotIn("event_count", health)

    def test_runtime_health_route_does_not_create_an_analysis_snapshot(self) -> None:
        from http.server import ThreadingHTTPServer

        server = ThreadingHTTPServer(("127.0.0.1", 0), LocalApiHandler)
        server.security_policy = LocalApiPolicy(  # type: ignore[attr-defined]
            api_session_token="",
            port=server.server_port,
            allowed_origins=set(),
            development_mode=False,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            with patch("opsmineflow_api.server.create_api_snapshot", side_effect=AssertionError("must not analyze")):
                connection.request("GET", "/runtime/health")
                response = connection.getresponse()
                payload = json.loads(response.read())
        finally:
            connection.close()
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["local_only"])

    def test_recording_agent_environment_does_not_inherit_runtime_credentials(self) -> None:
        environment = _recording_agent_environment("recording-token")

        self.assertEqual(environment, {"OPSMINEFLOW_RECORDING_TOKEN": "recording-token"})

    def test_diagnostic_subprocess_environment_does_not_inherit_runtime_credentials(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "PATH": "/usr/bin:/bin",
                "OPSMINEFLOW_RUNTIME_SECRET": "runtime-secret",
                "OPSMINEFLOW_RUNTIME_NONCE": "runtime-nonce",
                "OPSMINEFLOW_RUNTIME_PROBE_SECRET": "runtime-probe-secret",
                "PYTHONPATH": "/private/pythonpath",
            },
            clear=True,
        ):
            environment = sanitized_subprocess_environment()

        self.assertEqual(environment, {"PATH": "/usr/bin:/bin"})

    def test_recording_agent_version_probe_does_not_receive_runtime_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_path = root / "probe-agent.sh"
            probe_path = root / "environment.txt"
            agent_path.write_text(
                "#!/bin/bash\n"
                "if [[ ${1:-} == --version ]]; then\n"
                "  printf '%s|%s|%s' \"${OPSMINEFLOW_RUNTIME_SECRET-}\" \"${OPSMINEFLOW_RUNTIME_NONCE-}\" \"${PYTHONPATH-}\" > \"$(dirname \"$0\")/environment.txt\"\n"
                "  echo 'opsmineflow-agent test'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            agent_path.chmod(0o755)
            manager = RecordingManager(agent_path=agent_path, platform_name="Darwin")
            with patch.dict(
                "os.environ",
                {
                    "PATH": "/usr/bin:/bin",
                    "OPSMINEFLOW_RUNTIME_SECRET": "runtime-secret",
                    "OPSMINEFLOW_RUNTIME_NONCE": "runtime-nonce",
                    "PYTHONPATH": "/private/pythonpath",
                },
                clear=True,
            ):
                availability = manager.availability()
            observed_environment = probe_path.read_text(encoding="utf-8")

        self.assertEqual(availability["agent_version"], "opsmineflow-agent test")
        self.assertEqual(observed_environment, "||")

    def test_event_quality_report_flags_and_approves_issues(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        unlabeled = replace(
            events[0],
            event_id="evt-quality-unlabeled",
            activity_raw="Unlabeled activity",
            activity_normalized="unlabeled activity",
            duration_seconds=0,
        )
        reversed_time = replace(
            events[1],
            event_id="evt-quality-time",
            timestamp_start="2026-06-21T02:00:00+00:00",
            timestamp_end="2026-06-21T01:00:00+00:00",
        )
        long_event = replace(events[2], event_id="evt-quality-long", duration_seconds=3600)
        store = EventStore(events=[unlabeled, reversed_time, long_event])

        report = create_event_quality_report(store)
        store.set_event_quality_review("evt-quality-unlabeled", "approved")
        reviewed_report = create_event_quality_report(store)

        self.assertEqual(report["summary"]["affected_event_count"], 3)
        self.assertGreaterEqual(report["summary"]["zero_duration"], 1)
        self.assertGreaterEqual(report["summary"]["invalid_time"], 1)
        self.assertGreaterEqual(report["summary"]["long_duration"], 1)
        self.assertGreaterEqual(report["summary"]["unlabeled"], 1)
        self.assertEqual(reviewed_report["summary"]["approved_count"], 1)
        self.assertEqual(reviewed_report["summary"]["affected_event_count"], 2)

    def test_sqlite_store_persists_events_labels_and_settings(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            store = EventStore(events=events, db_path=db_path)
            store.set_label(events[0].event_id, "Reviewed")
            store.set_automation_review("社内確認", "adopted", "部門確認後に採用")
            store.update_settings({"retention_days": 14, "mask_url_paths": True})
            store.record_import("csv", "data/sample/sample_events.csv", len(events))

            reopened = EventStore(db_path=db_path)

        self.assertEqual(len(reopened.events), 7)
        self.assertEqual(reopened.manual_labels[events[0].event_id], "Reviewed")
        self.assertEqual(reopened.automation_reviews["社内確認"], "adopted")
        self.assertEqual(reopened.automation_review_notes["社内確認"], "部門確認後に採用")
        self.assertEqual(reopened.get_settings()["retention_days"], 14)
        self.assertEqual(reopened.list_import_history()[0]["event_count"], 7)

    def test_timeline_edit_operations_update_persistent_events(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            store = EventStore(events=events, db_path=db_path)
            first_event_id = store.events[0].event_id

            updated = store.update_event_activity(first_event_id, "請求レビュー")
            split = store.split_event(str(updated["event_id"]), 120, "請求レビュー前半", "請求レビュー後半")
            split_ids = [str(item["event_id"]) for item in split["events"]]
            reopened = EventStore(db_path=db_path)
            merged = reopened.merge_adjacent_events(split_ids[0], split_ids[1], "請求レビュー統合")
            reopened.exclude_event(str(merged["event"]["event_id"]))
            final_store = EventStore(db_path=db_path)

        self.assertEqual(len(final_store.events), 6)
        self.assertNotIn("請求レビュー統合", {event.activity_raw for event in final_store.events})
        self.assertEqual(create_api_snapshot(final_store)["summary"]["total_events"], 6)

    def test_diagnostics_exposes_storage_and_local_only_policy(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        snapshot = create_diagnostics(EventStore(events=events))

        self.assertEqual(snapshot["api"]["bind"], "127.0.0.1")
        self.assertIn("webui", snapshot)
        self.assertIn("dependencies", snapshot)
        self.assertIn("ports", snapshot)
        self.assertIn("guardrails", snapshot)
        self.assertIn("recording", snapshot)
        self.assertIn("privacy_evidence", snapshot)
        self.assertEqual(snapshot["privacy_evidence"]["status"], "passed")
        self.assertTrue(all(item["status"] == "not_collected" for item in snapshot["privacy_evidence"]["items"]))
        self.assertEqual(snapshot["activitywatch"]["status"], "disabled")
        self.assertTrue(snapshot["runtime_policy"]["local_only"])
        self.assertEqual(snapshot["storage"]["event_count"], 7)

    def test_diagnostic_checks_run_local_guardrails(self) -> None:
        results = run_diagnostic_checks()

        self.assertEqual(
            results["license_policy"]["status"],
            "passed",
            results["license_policy"].get("output", ""),
        )
        self.assertEqual(
            results["local_network_policy"]["status"],
            "passed",
            results["local_network_policy"].get("output", ""),
        )

    def test_import_preview_and_store_import_history(self) -> None:
        preview = create_import_preview("csv", "data/sample/sample_events.csv")
        store = EventStore()
        result = import_path_into_store("csv", "data/sample/sample_events.csv", store=store)

        self.assertEqual(preview["event_count"], 7)
        self.assertEqual(preview["display_name"], "sample_events.csv")
        self.assertEqual(result["imported_events"], 7)
        self.assertEqual(store.list_import_history()[0]["source"], "csv")
        self.assertEqual(store.list_import_history()[0]["path"], "sample_events.csv")

    def test_event_page_bounds_dashboard_transport(self) -> None:
        store = EventStore(events=load_events_from_csv("data/sample/sample_events.csv"))

        page = create_event_page(offset=2, limit=3, store=store)

        self.assertEqual(len(page["events"]), 3)
        self.assertEqual(page["offset"], 2)
        self.assertEqual(page["total"], 7)
        self.assertTrue(page["has_more"])
        with self.assertRaisesRegex(ValueError, "between 1 and 500"):
            create_event_page(offset=0, limit=501, store=store)
        with self.assertRaisesRegex(ValueError, "must not be negative"):
            create_event_page(offset=-1, limit=1, store=store)

    def test_event_page_projects_only_the_webui_contract(self) -> None:
        source_event = load_events_from_csv("data/sample/sample_events.csv")[0]
        event = replace(
            source_event,
            window_title="PRIVATE WINDOW TITLE",
            url="private-secret-path",
            metadata_json=json.dumps({"unbounded": "x" * (512 * 1024)}),
        )

        page = create_event_page(store=EventStore(events=[event]))
        record = page["events"][0]

        self.assertEqual(
            set(record),
            {
                "event_id",
                "case_id",
                "user_hash",
                "app_name",
                "window_title_masked",
                "url_masked",
                "domain",
                "activity_raw",
                "timestamp_start",
                "timestamp_end",
                "duration_seconds",
                "confidential_flag",
                "quality_review_status",
            },
        )
        self.assertNotIn("window_title", record)
        self.assertNotIn("url", record)
        self.assertNotIn("metadata_json", record)
        self.assertNotIn("user_alias", record)
        self.assertNotIn("PRIVATE WINDOW TITLE", json.dumps(page))
        self.assertLess(len(json.dumps(page)), 16 * 1024)

    def test_event_page_stays_below_the_ipc_response_budget(self) -> None:
        source_event = load_events_from_csv("data/sample/sample_events.csv")[0]
        events = [
            replace(
                source_event,
                event_id=f"evt-page-{index}",
                window_title="x" * (64 * 1024),
                window_title_masked="masked",
            )
            for index in range(60)
        ]
        store = EventStore(events=events)
        store.update_settings({"mask_window_titles": False})

        page = create_event_page(offset=0, limit=500, store=store)

        self.assertLess(len(json.dumps(page, ensure_ascii=False).encode("utf-8")), 3_100_000)
        self.assertLess(len(page["events"]), 60)
        self.assertTrue(page["has_more"])

    def test_csv_import_preview_accepts_column_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "client.csv"
            path.write_text(
                "案件,作業,開始,終了,担当者,利用アプリ,URL\n"
                "C-1,契約確認,2026/06/01 09:00,2026/06/01 09:10,佐藤,Chrome,http://127.0.0.1/detail\n",
                encoding="utf-8",
            )
            mapping = {
                "case_id": "案件",
                "activity": "作業",
                "timestamp_start": "開始",
                "timestamp_end": "終了",
                "user": "担当者",
                "app_name": "利用アプリ",
                "url": "URL",
            }
            preview = create_import_preview("csv", str(path), mapping, "%Y/%m/%d %H:%M", "Asia/Tokyo")
            store = EventStore()
            result = import_path_into_store(
                "csv",
                str(path),
                store=store,
                mapping=mapping,
                date_format="%Y/%m/%d %H:%M",
                timezone_name="Asia/Tokyo",
            )

        self.assertEqual(preview["columns"], ["案件", "作業", "開始", "終了", "担当者", "利用アプリ", "URL"])
        self.assertEqual(preview["sample_rows"][0]["作業"], "契約確認")
        self.assertEqual(preview["event_count"], 1)
        self.assertEqual(preview["sample_events"][0]["duration_seconds"], 600)
        self.assertEqual(result["imported_events"], 1)
        self.assertEqual(store.events[0].case_id, "C-1")
        self.assertEqual(store.events[0].domain, "127.0.0.1")

    def test_activitywatch_preview_requires_explicit_enable(self) -> None:
        with patch("opsmineflow_api.app.import_activitywatch_local") as mocked_import:
            preview = create_activitywatch_preview(False, store=EventStore())

        mocked_import.assert_not_called()
        self.assertFalse(preview["enabled"])
        self.assertEqual(preview["event_count"], 0)
        self.assertIn("disabled", preview["message"])

    def test_activitywatch_preview_summarizes_duplicates_and_filters(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        store = EventStore(events=[events[0]])
        store.update_settings({"excluded_apps": [events[1].app_name]})

        with patch("opsmineflow_api.app.import_activitywatch_local", return_value=events[:3]):
            preview = create_activitywatch_preview(True, store=store)

        self.assertTrue(preview["enabled"])
        self.assertEqual(preview["event_count"], 3)
        self.assertEqual(preview["importable_event_count"], 2)
        self.assertEqual(preview["duplicate_count"], 1)
        self.assertEqual(preview["new_event_count"], 1)
        self.assertEqual(preview["excluded_event_count"], 1)
        self.assertEqual(preview["app_usage_seconds"][events[0].app_name], events[0].duration_seconds)
        self.assertTrue(preview["period_start"])
        self.assertTrue(preview["period_end"])

    def test_activitywatch_skip_duplicates_appends_and_records_history(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        store = EventStore(events=[events[0]])

        with patch("opsmineflow_api.app.import_activitywatch_local", return_value=events[:3]):
            result = import_activitywatch_into_store(True, mode="skip_duplicates", store=store)

        self.assertEqual(result["imported_events"], 2)
        self.assertEqual(result["skipped_duplicates"], 1)
        self.assertEqual(len(store.events), 3)
        self.assertEqual(store.list_import_history()[0]["source"], "activitywatch_local_skip_duplicates")

    def test_clear_persists_initialized_empty_state(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            store = EventStore(events=events, db_path=db_path)
            store.clear()
            reopened = EventStore(db_path=db_path)

        self.assertTrue(reopened.is_initialized())
        self.assertEqual(reopened.events, [])

    def test_settings_filter_events_and_normalize_values(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        store = EventStore(events=events)
        settings = store.update_settings(
            {
                "retention_days": 999,
                "excluded_apps": "Slack, slack",
                "excluded_domains": "example.local",
            }
        )

        self.assertEqual(settings["retention_days"], 365)
        self.assertEqual(settings["excluded_apps"], ["Slack"])
        self.assertEqual(len(store.events), 4)
        self.assertNotIn("Slack", {event.app_name for event in store.events})

    def test_snapshot_respects_masking_settings(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        store = EventStore(events=events)
        masked_snapshot = create_api_snapshot(store)
        store.update_settings({"mask_url_paths": False})
        unmasked_snapshot = create_api_snapshot(store)

        masked_chrome = next(event for event in masked_snapshot["events"] if event["app_name"] == "Chrome")
        unmasked_chrome = next(event for event in unmasked_snapshot["events"] if event["app_name"] == "Chrome")
        self.assertNotIn("/search", str(masked_chrome["url_masked"]))
        self.assertIn("/search", str(unmasked_chrome["url_masked"]))

    def test_export_preview_and_save_artifact(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        store = EventStore(events=events)
        artifact = create_export_artifact("markdown", store=store)

        with tempfile.TemporaryDirectory() as temp_dir:
            requested_path = Path(temp_dir) / "map"
            result = save_export_artifact("drawio", str(requested_path), store=store)
            saved_path = requested_path.with_suffix(".drawio")

        self.assertEqual(artifact["format"], "markdown")
        self.assertIn("Review masked fields", artifact["warning"])
        self.assertTrue(saved_path.name.endswith(".drawio"))
        self.assertEqual(result["filename"], "map.drawio")
        self.assertNotIn("path", result)
        self.assertGreater(result["byte_size"], 0)

    def test_export_save_requires_explicit_overwrite_and_uses_the_selected_existing_folder(self) -> None:
        store = EventStore(events=load_events_from_csv("data/sample/sample_events.csv"))
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "existing.md"
            target.write_text("previous export", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Confirm replacement"):
                save_export_artifact("markdown", str(target), store=store)

            result = save_export_artifact("markdown", str(target), store=store, overwrite_confirmed=True)

            self.assertTrue(result["saved"])
            self.assertNotEqual(target.read_text(encoding="utf-8"), "previous export")
            self.assertEqual(list(Path(temp_dir).glob(".opsmineflow-export-*")), [])

    def test_export_save_rejects_a_symlink_or_directory_target(self) -> None:
        store = EventStore(events=load_events_from_csv("data/sample/sample_events.csv"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            directory_target = root / "selected.md"
            directory_target.mkdir()
            symlink_target = root / "linked.md"
            symlink_target.symlink_to(directory_target, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "regular export filename"):
                save_export_artifact("markdown", str(directory_target), store=store)
            with self.assertRaisesRegex(ValueError, "regular export filename"):
                save_export_artifact("markdown", str(symlink_target), store=store)

    def test_automation_review_state_is_exposed_and_exported(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        store = EventStore(events=events)
        store.set_automation_review("社内確認", "on_hold", "Slack運用の例外確認が必要")
        snapshot = create_api_snapshot(store)
        reviewed = next(item for item in snapshot["automation_candidates"] if item["activity"] == "社内確認")

        self.assertEqual(reviewed["review_status"], "on_hold")
        self.assertEqual(reviewed["review_note"], "Slack運用の例外確認が必要")
        self.assertIn("impact_score", reviewed)
        self.assertIn("implementation_difficulty", reviewed)
        self.assertIn("risk_level", reviewed)
        self.assertIn("required_data", reviewed)
        self.assertIn("## Automation Priority Portfolio", snapshot["markdown_report"])
        self.assertIn("Slack運用の例外確認が必要", snapshot["markdown_report"])
        self.assertIn("社内確認: review on_hold", snapshot["markdown_report"])


if __name__ == "__main__":
    unittest.main()
