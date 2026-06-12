from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opsmineflow_mining import load_events_from_csv, load_events_from_json


ROOT = Path(__file__).resolve().parents[3]


class ImporterTests(unittest.TestCase):
    def test_loads_sample_csv_into_standard_events(self) -> None:
        events = load_events_from_csv(ROOT / "data/sample/sample_events.csv")

        self.assertEqual(len(events), 7)
        first = events[0]
        self.assertEqual(first.case_id, "CASE-001")
        self.assertEqual(first.app_name, "Outlook")
        self.assertEqual(first.duration_seconds, 300)
        self.assertTrue(first.event_id.startswith("evt_"))
        self.assertTrue(first.user_hash.startswith("user_"))
        self.assertEqual(first.activity_raw, "メール確認")
        self.assertIn("顧客問い合わせ", first.window_title_masked)

    def test_loads_activitywatch_style_json(self) -> None:
        events = load_events_from_json(ROOT / "data/sample/sample_activitywatch_export.json")

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].source, "activitywatch_export")
        self.assertEqual(events[1].domain, "core.example.local")
        self.assertIn("[masked]", events[1].url_masked)

    def test_loads_generic_json_events(self) -> None:
        payload = [
            {
                "case_id": "CASE-X",
                "activity": "Review request",
                "timestamp_start": "2026-06-01T01:00:00+00:00",
                "timestamp_end": "2026-06-01T01:05:00+00:00",
                "user": "consultant",
                "app_name": "Mail"
            }
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            events = load_events_from_json(path)

        self.assertEqual(events[0].case_id, "CASE-X")
        self.assertEqual(events[0].duration_seconds, 300)


if __name__ == "__main__":
    unittest.main()
