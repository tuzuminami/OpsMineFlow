from __future__ import annotations

import json
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from opsmineflow_mining import load_events_from_json
from opsmineflow_mining.models import StandardEvent

ALLOWED_BASE_URLS = {"http://127.0.0.1:5600", "http://localhost:5600"}


def import_activitywatch_local(base_url: str) -> list[StandardEvent]:
    normalized = base_url.rstrip("/")
    if normalized not in ALLOWED_BASE_URLS:
        raise ValueError("ActivityWatch import only allows localhost port 5600.")
    parsed = urlparse(normalized)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("ActivityWatch import target must be localhost.")

    buckets_payload = _get_json(f"{normalized}/api/0/buckets")
    buckets: dict[str, object] = {}
    for bucket_id in buckets_payload:
        events = _get_json(f"{normalized}/api/0/buckets/{bucket_id}/events")
        buckets[bucket_id] = {"type": str(bucket_id), "events": events}

    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "activitywatch_export.json"
        path.write_text(json.dumps({"buckets": buckets}, ensure_ascii=False), encoding="utf-8")
        return load_events_from_json(path)


def _get_json(url: str) -> object:
    with urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))

