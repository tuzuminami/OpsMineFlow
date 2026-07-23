"""Deterministic, conservative preparation for local process-mining analyses.

The public pipeline functions consume :class:`PreparedAnalysis` rather than
making independent guesses about event order or case membership.  This keeps
the DFG, variants, durations, UI snapshot, and manual LLM handoff on one
auditable interpretation of the local event log.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Iterable

from .models import StandardEvent


ANALYSIS_ALGORITHM_VERSION = "1.0.0"
DEFAULT_SESSION_GAP_MINUTES = 30
_CASE_ORIGINS = {"observed", "manual", "inferred", "unassigned"}
_CONFIDENCE_LEVELS = {"high", "medium", "low"}
_EVENT_FINGERPRINT_FIELDS = tuple(
    field.name for field in fields(StandardEvent) if field.name not in {"event_id", "created_at"}
)


@dataclass(frozen=True)
class MiningConfig:
    """Stable parameters which define one process-mining result."""

    session_gap_minutes: int = DEFAULT_SESSION_GAP_MINUTES
    filter_context: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.session_gap_minutes, int) or isinstance(self.session_gap_minutes, bool):
            raise ValueError("session_gap_minutes must be a whole number.")
        if self.session_gap_minutes < 0:
            raise ValueError("session_gap_minutes must not be negative.")
        if any(not key or any(not isinstance(value, str) for value in values) for key, values in self.filter_context):
            raise ValueError("filter_context must contain named string filter values.")


@dataclass(frozen=True)
class CaseCorrelation:
    origin: str
    strategy: str
    confidence: str
    evidence: str


@dataclass(frozen=True)
class AnalysisCase:
    """One eligible, sessionized analysis unit.

    ``key`` stays local to the analysis.  It is not a safe export field.
    """

    key: str
    events: tuple[StandardEvent, ...]
    correlation: CaseCorrelation


@dataclass(frozen=True)
class AnalysisReceipt:
    """Counts and definitions needed to interpret an analysis safely."""

    algorithm_version: str
    session_gap_minutes: int
    scope_fingerprint: str
    filter_fingerprint: str
    input_event_count: int
    used_event_count: int
    excluded_event_count: int
    excluded_by_reason: dict[str, int]
    analysis_case_count: int
    case_origin_counts: dict[str, int]
    confidence_counts: dict[str, int]
    raw_active_seconds: float
    active_union_seconds: float
    case_elapsed_seconds: float
    waiting_seconds: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PreparedAnalysis:
    """Validated events and sessionized cases for every mining calculation."""

    events: tuple[StandardEvent, ...]
    cases: tuple[AnalysisCase, ...]
    exclusions: tuple["AnalysisExclusion", ...]
    receipt: AnalysisReceipt
    config: MiningConfig


@dataclass(frozen=True)
class AnalysisExclusion:
    """A local repairable reason why an event is absent from analysis."""

    event_id: str
    reason: str
    evidence: str
    remediation: str


def prepare_analysis(
    events: Iterable[StandardEvent], config: MiningConfig | None = None
) -> PreparedAnalysis:
    """Return a deterministic, fail-closed local analysis dataset.

    Ambiguous unassigned/inferred input is deliberately treated as singleton
    cases.  This may produce fewer edges than older versions, but never turns
    a shared domain or activity label into an invented business flow.
    """

    active_config = config or MiningConfig()
    source_events = tuple(events)
    excluded: Counter[str] = Counter()
    exclusion_details: list[AnalysisExclusion] = []
    canonical_events = _deduplicate(source_events, excluded, exclusion_details)
    eligible_by_base_case: dict[str, list[tuple[StandardEvent, CaseCorrelation]]] = defaultdict(list)

    for event in canonical_events:
        canonical_event, reason = _canonical_event(event)
        if reason:
            excluded[reason] += 1
            exclusion_details.append(_exclusion(event, reason))
            continue
        if canonical_event.idle_flag:
            excluded["idle_event"] += 1
            exclusion_details.append(_exclusion(canonical_event, "idle_event"))
            continue
        correlation = correlation_for(canonical_event)
        base_key = _base_case_key(canonical_event, correlation)
        eligible_by_base_case[base_key].append((canonical_event, correlation))

    cases: list[AnalysisCase] = []
    gap = timedelta(minutes=active_config.session_gap_minutes)
    for base_key in sorted(eligible_by_base_case):
        ordered = sorted(eligible_by_base_case[base_key], key=lambda item: event_sort_key(item[0]))
        for session_index, session in enumerate(_split_sessions(ordered, gap), start=1):
            session_events = tuple(item[0] for item in session)
            if _has_overlap(session_events):
                excluded["overlapping_or_parallel_session"] += len(session_events)
                exclusion_details.extend(
                    _exclusion(event, "overlapping_or_parallel_session") for event in session_events
                )
                continue
            correlation = _combined_correlation(item[1] for item in session)
            cases.append(
                AnalysisCase(
                    key=f"{base_key}:analysis-session-{session_index}",
                    events=session_events,
                    correlation=correlation,
                )
            )

    ordered_cases = tuple(sorted(cases, key=lambda item: item.key))
    used_events = tuple(event for case in ordered_cases for event in case.events)
    if len(used_events) + sum(excluded.values()) != len(source_events):
        raise RuntimeError("Analysis receipt invariant failed: used + excluded must equal input.")

    receipt = AnalysisReceipt(
        algorithm_version=ANALYSIS_ALGORITHM_VERSION,
        session_gap_minutes=active_config.session_gap_minutes,
        scope_fingerprint=_scope_fingerprint(source_events),
        filter_fingerprint=_filter_fingerprint(active_config),
        input_event_count=len(source_events),
        used_event_count=len(used_events),
        excluded_event_count=sum(excluded.values()),
        excluded_by_reason=dict(sorted(excluded.items())),
        analysis_case_count=len(ordered_cases),
        case_origin_counts=_count_case_attribute(ordered_cases, "origin", _CASE_ORIGINS),
        confidence_counts=_count_case_attribute(ordered_cases, "confidence", _CONFIDENCE_LEVELS),
        raw_active_seconds=_rounded(sum(float(event.duration_seconds) for event in used_events)),
        active_union_seconds=_rounded(sum(_union_seconds(case.events) for case in ordered_cases)),
        case_elapsed_seconds=_rounded(sum(_elapsed_seconds(case.events) for case in ordered_cases)),
        waiting_seconds=_rounded(sum(_waiting_seconds(case.events) for case in ordered_cases)),
    )
    return PreparedAnalysis(
        events=used_events,
        cases=ordered_cases,
        exclusions=tuple(sorted(exclusion_details, key=lambda item: (item.event_id, item.reason))),
        receipt=receipt,
        config=active_config,
    )


def sessionize_events(
    events: Iterable[StandardEvent], gap_minutes: int = DEFAULT_SESSION_GAP_MINUTES
) -> dict[str, list[StandardEvent]]:
    """Split valid, non-idle events by case and an actual UTC session gap.

    This public helper retains overlapping sessions so callers can inspect the
    raw grouping.  ``prepare_analysis`` additionally excludes those sessions
    from sequential DFG and variant calculations rather than inventing order.
    """

    config = MiningConfig(session_gap_minutes=gap_minutes)
    grouped: dict[str, list[StandardEvent]] = defaultdict(list)
    for event in events:
        if _event_exclusion_reason(event) or event.idle_flag:
            continue
        correlation = correlation_for(event)
        grouped[_base_case_key(event, correlation)].append(event)
    sessions: dict[str, list[StandardEvent]] = {}
    gap = timedelta(minutes=config.session_gap_minutes)
    for base_key in sorted(grouped):
        ordered = sorted(grouped[base_key], key=event_sort_key)
        for index, session in enumerate(_split_sessions([(event, correlation_for(event)) for event in ordered], gap), start=1):
            sessions[f"{base_key}:analysis-session-{index}"] = [item[0] for item in session]
    return sessions


def correlation_for(event: StandardEvent) -> CaseCorrelation:
    """Read structured provenance, falling back safely for legacy records."""

    metadata = _metadata_object(event.metadata_json)
    raw = metadata.get("opsmineflow_case_correlation") if metadata else None
    if isinstance(raw, dict):
        origin = str(raw.get("origin") or "").strip().lower()
        strategy = str(raw.get("strategy") or "").strip()
        confidence = str(raw.get("confidence") or "").strip().lower()
        evidence = str(raw.get("evidence") or "").strip()
        if origin in _CASE_ORIGINS and confidence in _CONFIDENCE_LEVELS and strategy and evidence:
            return CaseCorrelation(origin=origin, strategy=strategy, confidence=confidence, evidence=evidence)
    if event.case_id.startswith(("CASE-INFERRED-", "CASE-DOMAIN-", "CASE-UNASSIGNED-")):
        return CaseCorrelation(
            origin="inferred",
            strategy="legacy_unstructured_fallback",
            confidence="low",
            evidence="Legacy imported event has no structured case-correlation provenance.",
        )
    return CaseCorrelation(
        origin="observed",
        strategy="legacy_case_id",
        confidence="medium",
        evidence="Legacy imported event supplied a case identifier but not its provenance.",
    )


def event_sort_key(event: StandardEvent) -> tuple[datetime, datetime, str, str, str]:
    """A total, UTC-based order independent of source input sequence."""

    maximum = datetime.max.replace(tzinfo=timezone.utc)
    try:
        start = parse_utc(event.timestamp_start)
    except ValueError:
        start = maximum
    try:
        end = parse_utc(event.timestamp_end)
    except ValueError:
        end = maximum
    return (start, end, event.source, event.source_event_id, event.event_id)


def parse_utc(value: str) -> datetime:
    """Parse an explicit-offset timestamp into the internal UTC instant."""

    if not value or not isinstance(value, str):
        raise ValueError("timestamp is required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _deduplicate(
    events: tuple[StandardEvent, ...],
    excluded: Counter[str],
    exclusion_details: list[AnalysisExclusion],
) -> list[StandardEvent]:
    by_identity: dict[tuple[str, str], list[StandardEvent]] = defaultdict(list)
    for event in events:
        by_identity[(event.source, event.source_event_id)].append(event)
    kept: list[StandardEvent] = []
    for identity in sorted(by_identity):
        matches = sorted(by_identity[identity], key=event_sort_key)
        if len(matches) == 1:
            kept.append(matches[0])
            continue
        fingerprints = {_event_fingerprint(event) for event in matches}
        if len(fingerprints) == 1:
            kept.append(matches[0])
            excluded["duplicate_event"] += len(matches) - 1
            exclusion_details.extend(_exclusion(event, "duplicate_event") for event in matches[1:])
        else:
            excluded["conflicting_source_event_id"] += len(matches)
            exclusion_details.extend(_exclusion(event, "conflicting_source_event_id") for event in matches)
    return kept


def _event_fingerprint(event: StandardEvent) -> str:
    # Event IDs and import timestamps are local bookkeeping, not source-event
    # content. They must not turn a re-imported identical source record into a
    # conflict.
    # ``to_dict()`` uses dataclasses.asdict(), which recursively deep-copies
    # every scalar field. On a large local dataset that copy dominates summary
    # preparation even though the serialized fingerprint payload is identical.
    # Project only declared StandardEvent fields so a future transient/cache
    # attribute cannot change the receipt, then take a shallow copy. Raw event
    # data stays internal to analysis and is never a response or export DTO.
    payload_object = {field_name: getattr(event, field_name) for field_name in _EVENT_FINGERPRINT_FIELDS}
    payload = json.dumps(payload_object, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _scope_fingerprint(events: tuple[StandardEvent, ...]) -> str:
    """Hash the full local analysis input without exposing it in an export."""

    payload = json.dumps(
        sorted(_event_fingerprint(event) for event in events),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _filter_fingerprint(config: MiningConfig) -> str:
    payload = json.dumps(
        {
            "session_gap_minutes": config.session_gap_minutes,
            "filter_context": [
                {"name": key, "values": sorted(values)}
                for key, values in sorted(config.filter_context)
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _exclusion(event: StandardEvent, reason: str) -> AnalysisExclusion:
    guidance = {
        "duplicate_event": (
            "A matching source event was already retained.",
            "Re-import only one verified source row, or retain the duplicate as a separate source event with its own ID.",
        ),
        "conflicting_source_event_id": (
            "The same source event ID has conflicting contents.",
            "Correct the source event ID or resolve the conflicting row before analysis.",
        ),
        "invalid_timestamp": (
            "The timestamp is missing, invalid, or lacks an explicit UTC offset.",
            "Correct the source timestamp and import again with an explicit offset or selected mapping timezone.",
        ),
        "invalid_duration": (
            "The duration is not a finite non-negative number.",
            "Correct the duration in the source data, then import again.",
        ),
        "negative_interval": (
            "The interval ends before it starts.",
            "Correct the source timestamps or exclude this interval as noise.",
        ),
        "zero_duration": (
            "The interval has no elapsed time.",
            "Correct the source interval or exclude this zero-length event.",
        ),
        "duration_interval_mismatch": (
            "The source duration differs from the explicit timestamp interval.",
            "Correct the source duration or timestamps before relying on a process flow.",
        ),
        "idle_event": (
            "The event is marked as idle and is not business-flow evidence.",
            "Keep it excluded as a break, or correct the idle marker in the source data.",
        ),
        "overlapping_or_parallel_session": (
            "This session contains overlapping or parallel intervals, so a sequential order would be invented.",
            "Split, correct, or exclude overlapping intervals before interpreting a sequential flow.",
        ),
    }
    evidence, remediation = guidance[reason]
    return AnalysisExclusion(
        event_id=event.event_id,
        reason=reason,
        evidence=evidence,
        remediation=remediation,
    )


def _event_exclusion_reason(event: StandardEvent) -> str:
    return _canonical_event(event)[1]


def _canonical_event(event: StandardEvent) -> tuple[StandardEvent, str]:
    try:
        start = parse_utc(event.timestamp_start)
        end = parse_utc(event.timestamp_end)
    except ValueError:
        return event, "invalid_timestamp"
    try:
        duration = float(event.duration_seconds)
    except (TypeError, ValueError):
        return event, "invalid_duration"
    if not math.isfinite(duration) or duration < 0:
        return event, "invalid_duration"
    if end < start:
        return event, "negative_interval"
    interval_seconds = (end - start).total_seconds()
    if interval_seconds == 0:
        return event, "zero_duration"
    # Source duration and the explicit interval must agree closely enough to
    # describe one observation. One second tolerates agent clock granularity;
    # anything larger is reviewable data-quality evidence, not analysis input.
    if abs(duration - interval_seconds) > 1.0:
        return event, "duration_interval_mismatch"
    return replace(event, duration_seconds=interval_seconds), ""


def _base_case_key(event: StandardEvent, correlation: CaseCorrelation) -> str:
    if correlation.origin in {"observed", "manual"} and event.case_id.strip():
        return f"case:{event.case_id}"
    # Never turn an unassigned/inferred label, domain, or application into a
    # process instance.  A singleton is conservative and reviewable.
    return f"unassigned:{event.event_id}"


def _split_sessions(
    ordered: list[tuple[StandardEvent, CaseCorrelation]], gap: timedelta
) -> list[list[tuple[StandardEvent, CaseCorrelation]]]:
    if not ordered:
        return []
    sessions: list[list[tuple[StandardEvent, CaseCorrelation]]] = [[ordered[0]]]
    latest_end = parse_utc(ordered[0][0].timestamp_end)
    for item in ordered[1:]:
        start = parse_utc(item[0].timestamp_start)
        end = parse_utc(item[0].timestamp_end)
        if start - latest_end > gap:
            sessions.append([item])
            latest_end = end
            continue
        sessions[-1].append(item)
        latest_end = max(latest_end, end)
    return sessions


def _has_overlap(events: tuple[StandardEvent, ...]) -> bool:
    latest_end: datetime | None = None
    for event in events:
        start = parse_utc(event.timestamp_start)
        end = parse_utc(event.timestamp_end)
        if latest_end is not None and start < latest_end:
            return True
        latest_end = max(latest_end, end) if latest_end is not None else end
    return False


def _combined_correlation(values: Iterable[CaseCorrelation]) -> CaseCorrelation:
    items = tuple(values)
    origins = {item.origin for item in items}
    confidences = {item.confidence for item in items}
    if len(origins) == 1 and len(confidences) == 1:
        return items[0]
    rank = {"low": 0, "medium": 1, "high": 2}
    return CaseCorrelation(
        origin="inferred" if "inferred" in origins or "unassigned" in origins else "observed",
        strategy="mixed_case_provenance",
        confidence=min(confidences, key=lambda item: rank[item]),
        evidence="Analysis session contains events with different case-correlation provenance.",
    )


def _count_case_attribute(
    cases: tuple[AnalysisCase, ...], attribute: str, allowed: set[str]
) -> dict[str, int]:
    counts = Counter(getattr(case.correlation, attribute) for case in cases)
    return {value: int(counts[value]) for value in sorted(allowed) if counts[value]}


def _metadata_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value) if value else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _union_seconds(events: tuple[StandardEvent, ...]) -> float:
    if not events:
        return 0.0
    intervals = sorted((parse_utc(event.timestamp_start), parse_utc(event.timestamp_end)) for event in events)
    total = timedelta()
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start > current_end:
            total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    total += current_end - current_start
    return total.total_seconds()


def _elapsed_seconds(events: tuple[StandardEvent, ...]) -> float:
    if not events:
        return 0.0
    return (parse_utc(events[-1].timestamp_end) - parse_utc(events[0].timestamp_start)).total_seconds()


def _waiting_seconds(events: tuple[StandardEvent, ...]) -> float:
    return sum(
        max((parse_utc(right.timestamp_start) - parse_utc(left.timestamp_end)).total_seconds(), 0.0)
        for left, right in zip(events, events[1:])
    )


def _rounded(value: float) -> float:
    return round(float(value), 6) if math.isfinite(value) else 0.0
