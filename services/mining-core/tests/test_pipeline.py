from __future__ import annotations

import unittest
from pathlib import Path

from opsmineflow_mining import (
    analyze_variants,
    build_directly_follows_graph,
    calculate_duration_metrics,
    detect_app_switches,
    detect_bottlenecks,
    export_markdown_report,
    export_mermaid,
    load_events_from_csv,
    score_automation_candidates,
)


ROOT = Path(__file__).resolve().parents[3]


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = load_events_from_csv(ROOT / "data/sample/sample_events.csv")

    def test_calculates_duration_metrics(self) -> None:
        metrics = calculate_duration_metrics(self.events)

        self.assertEqual(metrics.total_events, 7)
        self.assertGreater(metrics.total_active_seconds, 0)
        self.assertIn("Microsoft Excel", metrics.app_usage_seconds)
        self.assertIn("問い合わせ対応", metrics.label_usage_seconds)

    def test_builds_directly_follows_graph(self) -> None:
        graph = build_directly_follows_graph(self.events)

        self.assertGreaterEqual(len(graph["nodes"]), 7)
        self.assertGreaterEqual(len(graph["edges"]), 5)
        self.assertIn("start_activities", graph)

    def test_analyzes_variants_and_bottlenecks(self) -> None:
        variants = analyze_variants(self.events)
        bottlenecks = detect_bottlenecks(self.events)

        self.assertEqual(len(variants), 2)
        self.assertTrue(any(item["activity"] == "社内確認" for item in bottlenecks))

    def test_scores_automation_candidates(self) -> None:
        candidates = score_automation_candidates(self.events)

        self.assertGreaterEqual(len(candidates), 1)
        self.assertIn("automation_score", candidates[0])

    def test_detects_app_switches(self) -> None:
        switches = detect_app_switches(self.events)

        self.assertGreaterEqual(len(switches["transition_ranking"]), 1)

    def test_exports_mermaid_and_markdown(self) -> None:
        mermaid = export_mermaid(self.events)
        markdown = export_markdown_report(self.events)

        self.assertIn("flowchart LR", mermaid)
        self.assertIn("# OpsMineFlow As-Is Report", markdown)
        self.assertIn("LLM integration: not supported", markdown)


if __name__ == "__main__":
    unittest.main()

