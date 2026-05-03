from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from robin.contracts.base import ContractModel
from robin.core.clock import as_utc


class EventRecord(ContractModel):
    canonical_document_id: str
    entity_ids: list[str] = Field(default_factory=list)
    event_type: str
    event_time: datetime | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    extraction_method: str = "rules"
    confidence: float = 0.0

    @field_validator("event_time")
    @classmethod
    def _clock_utc(cls, value: datetime | None) -> datetime | None:
        return as_utc(value)
