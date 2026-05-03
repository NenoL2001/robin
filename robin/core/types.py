from __future__ import annotations

from enum import StrEnum


class SourceTier(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    UNSPECIFIED = "UNSPECIFIED"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    CONFLICTED = "conflicted"
    REJECTED = "rejected"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class AssetType(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    OPTION = "option"
    CRYPTO = "crypto"
    UNKNOWN = "unknown"


class StrategyState(StrEnum):
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"


class DecisionAction(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    WATCH = "watch"
    BLOCK = "block"


class UnspecifiedReason(StrEnum):
    DATA_SOURCE = "UNSPECIFIED_DATA_SOURCE"
    MARKET_SOURCE = "UNSPECIFIED_MARKET_SOURCE"
    BROKER_PERMISSION = "UNSPECIFIED_BROKER_PERMISSION"
    BUDGET = "UNSPECIFIED_BUDGET"
    COMPLIANCE = "UNSPECIFIED_COMPLIANCE"
