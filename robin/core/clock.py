from __future__ import annotations

from datetime import date, datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def partition_date(value: datetime | date | None = None) -> str:
    if value is None:
        value = utc_now()
    if isinstance(value, datetime):
        return as_utc(value).date().isoformat()  # type: ignore[union-attr]
    return value.isoformat()
