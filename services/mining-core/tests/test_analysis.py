from __future__ import annotations

import hashlib
import json
import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from opsmineflow_mining import MiningConfig, StandardEvent, prepare_analysis, sessionize_events
from opsmineflow_mining.analysis import _event_fingerprint
from opsmineflow_mining.pipeline import analyze_variants, build_directly_follows_graph, calculate_duration_metrics


ROOT = Path(__file__).resolve().parents[3]


def event(
    event_id: str,
    activity: str,
    start: str,
    end: str,
    *,
    case_id: str = "CASE-OBSERVED",
    source_event_id: str | None = None,
    duration_seconds: float = 60.0,
    idle: bool = False,
) -> StandardEvent:
    return StandardEvent(
        event_id=event_id,
        source="golden",
        source_event_id=source_event_id or event_id,
        case_id=case_id,
        session_id=f"{case_id}:source-session",
        user_alias="fixture-user",
        user_hash="user_fixture",
        device_id="fixture-mac",
        app_name="Fixture App",
        app_bundle_id="example.fixture",
        window_title="Fixture title",
        window_title_masked="Fixture title",
        url="",
        url_masked="",
        domain="",
        activity_raw=activity,
        activity_normalized=activity.casefold(),
        event_type="fixture",
        timestamp_start=start,
        timestamp_end=end,
        duration_seconds=duration_seconds,
        idle_flag=idle,
        confidential_flag=False,
        metadata_json=json.dumps(
            {
                "opsmineflow_case_correlation": {
                    "origin": "observed" if not case_id.startswith("CASE-UNASSIGNED-") else "unassigned",
                    "strategy": "fixture_source_case_id"
                    if not case_id.startswith("CASE-UNASSIGNED-")
                    else "fixture_singleton",
                    "confidence": "high" if not case_id.startswith("CASE-UNASSIGNED-") else "low",
                    "evidence": "Golden fixture case provenance.",
                }
            },
            sort_keys=True,
        ),
        created_at="2026-01-01T00:00:00+00:00",
    )


class AnalysisPreparationTests(unittest.TestCase):
    def test_event_fingerprint_preserves_the_legacy_canonical_payload(self) -> None:
        fixture = event("evt-fingerprint", "A", "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00")
        legacy_payload = fixture.to_dict()
        legacy_payload.pop("event_id")
        legacy_payload.pop("created_at")
        expected = hashlib.sha256(
            json.dumps(legacy_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        self.assertEqual(_event_fingerprint(fixture), expected)
        object.__setattr__(fixture, "transient_secret", "must-not-affect-analysis-receipts")
        self.assertEqual(_event_fingerprint(fixture), expected)

    def test_session_gap_boundary_and_mixed_offsets_are_deterministic(self) -> None:
        events = [
            event("evt-a", "A", "2026-01-01T09:00:00+09:00", "2026-01-01T09:01:00+09:00"),
            event("evt-b", "B", "2025-12-31T19:31:00-05:00", "2025-12-31T19:32:00-05:00"),
            event("evt-c", "C", "2025-12-31T20:02:01-05:00", "2025-12-31T20:03:01-05:00"),
        ]

        default_analysis = prepare_analysis(events)
        short_gap_analysis = prepare_analysis(events, MiningConfig(session_gap_minutes=15))

        self.assertEqual(default_analysis.receipt.analysis_case_count, 2)
        self.assertEqual(short_gap_analysis.receipt.analysis_case_count, 3)
        self.assertEqual(len(sessionize_events(events, gap_minutes=30)), 2)
        graph = build_directly_follows_graph(default_analysis)
        self.assertEqual(graph["edges"], [{"source": "A", "target": "B", "frequency": 1, "average_transition_seconds": 1800.0}])
        self.assertEqual(analyze_variants(default_analysis)[0]["variant"], ["A", "B"])

    def test_dst_fall_back_uses_utc_instants_not_local_clock_order(self) -> None:
        events = [
            event("evt-before-fall-back", "Before fallback", "2026-11-01T01:30:00-04:00", "2026-11-01T01:35:00-04:00", duration_seconds=300),
            event("evt-after-fall-back", "After fallback", "2026-11-01T01:00:00-05:00", "2026-11-01T01:05:00-05:00", duration_seconds=300),
        ]

        analysis = prepare_analysis(list(reversed(events)))
        graph = build_directly_follows_graph(analysis)

        self.assertEqual(analysis.receipt.used_event_count, 2)
        self.assertEqual(
            graph["edges"],
            [{"source": "Before fallback", "target": "After fallback", "frequency": 1, "average_transition_seconds": 1500.0}],
        )

    def test_unassigned_events_are_singletons_not_a_guessed_domain_flow(self) -> None:
        events = [
            event("evt-u1", "Open", "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00", case_id="CASE-UNASSIGNED-00000001"),
            event("evt-u2", "Close", "2026-01-01T00:02:00+00:00", "2026-01-01T00:03:00+00:00", case_id="CASE-UNASSIGNED-00000002"),
        ]

        analysis = prepare_analysis(events)
        graph = build_directly_follows_graph(analysis)

        self.assertEqual(analysis.receipt.analysis_case_count, 2)
        self.assertEqual(analysis.receipt.case_origin_counts, {"unassigned": 2})
        self.assertEqual(graph["edges"], [])
        self.assertEqual(sum(item["count"] for item in analyze_variants(analysis)), 2)

    def test_overlap_duplicate_idle_and_invalid_events_are_reason_coded(self) -> None:
        overlapping = [
            event("evt-o1", "A", "2026-01-01T00:00:00+00:00", "2026-01-01T00:10:00+00:00", duration_seconds=600),
            event("evt-o2", "B", "2026-01-01T00:05:00+00:00", "2026-01-01T00:15:00+00:00", duration_seconds=600),
            event("evt-o3", "C", "2026-01-01T00:16:00+00:00", "2026-01-01T00:17:00+00:00"),
        ]
        duplicate = event("evt-d1", "D", "2026-01-01T01:00:00+00:00", "2026-01-01T01:01:00+00:00", case_id="CASE-D")
        idle = event("evt-idle", "Idle", "2026-01-01T02:00:00+00:00", "2026-01-01T02:01:00+00:00", idle=True)
        invalid = event("evt-invalid", "Invalid", "2026-01-01T03:01:00+00:00", "2026-01-01T03:00:00+00:00")

        analysis = prepare_analysis([*overlapping, duplicate, duplicate, idle, invalid])

        self.assertEqual(analysis.receipt.input_event_count, 7)
        self.assertEqual(analysis.receipt.used_event_count, 1)
        self.assertEqual(analysis.receipt.excluded_event_count, 6)
        self.assertEqual(
            analysis.receipt.excluded_by_reason,
            {
                "duplicate_event": 1,
                "idle_event": 1,
                "negative_interval": 1,
                "overlapping_or_parallel_session": 3,
            },
        )
        self.assertEqual(build_directly_follows_graph(analysis)["nodes"][0]["activity"], "D")
        self.assertEqual(calculate_duration_metrics(analysis).total_events, 1)

    def test_conflicting_source_identity_is_fail_closed_and_empty_is_finite(self) -> None:
        left = event("evt-left", "Left", "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00", source_event_id="shared")
        right = replace(left, event_id="evt-right", activity_raw="Right", activity_normalized="right")
        reimported_duplicate = replace(left, created_at="2026-01-02T00:00:00+00:00")

        analysis = prepare_analysis([left, right])
        duplicate_analysis = prepare_analysis([left, reimported_duplicate])
        empty = prepare_analysis([])

        self.assertEqual(analysis.receipt.used_event_count, 0)
        self.assertEqual(analysis.receipt.excluded_by_reason, {"conflicting_source_event_id": 2})
        self.assertEqual(build_directly_follows_graph(analysis)["nodes"], [])
        self.assertEqual(duplicate_analysis.receipt.used_event_count, 1)
        self.assertEqual(duplicate_analysis.receipt.excluded_by_reason, {"duplicate_event": 1})
        self.assertEqual(empty.receipt.used_event_count, 0)
        self.assertEqual(calculate_duration_metrics(empty).average_event_duration_seconds, 0.0)
        json.dumps(build_directly_follows_graph(empty), allow_nan=False)

    def test_permutation_does_not_change_receipt_graph_or_variants(self) -> None:
        events = [
            event("evt-3", "C", "2026-01-01T00:02:00+00:00", "2026-01-01T00:03:00+00:00"),
            event("evt-1", "A", "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00"),
            event("evt-2", "B", "2026-01-01T00:01:00+00:00", "2026-01-01T00:02:00+00:00"),
        ]

        first = prepare_analysis(events)
        second = prepare_analysis(list(reversed(events)))

        self.assertEqual(first.receipt.to_dict(), second.receipt.to_dict())
        self.assertEqual(build_directly_follows_graph(first), build_directly_follows_graph(second))
        self.assertEqual(analyze_variants(first), analyze_variants(second))

    def test_scope_and_filter_fingerprints_are_deterministic_and_distinguish_context(self) -> None:
        events = [
            event("evt-1", "A", "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00"),
            event("evt-2", "B", "2026-01-01T00:01:00+00:00", "2026-01-01T00:02:00+00:00"),
        ]
        base = prepare_analysis(events, MiningConfig(filter_context=(("excluded_apps", ("chat",)),)))
        permuted = prepare_analysis(list(reversed(events)), MiningConfig(filter_context=(("excluded_apps", ("chat",)),)))
        changed_scope = prepare_analysis([replace(events[0], activity_raw="A revised"), events[1]], MiningConfig(filter_context=(("excluded_apps", ("chat",)),)))
        changed_filter = prepare_analysis(events, MiningConfig(filter_context=(("excluded_apps", ("mail",)),)))

        self.assertEqual(base.receipt.scope_fingerprint, permuted.receipt.scope_fingerprint)
        self.assertEqual(base.receipt.filter_fingerprint, permuted.receipt.filter_fingerprint)
        self.assertNotEqual(base.receipt.scope_fingerprint, changed_scope.receipt.scope_fingerprint)
        self.assertNotEqual(base.receipt.filter_fingerprint, changed_filter.receipt.filter_fingerprint)
        self.assertRegex(base.receipt.scope_fingerprint, r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(base.receipt.filter_fingerprint, r"^sha256:[0-9a-f]{64}$")

    def test_single_and_large_cases_stay_finite_and_match_checked_in_snapshot(self) -> None:
        fixture = json.loads((ROOT / "services/mining-core/tests/fixtures/mixed-offset-golden.json").read_text(encoding="utf-8"))
        fixture_events = [
            event("fixture-a", "A", "2026-01-01T09:00:00+09:00", "2026-01-01T09:01:00+09:00"),
            event("fixture-b", "B", "2025-12-31T19:31:00-05:00", "2025-12-31T19:32:00-05:00"),
        ]
        fixture_analysis = prepare_analysis(fixture_events)
        self.assertEqual(build_directly_follows_graph(fixture_analysis)["edges"], fixture["edges"])
        self.assertEqual(analyze_variants(fixture_analysis), fixture["variants"])

        single = prepare_analysis([event("single", "Only", "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00")])
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        large = prepare_analysis(
            [
                event(
                    f"large-{index}",
                    f"Step {index % 5}",
                    (base + timedelta(minutes=index)).isoformat(),
                    (base + timedelta(minutes=index + 1)).isoformat(),
                )
                for index in range(256)
            ]
        )

        for analysis in (single, large):
            json.dumps(analysis.receipt.to_dict(), allow_nan=False)
            json.dumps(build_directly_follows_graph(analysis), allow_nan=False)
            json.dumps(analyze_variants(analysis), allow_nan=False)
            json.dumps(calculate_duration_metrics(analysis).__dict__, allow_nan=False)
        self.assertEqual(single.receipt.used_event_count, 1)
        self.assertEqual(large.receipt.used_event_count, 256)

    def test_generated_property_permutation_gap_and_offset_invariants(self) -> None:
        """A fixed-seed generative/property test without a runtime dependency.

        Every generated log uses multiple cases, UTC-equivalent offset forms,
        and gaps around the configured boundary.  The properties are more
        valuable here than random test flakiness: a failing seed is stable and
        the same generated evidence can be inspected locally.
        """

        for seed in range(32):
            rng = random.Random(seed)
            base = datetime(2026, 1, 1, tzinfo=timezone.utc)
            generated: list[StandardEvent] = []
            for case_index in range(1, 4):
                instant = base + timedelta(hours=case_index)
                for sequence in range(1, 9):
                    offset_hours = rng.choice((-5, 0, 9))
                    start = instant.astimezone(timezone(timedelta(hours=offset_hours)))
                    end = (instant + timedelta(seconds=60)).astimezone(timezone(timedelta(hours=offset_hours)))
                    generated.append(
                        event(
                            f"seed-{seed}-case-{case_index}-{sequence}",
                            f"Step {sequence % 3}",
                            start.isoformat(),
                            end.isoformat(),
                            case_id=f"CASE-{case_index}",
                        )
                    )
                    instant += timedelta(minutes=rng.choice((1, 30, 31, 60)))

            gap = rng.choice((0, 30, 60))
            config = MiningConfig(session_gap_minutes=gap)
            shuffled = list(generated)
            rng.shuffle(shuffled)
            first = prepare_analysis(generated, config)
            second = prepare_analysis(shuffled, config)

            with self.subTest(seed=seed, gap=gap):
                self.assertEqual(first.receipt.to_dict(), second.receipt.to_dict())
                self.assertEqual(build_directly_follows_graph(first), build_directly_follows_graph(second))
                self.assertEqual(analyze_variants(first), analyze_variants(second))
                self.assertEqual(
                    first.receipt.used_event_count + first.receipt.excluded_event_count,
                    first.receipt.input_event_count,
                )
                json.dumps(first.receipt.to_dict(), allow_nan=False)
                json.dumps(build_directly_follows_graph(first), allow_nan=False)
                json.dumps(analyze_variants(first), allow_nan=False)

    def test_duration_interval_mismatch_is_excluded_and_canonical_interval_is_used(self) -> None:
        mismatched = event(
            "evt-mismatch",
            "Mismatch",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:05:00+00:00",
            duration_seconds=1,
        )
        rounded = event(
            "evt-rounded",
            "Rounded",
            "2026-01-01T01:00:00+00:00",
            "2026-01-01T01:05:00+00:00",
            duration_seconds=300.5,
        )

        analysis = prepare_analysis([mismatched, rounded])

        self.assertEqual(analysis.receipt.excluded_by_reason, {"duration_interval_mismatch": 1})
        self.assertEqual(analysis.receipt.raw_active_seconds, 300.0)
        self.assertEqual(analysis.receipt.active_union_seconds, 300.0)
        self.assertEqual(analysis.events[0].duration_seconds, 300.0)


if __name__ == "__main__":
    unittest.main()
