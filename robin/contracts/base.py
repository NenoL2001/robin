from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from robin.core.clock import as_utc, utc_now


class ContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_assignment=False)

    schema_version: str = "1.0.0"
    id: str = ""
    lineage: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return as_utc(value) or utc_now()

    def to_storage_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
