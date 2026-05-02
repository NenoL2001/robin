from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..models import NewsItem, Quote
from ..market.metrics import keyword_matches_text


@dataclass(slots=True)
class FactorSignal:
    name: str
    value: float
    contribution: float
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FactorMiningResult:
    score: float
    confidence: float
    signals: list[FactorSignal]
    positive_score: int
    negative_score: int
    chain_hits: list[str]
    valuation_gap: float

    def breakdown(self) -> list[dict[str, Any]]:
        return [signal.to_dict() for signal in self.signals]


def mine_semiconductor_factors(
    *,
    symbol: str,
    quote: Quote | None,
    news: list[NewsItem],
    features: dict[str, Any],
    fundamentals: dict[str, Any],
    positive_keywords: dict[str, int],
    negative_keywords: dict[str, int],
    chain_keywords: dict[str, list[str]],
) -> FactorMiningResult:
    text = " ".join([item.title + " " + item.summary for item in news]).lower()
    factor_weights = dict(features.get("factor_weights") or {})
    positive = int(features.get("positive_keyword_score") or keyword_score(positive_keywords, text))
    negative = int(features.get("negative_keyword_score") or keyword_score(negative_keywords, text))
    chain_hits = list(features.get("chain_hits") or chain_matches(chain_keywords, text))
    valuation_gap = valuation_factor(fundamentals)
    momentum = price_momentum_factor(quote)
    high_impact_count = float(features.get("high_impact_news_count") or 0)
    high_impact_bonus = min(10.0, high_impact_count * 3.0)
    exposure = dict(features.get("exposure") or {})
    portfolio_market_value = float(exposure.get("market_value") or features.get("portfolio_market_value") or 0.0)
    portfolio_bonus = 2.0 if portfolio_market_value > 0 else 0.0

    signals = [
        FactorSignal("base_prior", 1.0, 35.0, "starting prior for semiconductor reversal screen"),
        FactorSignal("catalyst_keywords", float(positive), float(positive), "weighted positive catalyst keywords in matched news"),
        FactorSignal("risk_keywords", float(negative), -float(negative), "weighted negative risk keywords in matched news"),
        FactorSignal("valuation_gap", valuation_gap, valuation_gap, "fundamental growth/margin/valuation inputs when available"),
        FactorSignal("price_momentum", quote.change_percent if quote and quote.change_percent is not None else 0.0, momentum, "bounded intraday momentum/reversal input"),
        FactorSignal("chain_relevance", float(len(chain_hits)), len(chain_hits) * 2.5, "semiconductor supply-chain factor matches"),
        FactorSignal("high_impact_news", high_impact_count, high_impact_bonus, "major-news count, capped to limit headline clustering"),
    ]
    signals.extend(behavior_factor_signals(features, factor_weights))
    signals.extend(event_factor_signals(news, text, factor_weights))
    signals.extend(relationship_factor_signals(news, factor_weights))
    if portfolio_bonus:
        signals.append(FactorSignal("portfolio_context", portfolio_market_value, portfolio_bonus, f"{symbol.upper()} is in current portfolio exposure map"))

    raw_score = sum(signal.contribution for signal in signals)
    score = max(0.0, min(100.0, raw_score))
    confidence = max(
        0.15,
        min(
            0.95,
            0.25
            + len(news) * 0.04
            + len(chain_hits) * 0.05
            + min(0.12, float(features.get("news_count_24h") or 0) * 0.02)
            + (0.03 if portfolio_bonus else 0.0)
            + (0.03 if features.get("daily_behavior") else 0.0),
        ),
    )
    return FactorMiningResult(
        score=score,
        confidence=confidence,
        signals=signals,
        positive_score=positive,
        negative_score=negative,
        chain_hits=chain_hits,
        valuation_gap=valuation_gap,
    )


def keyword_score(weights: dict[str, int], text: str) -> int:
    return sum(weight for keyword, weight in weights.items() if keyword_matches_text(keyword, text))


def chain_matches(chain_keywords: dict[str, list[str]], text: str) -> list[str]:
    return [
        chain
        for chain, keywords in chain_keywords.items()
        if any(keyword_matches_text(keyword, text) for keyword in keywords)
    ]


def valuation_factor(fundamentals: dict[str, Any]) -> float:
    revenue_growth = _float(fundamentals.get("revenue_growth"))
    gross_margin_trend = _float(fundamentals.get("gross_margin_trend"))
    ev_sales = _float(fundamentals.get("ev_sales"))
    gap = 0.0
    if revenue_growth is not None and revenue_growth > 0.15:
        gap += min(15.0, revenue_growth * 40.0)
    if gross_margin_trend is not None and gross_margin_trend > 0:
        gap += min(10.0, gross_margin_trend * 50.0)
    if ev_sales is not None and ev_sales < 3.0:
        gap += 8.0
    return gap


def price_momentum_factor(quote: Quote | None) -> float:
    if not quote or quote.change_percent is None:
        return 0.0
    if quote.change_percent > 5:
        return 6.0
    if quote.change_percent < -8:
        return -6.0
    return max(-3.0, min(3.0, quote.change_percent / 2.0))


def event_factor_signals(news: list[NewsItem], text: str, weights: dict[str, Any]) -> list[FactorSignal]:
    event_types = set()
    official_sources = 0
    for item in news:
        for event_type in item.raw.get("event_types", []) or []:
            event_types.add(str(event_type))
        tier = str(item.raw.get("source_tier", "")).lower()
        if tier == "official" or "investor." in (item.url or "").lower() or "sec.gov" in (item.url or "").lower():
            official_sources += 1
    inferred = {
        "earnings_surprise": ("earnings" in text or "eps" in text or "revenue" in text) and any(term in text for term in ("above guidance", "exceed", "surpass", "beat", "outperformance")),
        "guidance_revision": "guidance" in text or "outlook" in text or "guide" in text,
        "datacenter_mix_shift": "data center" in text or "datacenter" in text,
        "contracted_revenue_visibility": any(term in text for term in ("nbm", "new business model", "multiyear", "financial guarantee", "supply agreement", "prepayment")),
        "product_roadmap_acceleration": any(term in text for term in ("sample", "shipping", "ramp", "roadmap", "stargate")),
        "hbf_ai_inference_moat": any(term in text for term in ("hbf", "high bandwidth flash", "bics8", "ai inference")),
        "official_source_strength": official_sources > 0,
        "sell_the_news_volatility": any(term in text for term in ("sell-the-news", "profit-taking", "parabolic", "volatility")),
    }
    signals: list[FactorSignal] = []
    for name, matched in inferred.items():
        if not matched and name not in event_types:
            continue
        weight = float(weights.get(name, default_event_factor_weight(name)) or 0.0)
        value = float(official_sources if name == "official_source_strength" else 1.0)
        evidence = "structured strategy evidence and configured factor spec"
        signals.append(FactorSignal(name, value, weight, evidence))
    return signals


def behavior_factor_signals(features: dict[str, Any], weights: dict[str, Any]) -> list[FactorSignal]:
    behavior = dict(features.get("daily_behavior") or {})
    if not behavior:
        return []
    score = float(behavior.get("score") or 0.0)
    abs_move = float(behavior.get("abs_intraday_return_pct") or 0.0)
    overextension = float(behavior.get("overextension_score") or 0.0)
    position_pressure = float(behavior.get("position_pressure_score") or 0.0)
    volume_confirmation = float(behavior.get("volume_confirmation_score") or 0.0)
    flags = ", ".join(behavior.get("flags") or [])
    signals = [
        FactorSignal(
            "intraday_followthrough",
            score,
            bounded(score * float(weights.get("intraday_followthrough", 0.45) or 0.0), -8.0, 8.0),
            f"deterministic intraday behavior score; flags={flags or 'none'}",
        )
    ]
    if abs_move >= 8.0:
        signals.append(
            FactorSignal(
                "large_move_reversal_risk",
                abs_move,
                bounded(overextension + min(0.0, score * 0.25), -12.0, 2.0),
                "large daily move control from local quote behavior, independent of LLM text",
            )
        )
    if position_pressure < 0:
        signals.append(
            FactorSignal(
                "position_crowding_pressure",
                position_pressure,
                position_pressure,
                "existing position size plus large daily move reduces new paper sizing appetite",
            )
        )
    if volume_confirmation > 0:
        signals.append(
            FactorSignal(
                "volume_confirmation",
                volume_confirmation,
                min(2.0, volume_confirmation * float(weights.get("volume_confirmation", 1.0) or 0.0)),
                "quote contained nonzero volume; stronger volume normalization needs historical bars",
            )
        )
    return signals


def relationship_factor_signals(news: list[NewsItem], weights: dict[str, Any]) -> list[FactorSignal]:
    relations = []
    for item in news:
        relation = item.raw.get("relationship") if isinstance(item.raw, dict) else None
        if isinstance(relation, dict):
            relations.append(relation)
    if not relations:
        return []
    confidence = sum(float(row.get("confidence") or 0.0) for row in relations) / max(1, len(relations))
    contribution = min(8.0, confidence * float(weights.get("underlying_relation_strength", 8.0) or 0.0))
    related = sorted({str(row.get("related_symbol", "")).upper() for row in relations if row.get("related_symbol")})
    return [
        FactorSignal(
            "underlying_relation_strength",
            confidence,
            contribution,
            f"bridged underlying/related-symbol evidence from {', '.join(related[:4]) or 'related symbols'}",
        )
    ]


def default_event_factor_weight(name: str) -> float:
    defaults = {
        "earnings_surprise": 14.0,
        "guidance_revision": 12.0,
        "datacenter_mix_shift": 10.0,
        "contracted_revenue_visibility": 10.0,
        "product_roadmap_acceleration": 8.0,
        "hbf_ai_inference_moat": 10.0,
        "official_source_strength": 4.0,
        "sell_the_news_volatility": -6.0,
        "intraday_followthrough": 0.45,
        "volume_confirmation": 1.0,
        "underlying_relation_strength": 8.0,
    }
    return defaults.get(name, 0.0)


def bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
