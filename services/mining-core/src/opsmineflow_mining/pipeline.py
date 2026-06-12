from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Iterable

from .importers import load_events_from_csv, load_events_from_json
from .models import StandardEvent

DEFAULT_AUTOMATION_WEIGHTS = {
    "repeatability_score": 0.30,
    "volume_score": 0.20,
    "rule_based_score": 0.20,
    "system_handover_score": 0.15,
    "manual_transfer_risk_score": 0.15,
}

LABEL_RULES = [
    ("請求処理", ("excel", "invoice", "請求")),
    ("問い合わせ対応", ("gmail", "outlook", "問い合わせ", "返信")),
    ("申請処理", ("workflow", "申請", "基幹")),
    ("社内確認", ("slack", "teams", "確認")),
    ("台帳更新", ("excel", "台帳", "転記")),
]


@dataclass(frozen=True)
class DurationMetrics:
    total_events: int
    total_active_seconds: float
    period_start: str
    period_end: str
    app_usage_seconds: dict[str, float]
    label_usage_seconds: dict[str, float]
    user_usage_seconds: dict[str, float]
    average_event_duration_seconds: float


def load_events(path: str | Path) -> list[StandardEvent]:
    source_path = Path(path)
    if source_path.suffix.lower() == ".csv":
        return load_events_from_csv(source_path)
    if source_path.suffix.lower() == ".json":
        return load_events_from_json(source_path)
    raise ValueError(f"Unsupported event file type: {source_path.suffix}")


def normalize_events(events: Iterable[StandardEvent]) -> list[StandardEvent]:
    return sorted(events, key=lambda event: (event.case_id, event.timestamp_start, event.event_id))


def mask_sensitive_fields(events: Iterable[StandardEvent]) -> list[StandardEvent]:
    return list(events)


def assign_activity_labels(events: Iterable[StandardEvent]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for event in events:
        haystack = " ".join(
            [
                event.activity_raw,
                event.activity_normalized,
                event.app_name,
                event.domain,
                event.window_title_masked,
            ]
        ).lower()
        labels[event.event_id] = "未分類"
        for label, keywords in LABEL_RULES:
            if any(keyword.lower() in haystack for keyword in keywords):
                labels[event.event_id] = label
                break
    return labels


def sessionize_events(events: Iterable[StandardEvent], gap_minutes: int = 30) -> dict[str, list[StandardEvent]]:
    sessions: dict[str, list[StandardEvent]] = defaultdict(list)
    for event in normalize_events(events):
        sessions[event.session_id].append(event)
    return dict(sessions)


def calculate_duration_metrics(events: Iterable[StandardEvent]) -> DurationMetrics:
    event_list = normalize_events(events)
    labels = assign_activity_labels(event_list)
    app_usage: dict[str, float] = defaultdict(float)
    label_usage: dict[str, float] = defaultdict(float)
    user_usage: dict[str, float] = defaultdict(float)
    for event in event_list:
        duration = 0 if event.idle_flag else event.duration_seconds
        app_usage[event.app_name or "Unknown"] += duration
        label_usage[labels[event.event_id]] += duration
        user_usage[event.user_hash] += duration
    timestamps = [event.timestamp_start for event in event_list] + [event.timestamp_end for event in event_list]
    durations = [event.duration_seconds for event in event_list]
    return DurationMetrics(
        total_events=len(event_list),
        total_active_seconds=sum(app_usage.values()),
        period_start=min(timestamps) if timestamps else "",
        period_end=max(timestamps) if timestamps else "",
        app_usage_seconds=dict(sorted(app_usage.items(), key=lambda item: item[1], reverse=True)),
        label_usage_seconds=dict(sorted(label_usage.items(), key=lambda item: item[1], reverse=True)),
        user_usage_seconds=dict(sorted(user_usage.items(), key=lambda item: item[1], reverse=True)),
        average_event_duration_seconds=mean(durations) if durations else 0.0,
    )


def detect_app_switches(events: Iterable[StandardEvent]) -> dict[str, object]:
    transitions: Counter[tuple[str, str]] = Counter()
    round_trips: Counter[str] = Counter()
    by_case = _events_by_case(events)
    for case_events in by_case.values():
        apps = [event.app_name or "Unknown" for event in case_events]
        for left, right in zip(apps, apps[1:]):
            if left != right:
                transitions[(left, right)] += 1
        for first, middle, last in zip(apps, apps[1:], apps[2:]):
            if first == last and first != middle:
                round_trips[f"{first} -> {middle} -> {last}"] += 1
    return {
        "transition_ranking": [
            {"source_app": source, "target_app": target, "count": count}
            for (source, target), count in transitions.most_common()
        ],
        "round_trips": [
            {"pattern": pattern, "count": count}
            for pattern, count in round_trips.most_common()
        ],
    }


def detect_repeated_patterns(events: Iterable[StandardEvent], window_size: int = 2) -> list[dict[str, object]]:
    patterns: Counter[tuple[str, ...]] = Counter()
    for case_events in _events_by_case(events).values():
        activities = [event.activity_normalized for event in case_events]
        for index in range(0, max(len(activities) - window_size + 1, 0)):
            patterns[tuple(activities[index : index + window_size])] += 1
    return [
        {"pattern": list(pattern), "count": count}
        for pattern, count in patterns.most_common()
        if count > 1
    ]


def build_directly_follows_graph(events: Iterable[StandardEvent]) -> dict[str, object]:
    by_case = _events_by_case(events)
    activity_counts: Counter[str] = Counter()
    activity_durations: dict[str, list[float]] = defaultdict(list)
    transition_counts: Counter[tuple[str, str]] = Counter()
    transition_durations: dict[tuple[str, str], list[float]] = defaultdict(list)
    start_counts: Counter[str] = Counter()
    end_counts: Counter[str] = Counter()

    for case_events in by_case.values():
        if not case_events:
            continue
        start_counts[case_events[0].activity_raw] += 1
        end_counts[case_events[-1].activity_raw] += 1
        for event in case_events:
            activity_counts[event.activity_raw] += 1
            activity_durations[event.activity_raw].append(event.duration_seconds)
        for left, right in zip(case_events, case_events[1:]):
            key = (left.activity_raw, right.activity_raw)
            transition_counts[key] += 1
            transition_durations[key].append(_seconds_between(left.timestamp_end, right.timestamp_start))

    bottlenecks = {item["activity"] for item in detect_bottlenecks_from_counts(activity_durations)}
    automation = {item["activity"] for item in score_automation_candidates_from_counts(activity_counts, transition_counts)}
    nodes = [
        {
            "activity": activity,
            "frequency": count,
            "average_duration_seconds": mean(activity_durations[activity]),
            "bottleneck": activity in bottlenecks,
            "automation_candidate": activity in automation,
        }
        for activity, count in activity_counts.most_common()
    ]
    edges = [
        {
            "source": source,
            "target": target,
            "frequency": count,
            "average_transition_seconds": mean(transition_durations[(source, target)]),
        }
        for (source, target), count in transition_counts.most_common()
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "start_activities": dict(start_counts),
        "end_activities": dict(end_counts),
    }


def analyze_variants(events: Iterable[StandardEvent]) -> list[dict[str, object]]:
    variants: Counter[tuple[str, ...]] = Counter()
    durations: dict[tuple[str, ...], list[float]] = defaultdict(list)
    for case_events in _events_by_case(events).values():
        sequence = tuple(event.activity_raw for event in case_events)
        variants[sequence] += 1
        durations[sequence].append(sum(event.duration_seconds for event in case_events))
    return [
        {
            "variant": list(sequence),
            "count": count,
            "average_case_duration_seconds": mean(durations[sequence]),
        }
        for sequence, count in variants.most_common()
    ]


def detect_bottlenecks(events: Iterable[StandardEvent]) -> list[dict[str, object]]:
    durations: dict[str, list[float]] = defaultdict(list)
    for event in events:
        durations[event.activity_raw].append(event.duration_seconds)
    return detect_bottlenecks_from_counts(durations)


def score_automation_candidates(
    events: Iterable[StandardEvent],
    weights: dict[str, float] | None = None,
) -> list[dict[str, object]]:
    event_list = normalize_events(events)
    activity_counts = Counter(event.activity_raw for event in event_list)
    transition_counts = Counter(
        (left.activity_raw, right.activity_raw)
        for case_events in _events_by_case(event_list).values()
        for left, right in zip(case_events, case_events[1:])
    )
    return score_automation_candidates_from_counts(activity_counts, transition_counts, weights=weights)


def export_mermaid(events: Iterable[StandardEvent]) -> str:
    graph = build_directly_follows_graph(events)
    node_ids = {node["activity"]: f"N{index}" for index, node in enumerate(graph["nodes"], start=1)}
    lines = ["flowchart LR", "  Start((Start))", "  End((End))"]
    for node in graph["nodes"]:
        activity = str(node["activity"])
        label = activity.replace('"', "'")
        lines.append(f'  {node_ids[activity]}["{label}<br/>freq {node["frequency"]}"]')
    for activity in graph["start_activities"]:
        if activity in node_ids:
            lines.append(f"  Start --> {node_ids[activity]}")
    for edge in graph["edges"]:
        source = node_ids[str(edge["source"])]
        target = node_ids[str(edge["target"])]
        lines.append(f'  {source} -->|{edge["frequency"]}| {target}')
    for activity in graph["end_activities"]:
        if activity in node_ids:
            lines.append(f"  {node_ids[activity]} --> End")
    return "\n".join(lines) + "\n"


def export_markdown_report(events: Iterable[StandardEvent]) -> str:
    event_list = normalize_events(events)
    metrics = calculate_duration_metrics(event_list)
    graph = build_directly_follows_graph(event_list)
    variants = analyze_variants(event_list)
    bottlenecks = detect_bottlenecks(event_list)
    candidates = score_automation_candidates(event_list)
    switches = detect_app_switches(event_list)

    lines = [
        "# OpsMineFlow As-Is Report",
        "",
        "## Investigation Overview",
        f"- Events: {metrics.total_events}",
        f"- Period: {metrics.period_start} to {metrics.period_end}",
        f"- Total active seconds: {metrics.total_active_seconds:.0f}",
        "- Data scope: imported local event logs only",
        "- Privacy: URL paths and long or sensitive window titles are masked",
        "- LLM integration: not supported",
        "",
        "## App Usage",
    ]
    lines.extend(_format_metric_table(metrics.app_usage_seconds, "App"))
    lines.extend(["", "## Business Label Usage"])
    lines.extend(_format_metric_table(metrics.label_usage_seconds, "Label"))
    lines.extend(["", "## Main Process Flow"])
    for edge in graph["edges"][:10]:
        lines.append(f'- {edge["source"]} -> {edge["target"]}: {edge["frequency"]} transitions')
    lines.extend(["", "## Variants"])
    for variant in variants[:5]:
        lines.append(f'- {" -> ".join(variant["variant"])}: {variant["count"]} cases')
    lines.extend(["", "## Bottleneck Candidates"])
    for item in bottlenecks[:10]:
        lines.append(f'- {item["activity"]}: avg {item["average_duration_seconds"]:.0f}s')
    lines.extend(["", "## App Switching and Manual Transfer Risk"])
    for item in switches["round_trips"][:10]:
        lines.append(f'- {item["pattern"]}: {item["count"]} times')
    lines.extend(["", "## Automation Candidates"])
    for item in candidates[:10]:
        lines.append(
            f'- {item["activity"]}: score {item["automation_score"]:.2f}, '
            f'class {item["classification"]}, reasons {", ".join(item["reasons"])}'
        )
    lines.extend(
        [
            "",
            "## Additional Interview Topics",
            "- Confirm whether repeated patterns are necessary controls or avoidable rework.",
            "- Review high-duration activities with participants before making recommendations.",
            "- Confirm whether app round trips represent manual transfer, review, or approval work.",
            "",
            "## Data Constraints",
            "- Imported logs may not represent all offline work.",
            "- Rule-based labels are hypotheses and should be reviewed manually.",
            "- Automation scores are prioritization signals, not final business cases.",
        ]
    )
    return "\n".join(lines) + "\n"


def detect_bottlenecks_from_counts(durations: dict[str, list[float]]) -> list[dict[str, object]]:
    if not durations:
        return []
    overall = mean(duration for values in durations.values() for duration in values)
    candidates = []
    for activity, values in durations.items():
        average_duration = mean(values)
        if average_duration >= overall and average_duration >= 300:
            candidates.append(
                {
                    "activity": activity,
                    "average_duration_seconds": average_duration,
                    "frequency": len(values),
                    "reason": "above-average duration",
                }
            )
    return sorted(candidates, key=lambda item: item["average_duration_seconds"], reverse=True)


def score_automation_candidates_from_counts(
    activity_counts: Counter[str],
    transition_counts: Counter[tuple[str, str]],
    weights: dict[str, float] | None = None,
) -> list[dict[str, object]]:
    active_weights = weights or DEFAULT_AUTOMATION_WEIGHTS
    max_volume = max(activity_counts.values(), default=1)
    handover_activities = {
        activity
        for pair, count in transition_counts.items()
        if count > 0
        for activity in pair
    }
    candidates = []
    for activity, count in activity_counts.items():
        repeatability = min(count / 3, 1.0)
        volume = count / max_volume
        lower = activity.lower()
        rule_based = 1.0 if any(word in lower for word in ("確認", "検索", "転記", "review", "check", "copy")) else 0.4
        handover = 1.0 if activity in handover_activities else 0.3
        transfer = 1.0 if any(word in lower for word in ("転記", "excel", "copy")) else 0.3
        score = (
            repeatability * active_weights["repeatability_score"]
            + volume * active_weights["volume_score"]
            + rule_based * active_weights["rule_based_score"]
            + handover * active_weights["system_handover_score"]
            + transfer * active_weights["manual_transfer_risk_score"]
        )
        reasons = []
        if repeatability >= 0.66:
            reasons.append("repeated activity")
        if rule_based >= 1.0:
            reasons.append("rule-based wording")
        if transfer >= 1.0:
            reasons.append("manual transfer risk")
        if handover >= 1.0:
            reasons.append("system handover")
        candidates.append(
            {
                "activity": activity,
                "automation_score": round(score, 4),
                "frequency": count,
                "classification": _classify_candidate(activity, transfer, rule_based),
                "reasons": reasons or ["low-volume hypothesis"],
                "component_scores": {
                    "repeatability_score": round(repeatability, 4),
                    "volume_score": round(volume, 4),
                    "rule_based_score": round(rule_based, 4),
                    "system_handover_score": round(handover, 4),
                    "manual_transfer_risk_score": round(transfer, 4),
                },
            }
        )
    return sorted(candidates, key=lambda item: item["automation_score"], reverse=True)


def _events_by_case(events: Iterable[StandardEvent]) -> dict[str, list[StandardEvent]]:
    grouped: dict[str, list[StandardEvent]] = defaultdict(list)
    for event in events:
        grouped[event.case_id].append(event)
    return {case_id: normalize_events(case_events) for case_id, case_events in grouped.items()}


def _seconds_between(left_iso: str, right_iso: str) -> float:
    return max((_parse_iso(right_iso) - _parse_iso(left_iso)).total_seconds(), 0.0)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_metric_table(values: dict[str, float], label: str) -> list[str]:
    lines = [f"| {label} | Seconds |", "|---|---:|"]
    for key, value in values.items():
        lines.append(f"| {key} | {value:.0f} |")
    return lines


def _classify_candidate(activity: str, transfer: float, rule_based: float) -> str:
    lower = activity.lower()
    if transfer >= 1.0:
        return "rpa"
    if any(word in lower for word in ("申請", "基幹", "workflow")):
        return "system_change"
    if rule_based >= 1.0:
        return "operations_rule_change"
    return "improvement_review"


def metrics_to_dict(metrics: DurationMetrics) -> dict[str, object]:
    return asdict(metrics)

