from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from robin.contracts.base import ContractModel
from robin.core.clock import as_utc, utc_now
from robin.core.ids import content_hash, stable_id
from robin.core.types import SourceTier


class RawDocument(ContractModel):
    source_id: str
    source_tier: SourceTier = SourceTier.UNSPECIFIED
    url: str = ""
    title: str = ""
    body: str = ""
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    source_hash: str = ""
    event_time: datetime | None = None
    published_time: datetime | None = None
    ingested_time: datetime = Field(default_factory=utc_now)

    @field_validator("event_time", "published_time", "ingested_time")
    @classmethod
    def _clock_utc(cls, value: datetime | None) -> datetime | None:
        return as_utc(value)

    @model_validator(mode="after")
    def _fill_hashes(self) -> "RawDocument":
        source_hash = self.source_hash or content_hash(f"{self.url}\n{self.title}\n{self.body}")
        object.__setattr__(self, "source_hash", source_hash)
        if not self.id:
            object.__setattr__(self, "id", stable_id("rawdoc", {"source": self.source_id, "url": self.url, "hash": source_hash}))
        return self
