from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class StandardEvent:
    event_id: str
    source: str
    source_event_id: str
    case_id: str
    session_id: str
    user_alias: str
    user_hash: str
    device_id: str
    app_name: str
    app_bundle_id: str
    window_title: str
    window_title_masked: str
    url: str
    url_masked: str
    domain: str
    activity_raw: str
    activity_normalized: str
    event_type: str
    timestamp_start: str
    timestamp_end: str
    duration_seconds: float
    idle_flag: bool
    confidential_flag: bool
    metadata_json: str
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

