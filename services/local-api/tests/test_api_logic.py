from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opsmineflow_api.app import (
    create_api_snapshot,
    create_diagnostics,
    create_export_artifact,
    create_import_preview,
    import_path_into_store,
    save_export_artifact,
)
from opsmineflow_api.storage import EventStore
from opsmineflow_mining import load_events_from_csv


class ApiLogicTests(unittest.TestCase):
    def test_snapshot_contains_local_only_health_and_exports(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        snapshot = create_api_snapshot(EventStore(events=events))

        self.assertTrue(snapshot["health"]["local_only"])
        self.assertFalse(snapshot["health"]["llm_supported"])
        self.assertEqual(snapshot["summary"]["total_events"], 7)
        self.assertIn("flowchart LR", snapshot["mermaid"])
        self.assertIn("<mxfile", snapshot["drawio"])

    def test_sqlite_store_persists_events_labels_and_settings(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "opsmineflow.sqlite3"
            store = EventStore(events=events, db_path=db_path)
            store.set_label(events[0].event_id, "Reviewed")
            store.set_automation_review("社内確認", "adopted")
            store.update_settings({"retention_days": 14, "mask_url_paths": True})
            store.record_import("csv", "data/sample/sample_events.csv", len(events))

            reopened = EventStore(db_path=db_path)

        self.assertEqual(len(reopened.events), 7)
        self.assertEqual(reopened.manual_labels[events[0].event_id], "Reviewed")
        self.assertEqual(reopened.automation_reviews["社内確認"], "adopted")
        self.assertEqual(reopened.get_settings()["retention_days"], 14)
        self.assertEqual(reopened.list_import_history()[0]["event_count"], 7)

    def test_diagnostics_exposes_storage_and_local_only_policy(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        snapshot = create_diagnostics(EventStore(events=events))

        self.assertEqual(snapshot["api"]["bind"], "127.0.0.1")
        self.assertTrue(snapshot["runtime_policy"]["local_only"])
        self.assertEqual(snapshot["storage"]["event_count"], 7)

    def test_import_preview_and_store_import_history(self) -> None:
        preview = create_import_preview("csv", "data/sample/sample_events.csv")
        store = EventStore()
        result = import_path_into_store("csv", "data/sample/sample_events.csv", store=store)

        self.assertEqual(preview["event_count"], 7)
        self.assertEqual(result["imported_events"], 7)
        self.assertEqual(store.list_import_history()[0]["source"], "csv")

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
            result = save_export_artifact("drawio", str(Path(temp_dir) / "map"), store=store)
            saved_path = Path(str(result["path"]))

        self.assertEqual(artifact["format"], "markdown")
        self.assertIn("Review masked fields", artifact["warning"])
        self.assertTrue(saved_path.name.endswith(".drawio"))
        self.assertGreater(result["byte_size"], 0)

    def test_automation_review_state_is_exposed_and_exported(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        store = EventStore(events=events)
        store.set_automation_review("社内確認", "on_hold")
        snapshot = create_api_snapshot(store)
        reviewed = next(item for item in snapshot["automation_candidates"] if item["activity"] == "社内確認")

        self.assertEqual(reviewed["review_status"], "on_hold")
        self.assertIn("## Automation Review Status", snapshot["markdown_report"])
        self.assertIn("社内確認: review on_hold", snapshot["markdown_report"])


if __name__ == "__main__":
    unittest.main()
