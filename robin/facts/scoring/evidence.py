from __future__ import annotations

from robin.contracts.claim import ClaimRecord
from robin.core.types import SourceTier, VerificationStatus


def evidence_score(source_tier: SourceTier, claims: list[ClaimRecord]) -> tuple[float, list[str]]:
    score = {SourceTier.P0: 0.9, SourceTier.P1: 0.72, SourceTier.P2: 0.48}.get(source_tier, 0.25)
    flags: list[str] = []
    if not claims:
        return 0.0, ["no_claims"]
    verified = sum(1 for claim in claims if claim.verification_status == VerificationStatus.VERIFIED)
    conflicted = sum(1 for claim in claims if claim.verification_status == VerificationStatus.CONFLICTED)
    score += min(0.08, verified * 0.02)
    if conflicted:
        score -= 0.3
        flags.append("conflicting_claims")
    if verified == 0:
        flags.append("unverified_claims")
    return round(max(0.0, min(1.0, score)), 4), flags
