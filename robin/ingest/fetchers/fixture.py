from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from robin.contracts.raw_document import RawDocument
from robin.ingest.fetchers.base import FetchFailure
from robin.ingest.sources.registry import SourceConfig


class FixtureFetcher:
    """Reads deterministic fixture documents from JSON/JSONL files."""

    def fetch(self, source: SourceConfig) -> tuple[list[RawDocument], list[FetchFailure]]:
        if not source.path:
            return [], [FetchFailure(source.source_id, "fixture path missing")]
        path = Path(source.path)
        if not path.exists():
            return [], [FetchFailure(source.source_id, f"fixture path not found: {path}")]
        try:
            rows = read_fixture_rows(path)
            return [raw_document_from_row(source, row) for row in rows], []
        except Exception as exc:
            return [], [FetchFailure(source.source_id, f"fixture read failed: {exc}")]


def read_fixture_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("documents"), list):
        return [row for row in payload["documents"] if isinstance(row, dict)]
    return []


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def raw_document_from_row(source: SourceConfig, row: dict[str, Any]) -> RawDocument:
    return RawDocument(
        id=str(row.get("id", "")),
        source_id=source.source_id,
        source_tier=source.tier,
        url=str(row.get("url", source.url)),
        title=str(row.get("title", "")),
        body=str(row.get("body", row.get("summary", ""))),
        raw_payload=dict(row),
        event_time=parse_time(row.get("event_time")),
        published_time=parse_time(row.get("published_time") or row.get("published_at")),
        ingested_time=parse_time(row.get("ingested_time")) or datetime.now(timezone.utc),
    )
