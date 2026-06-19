from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opsmineflow_api.app import create_api_snapshot, create_diagnostics
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
            store.update_settings({"retention_days": 14, "mask_url_paths": True})

            reopened = EventStore(db_path=db_path)

        self.assertEqual(len(reopened.events), 7)
        self.assertEqual(reopened.manual_labels[events[0].event_id], "Reviewed")
        self.assertEqual(reopened.get_settings()["retention_days"], 14)

    def test_diagnostics_exposes_storage_and_local_only_policy(self) -> None:
        events = load_events_from_csv("data/sample/sample_events.csv")
        snapshot = create_diagnostics(EventStore(events=events))

        self.assertEqual(snapshot["api"]["bind"], "127.0.0.1")
        self.assertTrue(snapshot["runtime_policy"]["local_only"])
        self.assertEqual(snapshot["storage"]["event_count"], 7)


if __name__ == "__main__":
    unittest.main()
