from __future__ import annotations

from robin.contracts.base import ContractModel
from robin.core.types import AssetType


class EntityMention(ContractModel):
    canonical_document_id: str
    text: str
    start_char: int
    end_char: int
    candidate_type: str = "organization"
    confidence: float = 0.0


class CanonicalEntity(ContractModel):
    mention_id: str
    security_id: str
    symbol: str
    name: str = ""
    asset_type: AssetType = AssetType.UNKNOWN
    exchange: str = ""
    confidence: float = 0.0
