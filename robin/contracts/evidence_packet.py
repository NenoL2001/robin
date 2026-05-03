from __future__ import annotations

from pydantic import Field

from robin.contracts.base import ContractModel
from robin.core.types import VerificationStatus


class EvidencePacket(ContractModel):
    canonical_document_id: str
    entity_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    evidence_score: float = 0.0
    source_tier: str = "UNSPECIFIED"
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    summary: str = ""
    citations: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
