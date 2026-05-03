from __future__ import annotations

from datetime import datetime, timezone


def age_seconds(timestamp: datetime | None) -> float:
    if timestamp is None:
        return float("inf")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds())
