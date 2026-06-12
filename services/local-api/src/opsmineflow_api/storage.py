from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from opsmineflow_mining import load_events_from_csv
from opsmineflow_mining.models import StandardEvent


@dataclass
class EventStore:
    events: list[StandardEvent] = field(default_factory=list)
    manual_labels: dict[str, str] = field(default_factory=dict)

    def replace(self, events: list[StandardEvent]) -> None:
        self.events = events
        self.manual_labels = {}


_STORE: EventStore | None = None


def default_store() -> EventStore:
    global _STORE
    if _STORE is None:
        sample_path = Path(__file__).resolve().parents[4] / "data/sample/sample_events.csv"
        _STORE = EventStore(events=load_events_from_csv(sample_path))
    return _STORE

