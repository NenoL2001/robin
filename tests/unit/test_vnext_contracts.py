from __future__ import annotations

from datetime import datetime, timezone

from robin.contracts.raw_document import RawDocument
from robin.core.types import SourceTier


def test_raw_document_storage_round_trips_schema_version() -> None:
    document = RawDocument(
        source_id="fixture",
        source_tier=SourceTier.P0,
        url="https://example.com/a",
        title="SNDK earnings",
        body="SNDK revenue outperformance.",
        published_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )

    payload = document.to_storage_dict()
    restored = RawDocument.model_validate(payload)

    assert payload["schema_version"] == "1.0.0"
    assert restored.id == document.id
    assert restored.source_hash == document.source_hash
    assert restored.published_time is not None
    assert restored.published_time.tzinfo is not None
