from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opsmineflow_mining import (
    inspect_csv_columns,
    load_events_from_csv,
    load_events_from_csv_with_mapping,
    load_events_from_json,
    suggest_csv_mapping,
)


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

    def test_loads_arbitrary_csv_with_mapping_and_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "client.csv"
            path.write_text(
                "案件,作業,開始,終了,担当者,利用アプリ,備考\n"
                "A-1,申請確認,2026/06/01 09:00,2026/06/01 09:07,佐藤,Chrome,申請画面\n",
                encoding="utf-8",
            )
            inspection = inspect_csv_columns(path)
            suggested = suggest_csv_mapping(inspection["columns"])  # type: ignore[arg-type]
            events = load_events_from_csv_with_mapping(
                path,
                suggested,
                date_format="%Y/%m/%d %H:%M",
                timezone_name="Asia/Tokyo",
            )

        self.assertEqual(suggested["case_id"], "案件")
        self.assertEqual(suggested["activity"], "作業")
        self.assertEqual(events[0].case_id, "A-1")
        self.assertEqual(events[0].activity_raw, "申請確認")
        self.assertEqual(events[0].app_name, "Chrome")
        self.assertEqual(events[0].duration_seconds, 420)
        self.assertTrue(events[0].timestamp_start.endswith("+09:00"))


if __name__ == "__main__":
    unittest.main()
