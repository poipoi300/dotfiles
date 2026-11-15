"""Inspect local lifecycle markers without reading provider conversations."""

from __future__ import annotations

import json
import os
from pathlib import Path


def summarize(state: Path) -> dict:
    sessions = []
    for directory in sorted((state / "markers").glob("*")):
        events: dict[str, int] = {}
        first = last = None
        provider = None
        for path in directory.glob("*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                if row.get("schema_version") != 1:
                    continue
                event, at = row["event"], float(row["at"])
                events[event] = events.get(event, 0) + 1
                provider = row["provider"]
                first = min(first, at) if first is not None else at
                last = max(last, at) if last is not None else at
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
        if events:
            sessions.append({"session_key": directory.name, "provider": provider,
                             "first_observed_at": first, "last_observed_at": last, "events": events})
    return {"schema_version": 1, "sessions": sessions,
            "scope": "Local lifecycle markers only; no transcript, delivery, sharing, or UI integration."}


if __name__ == "__main__":
    print(json.dumps(summarize(Path(os.path.abspath(__file__)).parent / "state"), indent=2))
