from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


AssetType = Literal["equity", "etf", "option", "crypto", "cash", "unknown"]
SignalAction = Literal["paper_buy", "paper_sell", "watch", "avoid", "review_required"]
StrategyStatus = Literal["candidate", "active", "paused", "retired"]
RiskGateSeverity = Literal["pass", "warn", "block"]


@dataclass(slots=True)
class Holding:
    symbol: str
    name: str = ""
    asset_type: AssetType = "equity"
    quantity: float = 0.0
    avg_cost: float | None = None
    market_value: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized_symbol(self) -> str:
        return self.symbol.strip().upper()


@dataclass(slots=True)
class Quote:
    symbol: str
    price: float
    timestamp: datetime
    change_percent: float | None = None
    previous_close: float | None = None
    volume: int | None = None


@dataclass(slots=True)
class NewsItem:
    title: str
    url: str
    source: str
    published_at: datetime | None = None
    symbols: list[str] = field(default_factory=list)
    summary: str = ""
    kind: str = "news"
    raw: dict[str, Any] = field(default_factory=dict)

    def dedupe_key(self) -> str:
        if self.url:
            return self.url.strip().lower()
        return f"{self.source}:{self.title}".strip().lower()


@dataclass(slots=True)
class NewsEvidence:
    publisher: str
    via_source: str
    source_url: str
    canonical_url: str
    published_at: datetime | None
    symbols: list[str]
    summary: str
    key_points: list[str]
    relevance_score: float
    confidence: float
    risk_flags: list[str] = field(default_factory=list)
    title: str = ""
    relation: str = "research"
    category: str = "research"


@dataclass(slots=True)
class WebEvidence:
    title: str
    url: str
    source: str
    query: str
    symbols: list[str] = field(default_factory=list)
    summary: str = ""
    published_at: datetime | None = None
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = 0.35
    relevance_score: float = 0.0
    provider: str = "web"
    risk_flags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def dedupe_key(self) -> str:
        if self.url:
            return self.url.strip().lower()
        return f"{self.provider}:{self.source}:{self.title}".strip().lower()

    def to_news_item(self) -> "NewsItem":
        return NewsItem(
            title=self.title,
            url=self.url,
            source=self.source or self.provider,
            published_at=self.published_at,
            symbols=self.symbols,
            summary=self.summary,
            kind="web_evidence",
            raw={
                "query": self.query,
                "provider": self.provider,
                "confidence": self.confidence,
                "relevance_score": self.relevance_score,
                "risk_flags": self.risk_flags,
                **self.raw,
            },
        )


@dataclass(slots=True)
class AnalyzedNewsItem:
    symbols: list[str]
    source: str
    title: str
    url: str
    published_at: datetime | None
    chinese_summary: str
    portfolio_impact: str
    strategy_relevance: str
    confidence: float
    risk_flags: list[str]
    why_it_matters: str
    relation: str = "research"
    relevance_score: float = 0.0
    category: str = "research"
    source_label: str = ""
    key_points: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FeatureBundle:
    symbol: str
    quote: dict[str, Any]
    exposure: dict[str, Any]
    news: dict[str, Any]
    sentiment: dict[str, Any]
    catalysts: list[str]
    risks: list[str]
    chain_exposure: list[str]
    strategy: dict[str, Any]
    paper: dict[str, Any]
    backtest: dict[str, Any]
    behavior: dict[str, Any] = field(default_factory=dict)
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_features(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quote_price": self.quote.get("price"),
            "quote_change_percent": self.quote.get("change_percent"),
            "quote_previous_close": self.quote.get("previous_close"),
            "quote_volume": self.quote.get("volume"),
            "daily_behavior": self.behavior,
            "daily_behavior_score": self.behavior.get("score", 0.0),
            "intraday_return_pct": self.behavior.get("intraday_return_pct", 0.0),
            "absolute_intraday_return_pct": self.behavior.get("abs_intraday_return_pct", 0.0),
            "gap_from_previous_close_pct": self.behavior.get("gap_from_previous_close_pct", 0.0),
            "return_1d": self.behavior.get("return_1d", 0.0),
            "return_3d": self.behavior.get("return_3d", 0.0),
            "return_5d": self.behavior.get("return_5d", 0.0),
            "return_20d": self.behavior.get("return_20d", 0.0),
            "relative_volume": self.behavior.get("relative_volume", 0.0),
            "open_gap_pct": self.behavior.get("open_gap_pct", 0.0),
            "close_location_value": self.behavior.get("close_location_value", 0.5),
            "vwap_distance_pct": self.behavior.get("vwap_distance_pct", 0.0),
            "new_high_breakout": self.behavior.get("new_high_breakout", False),
            "behavior_flags": self.behavior.get("flags", []),
            "exposure": self.exposure,
            "portfolio_quantity": self.exposure.get("quantity", 0.0),
            "portfolio_market_value": self.exposure.get("market_value", 0.0),
            "portfolio_asset_types": self.exposure.get("asset_types", []),
            "news_count": self.news.get("count", 0),
            "news_count_24h": self.news.get("count_24h", 0),
            "news_count_5d": self.news.get("count_5d", 0),
            "high_impact_news_count": self.news.get("high_impact_count", 0),
            "positive_keyword_score": self.sentiment.get("positive_score", 0),
            "negative_keyword_score": self.sentiment.get("negative_score", 0),
            "sentiment_score": self.sentiment.get("score", 0),
            "chain_hits": self.chain_exposure,
            "chain_hit_count": len(self.chain_exposure),
            "catalyst_density": self.news.get("catalyst_density", 0.0),
            "risk_density": self.news.get("risk_density", 0.0),
            "relevance_score": self.news.get("relevance_score", 0.0),
            "catalysts": self.catalysts,
            "risk_flags": self.risks,
            "exposure": self.exposure,
            "strategy": self.strategy,
            "paper": self.paper,
            "backtest": self.backtest,
            "computed_at": self.computed_at.isoformat(),
        }


@dataclass(slots=True)
class PatchProposal:
    touched_files: list[str]
    diff: str
    risk_level: Literal["low", "medium", "high"]
    rollback: str
    tests: list[str]
    restart_required: bool = False
    summary: str = ""


@dataclass(slots=True)
class OptionContract:
    underlying: str
    symbol: str
    expiration: datetime
    strike: float
    option_type: Literal["call", "put"]
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    mark: float | None = None
    delta: float | None = None
    implied_volatility: float | None = None
    open_interest: int | None = None
    volume: int | None = None

    @property
    def mid(self) -> float | None:
        if self.mark is not None:
            return self.mark
        if self.bid is not None and self.ask is not None and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        if self.last is not None:
            return self.last
        return self.ask or self.bid

    @property
    def premium(self) -> float | None:
        mid = self.mid
        return None if mid is None else mid * 100.0

    @property
    def spread_percent(self) -> float | None:
        if self.bid is None or self.ask is None or self.ask <= 0:
            return None
        mid = (self.bid + self.ask) / 2.0
        if mid <= 0:
            return None
        return ((self.ask - self.bid) / mid) * 100.0


@dataclass(slots=True)
class StrategyScore:
    symbol: str
    strategy: str
    score: float
    bull_case: str
    bear_case: str
    catalysts: list[str]
    valuation_gap: float
    option_quality: float
    risk_flags: list[str]
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StrategySignal:
    signal_id: str
    symbol: str
    strategy_name: str
    strategy_version: str
    action: SignalAction
    score: float
    confidence: float
    reason: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RiskGateVerdict:
    signal_id: str
    allowed: bool
    severity: RiskGateSeverity
    reasons: list[str]
    size_multiplier: float
    max_notional: float
    blocked_checks: list[str] = field(default_factory=list)
    warning_checks: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PaperOrderProposal:
    proposal_id: str
    signal_id: str
    symbol: str
    asset_type: AssetType
    side: Literal["buy", "sell"]
    quantity: float
    price: float
    gross_value: float
    strategy_name: str
    strategy_version: str
    reason: str
    risk_gate: RiskGateVerdict
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StrategyInfo:
    name: str
    version: str
    status: StrategyStatus
    description: str = ""
    path: str = ""
    data_sources: list[str] = field(default_factory=list)
    metric_ops: list[str] = field(default_factory=list)
    calculation: dict[str, Any] = field(default_factory=dict)
    last_backtest: dict[str, Any] = field(default_factory=dict)
    recent_lesson: str = ""


@dataclass(slots=True)
class OptionCandidate:
    contract: OptionContract
    score: float
    reason: str
    risk_flags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MarketEvent:
    symbol: str
    event_type: str
    severity: Literal["low", "medium", "high"]
    message: str
    quote: Quote | None = None
    news: NewsItem | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
