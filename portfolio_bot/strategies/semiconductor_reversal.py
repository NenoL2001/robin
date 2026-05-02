from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..config import ResearchConfig
from ..market.metrics import exposure_by_symbol
from ..market.metrics import keyword_matches_text
from ..models import Holding, NewsItem, OptionCandidate, OptionContract, Quote, StrategyScore
from .base import StrategySkill
from .factors import FactorSignal, keyword_score, mine_semiconductor_factors, price_momentum_factor, valuation_factor


POSITIVE_KEYWORDS = {
    "design win": 10,
    "customer qualification": 10,
    "qualification": 7,
    "backlog": 8,
    "order": 6,
    "booking": 7,
    "revenue growth": 8,
    "raises guidance": 12,
    "gross margin": 6,
    "margin expansion": 9,
    "ai": 5,
    "hbm": 7,
    "silicon photonics": 10,
    "advanced packaging": 8,
    "wafer": 5,
    "capacity expansion": 6,
    "ai capex": 8,
    "compute capex": 8,
    "data center capex": 7,
    "gpu shortage": 7,
    "asic": 6,
    "chips act": 6,
    "profitability": 8,
    "free cash flow": 7,
    "earnings beat": 12,
    "above guidance": 12,
    "exceeding guidance": 12,
    "guidance": 5,
    "data center": 6,
    "datacenter": 6,
    "high bandwidth flash": 12,
    "hbf": 10,
    "bics8": 8,
    "new business model": 8,
    "nbm": 7,
    "financial guarantees": 7,
    "share repurchase": 5,
}

NEGATIVE_KEYWORDS = {
    "dilution": 12,
    "going concern": 14,
    "misses guidance": 10,
    "cuts guidance": 12,
    "customer delay": 9,
    "inventory correction": 7,
    "capex digestion": 8,
    "capex pause": 9,
    "export controls": 9,
    "export restriction": 9,
    "power constraint": 7,
    "power bottleneck": 7,
    "data center delay": 8,
    "sec investigation": 14,
    "public offering": 8,
    "stock offering": 8,
    "bankruptcy": 20,
    "sell-the-news": 8,
    "profit-taking": 7,
    "parabolic": 6,
    "overextension": 6,
}

CHAIN_KEYWORDS = {
    "EDA/IP": ["eda", "ip", "synopsys", "cadence"],
    "equipment": ["lithography", "etch", "deposition", "metrology", "test equipment"],
    "materials": ["wafer", "substrate", "silicon carbide", "gallium arsenide"],
    "foundry_idm": ["foundry", "idm", "fab", "node"],
    "packaging_test": ["advanced packaging", "osat", "test", "burn-in"],
    "silicon_photonics": ["silicon photonics", "optical engine", "pluggable", "co-packaged optics"],
    "power": ["sic", "gan", "power semiconductor"],
    "ai_infra": ["hbm", "gpu", "ai server", "ethernet", "infiniband", "asic", "ai accelerator"],
    "data_center_power": ["data center", "datacenter", "power bottleneck", "power constraint", "grid interconnect"],
    "ai_storage": ["high bandwidth flash", "hbf", "bics8", "stargate", "enterprise ssd", "qlc"],
}

SERENITY_CHAIN_KEYWORDS = {
    "inventory bottom": 6,
    "channel inventory": 5,
    "book-to-bill": 7,
    "semi capex": 6,
    "memory cycle": 6,
    "storage cycle": 5,
    "ai storage": 7,
    "follow-through": 5,
    "supply chain": 5,
}

LEOPOLD_COMPUTE_KEYWORDS = {
    "ai compute": 8,
    "compute cluster": 8,
    "training cluster": 8,
    "frontier lab": 6,
    "ai lab": 6,
    "gpu cluster": 8,
    "inference demand": 7,
    "scaling law": 5,
    "data center buildout": 7,
    "power demand": 6,
    "electricity demand": 6,
}

MACRO_RISK_KEYWORDS = {
    "export controls": 9,
    "export restriction": 9,
    "china restriction": 8,
    "power bottleneck": 7,
    "grid bottleneck": 7,
    "data center bottleneck": 7,
    "capex digestion": 8,
    "capex pause": 9,
    "cluster delay": 7,
}


class SemiconductorReversalStrategy(StrategySkill):
    name = "semiconductor_reversal"

    def __init__(self, research: ResearchConfig | None = None, holdings: list[Holding] | None = None):
        self.research = research or ResearchConfig()
        self.holdings = holdings or []
        self.portfolio_exposure = exposure_by_symbol(self.holdings)

    def evaluate(
        self,
        symbol: str,
        quote: Quote | None,
        news: list[NewsItem],
        fundamentals: dict[str, Any] | None = None,
        features: dict[str, Any] | None = None,
    ) -> StrategyScore:
        fundamentals = fundamentals or {}
        features = features or {}
        text = " ".join([item.title + " " + item.summary for item in news]).lower()
        if not features.get("exposure") and symbol.upper() in self.portfolio_exposure:
            features = {**features, "exposure": self.portfolio_exposure[symbol.upper()]}
        mined = mine_semiconductor_factors(
            symbol=symbol,
            quote=quote,
            news=news,
            features=features,
            fundamentals=fundamentals,
            positive_keywords=POSITIVE_KEYWORDS,
            negative_keywords=NEGATIVE_KEYWORDS,
            chain_keywords=CHAIN_KEYWORDS,
        )
        macro_signals = self._macro_signals(news, text)
        score = max(0.0, min(100.0, mined.score + sum(signal.contribution for signal in macro_signals)))
        positive = mined.positive_score
        negative = mined.negative_score
        chain_hits = mined.chain_hits
        valuation_gap = mined.valuation_gap
        option_quality = 0.0
        catalysts = self._extract_catalysts(news)
        risk_flags = self._risk_flags(text, quote)
        bull_case = self._bull_case(symbol, catalysts, chain_hits, valuation_gap)
        bear_case = self._bear_case(risk_flags)
        source_handles = self._source_handles(news)
        macro_topics = self._macro_topics(news)
        confidence = min(0.95, mined.confidence + (0.04 if macro_topics else 0.0) + (0.03 if source_handles else 0.0))
        return StrategyScore(
            symbol=symbol.upper(),
            strategy=self.name,
            score=score,
            bull_case=bull_case,
            bear_case=bear_case,
            catalysts=catalysts,
            valuation_gap=valuation_gap,
            option_quality=option_quality,
            risk_flags=risk_flags,
            confidence=confidence,
            metadata={
                "chain_hits": chain_hits,
                "positive_keyword_score": positive,
                "negative_keyword_score": negative,
                "factor_breakdown": [*mined.breakdown(), *(signal.to_dict() for signal in macro_signals)],
                "macro_topics": macro_topics,
                "source_handles": source_handles,
                "features": features,
            },
        )

    def rank_options(
        self,
        symbol: str,
        quote: Quote | None,
        contracts: list[OptionContract],
        news: list[NewsItem],
    ) -> list[OptionCandidate]:
        now = datetime.now(timezone.utc)
        spot = quote.price if quote else None
        catalysts = self._extract_catalysts(news)
        candidates: list[OptionCandidate] = []
        for contract in contracts:
            if contract.option_type != "call":
                continue
            days = (contract.expiration - now).days
            if days < self.research.option_min_days or days > self.research.option_max_days:
                continue
            premium = contract.premium
            if premium is None or premium <= 0 or premium > self.research.option_max_premium:
                continue
            spread = contract.spread_percent
            if spread is not None and spread > self.research.option_max_spread_percent:
                continue
            score = self._option_score(contract, spot, days, len(catalysts))
            risk_flags = []
            if spread is None:
                risk_flags.append("missing_spread")
            elif spread > 20:
                risk_flags.append("wide_spread")
            if contract.open_interest is not None and contract.open_interest < 10:
                risk_flags.append("low_open_interest")
            if contract.implied_volatility is not None and contract.implied_volatility > 1.25:
                risk_flags.append("very_high_iv")
            reason = self._option_reason(contract, days, premium, score, catalysts)
            candidates.append(OptionCandidate(contract=contract, score=score, reason=reason, risk_flags=risk_flags))
        return sorted(candidates, key=lambda item: item.score, reverse=True)

    def _chain_hits(self, text: str) -> list[str]:
        hits: list[str] = []
        for chain, keywords in CHAIN_KEYWORDS.items():
            if any(keyword_matches_text(keyword, text) for keyword in keywords):
                hits.append(chain)
        return hits

    def _valuation_gap(self, fundamentals: dict[str, Any]) -> float:
        return valuation_factor(fundamentals)

    def _price_momentum(self, quote: Quote | None) -> float:
        return price_momentum_factor(quote)

    def _extract_catalysts(self, news: list[NewsItem]) -> list[str]:
        catalysts: list[str] = []
        for item in news:
            text = f"{item.title} {item.summary}".lower()
            for keyword in [*POSITIVE_KEYWORDS, *SERENITY_CHAIN_KEYWORDS, *LEOPOLD_COMPUTE_KEYWORDS]:
                if keyword_matches_text(keyword, text) and keyword not in catalysts:
                    catalysts.append(keyword)
        return catalysts[:8]

    def _risk_flags(self, text: str, quote: Quote | None) -> list[str]:
        flags = []
        for keyword in [*NEGATIVE_KEYWORDS, *MACRO_RISK_KEYWORDS]:
            if keyword_matches_text(keyword, text) and keyword not in flags:
                flags.append(keyword)
        if quote and quote.change_percent is not None and quote.change_percent < -10:
            flags.append("sharp_intraday_drawdown")
        return flags[:8]

    def _macro_signals(self, news: list[NewsItem], text: str) -> list[FactorSignal]:
        serenity_score = keyword_score(SERENITY_CHAIN_KEYWORDS, text)
        compute_score = keyword_score(LEOPOLD_COMPUTE_KEYWORDS, text)
        risk_score = keyword_score(MACRO_RISK_KEYWORDS, text)
        handles = set(self._source_handles(news))
        topics = set(self._macro_topics(news))
        signals: list[FactorSignal] = []
        if serenity_score:
            bonus = min(12.0, serenity_score * 0.75 + (2.0 if "aleabitoreddit" in handles else 0.0))
            signals.append(FactorSignal("serenity_chain_readthrough", float(serenity_score), bonus, "Serenity-style semiconductor chain/cycle keywords"))
        if compute_score or "ai_compute" in topics:
            value = float(compute_score or 1)
            bonus = min(14.0, compute_score * 0.7 + (2.0 if "leopoldasch" in handles else 0.0) + (2.0 if "ai_compute" in topics else 0.0))
            signals.append(FactorSignal("leopold_compute_demand", value, bonus, "AI compute/capex demand read-through from macro evidence"))
        if risk_score:
            penalty = -min(14.0, risk_score * 0.8)
            signals.append(FactorSignal("ai_compute_macro_risk", float(risk_score), penalty, "macro bottleneck or policy risk in AI compute supply chain"))
        return signals

    def _source_handles(self, news: list[NewsItem]) -> list[str]:
        handles = []
        for item in news:
            handle = str(item.raw.get("handle", "")).lstrip("@").lower()
            if handle and handle not in handles:
                handles.append(handle)
        return handles

    def _macro_topics(self, news: list[NewsItem]) -> list[str]:
        topics: list[str] = []
        for item in news:
            for topic in item.raw.get("macro_topics", []) or []:
                topic = str(topic)
                if topic and topic not in topics:
                    topics.append(topic)
        return topics[:8]

    def _bull_case(self, symbol: str, catalysts: list[str], chain_hits: list[str], valuation_gap: float) -> str:
        parts = []
        if catalysts:
            parts.append("catalysts: " + ", ".join(catalysts[:4]))
        if chain_hits:
            parts.append("chain exposure: " + ", ".join(chain_hits[:4]))
        if valuation_gap > 8:
            parts.append("valuation/revision gap appears favorable")
        return f"{symbol.upper()} reversal setup; " + "; ".join(parts) if parts else f"{symbol.upper()} needs more confirmed catalysts"

    def _bear_case(self, risk_flags: list[str]) -> str:
        if not risk_flags:
            return "Main risk is that catalysts fail to convert into revenue or margin improvement."
        return "Key risks: " + ", ".join(risk_flags)

    def _option_score(self, contract: OptionContract, spot: float | None, days: int, catalyst_count: int) -> float:
        score = 40.0
        premium = contract.premium or 0.0
        spread = contract.spread_percent
        if premium <= 500:
            score += 12
        elif premium <= 1000:
            score += 7
        if spread is not None:
            score += max(-15.0, 12.0 - spread / 2.0)
        if contract.open_interest:
            score += min(10.0, contract.open_interest / 50.0)
        if contract.delta is not None:
            score += max(-8.0, 10.0 - abs(contract.delta - 0.45) * 35.0)
        if contract.implied_volatility is not None:
            score += max(-12.0, 10.0 - contract.implied_volatility * 8.0)
        if spot and spot > 0:
            breakeven = contract.strike + premium / 100.0
            upside_needed = (breakeven / spot - 1.0) * 100.0
            score += max(-15.0, 15.0 - upside_needed / 2.0)
        if 240 <= days <= 420:
            score += 6
        score += min(8, catalyst_count * 2)
        return max(0.0, min(100.0, score))

    def _option_reason(self, contract: OptionContract, days: int, premium: float, score: float, catalysts: list[str]) -> str:
        catalyst_text = ", ".join(catalysts[:3]) if catalysts else "no confirmed catalyst yet"
        return (
            f"{contract.symbol} score={score:.1f}; {days} DTE; "
            f"premium about ${premium:.0f}; strike {contract.strike:g}; catalysts: {catalyst_text}"
        )
