from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from robin.contracts.base import ContractModel
from robin.core.clock import as_utc, utc_now


class CanonicalDocument(ContractModel):
    raw_document_id: str
    source_id: str
    canonical_url: str = ""
    title: str = ""
    text: str
    language: str = "en"
    source_hash: str
    event_time: datetime | None = None
    published_time: datetime | None = None
    ingested_time: datetime = Field(default_factory=utc_now)

    @field_validator("event_time", "published_time", "ingested_time")
    @classmethod
    def _clock_utc(cls, value: datetime | None) -> datetime | None:
        return as_utc(value)
