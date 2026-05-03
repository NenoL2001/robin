from __future__ import annotations

from typing import Any

from pydantic import Field

from robin.contracts.base import ContractModel
from robin.core.types import VerificationStatus


class ClaimRecord(ContractModel):
    event_id: str
    entity_ids: list[str] = Field(default_factory=list)
    claim_type: str
    statement: str
    value: str = ""
    unit: str = ""
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    supporting_document_ids: list[str] = Field(default_factory=list)
    conflicting_claim_ids: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
