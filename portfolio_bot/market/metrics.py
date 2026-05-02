from __future__ import annotations

import asyncio
import re
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Literal

from ..models import FeatureBundle, Holding, NewsItem, Quote
from ..runtime import RuntimeStore
from .exposures import leveraged_exposure


MetricBackend = Literal["sync", "thread", "async", "process"]


POSITIVE_FEATURE_KEYWORDS = {
    "design win": 10,
    "customer qualification": 10,
    "qualification": 7,
    "backlog": 8,
    "order": 5,
    "booking": 7,
    "raises guidance": 12,
    "revenue growth": 8,
    "margin expansion": 9,
    "silicon photonics": 10,
    "advanced packaging": 8,
    "hbm": 7,
    "ai compute": 8,
    "ai capex": 8,
    "compute capex": 8,
    "gpu cluster": 8,
    "asic": 6,
    "data center buildout": 7,
    "chips act": 6,
    "profitability": 8,
    "partnership": 5,
}

NEGATIVE_FEATURE_KEYWORDS = {
    "dilution": 12,
    "public offering": 8,
    "stock offering": 8,
    "going concern": 14,
    "cuts guidance": 12,
    "misses guidance": 10,
    "customer delay": 9,
    "inventory correction": 7,
    "capex digestion": 8,
    "capex pause": 9,
    "export controls": 9,
    "export restriction": 9,
    "power bottleneck": 7,
    "data center delay": 8,
    "sec investigation": 14,
    "bankruptcy": 20,
}

CHAIN_FEATURE_KEYWORDS = {
    "eda_ip": ["eda", "ip", "synopsys", "cadence"],
    "equipment": ["lithography", "etch", "deposition", "metrology", "test equipment"],
    "materials": ["wafer", "substrate", "silicon carbide", "gallium arsenide"],
    "foundry_idm": ["foundry", "idm", "fab", "node"],
    "packaging_test": ["advanced packaging", "osat", "test", "burn-in"],
    "silicon_photonics": ["silicon photonics", "optical engine", "co-packaged optics"],
    "power": ["sic", "gan", "power semiconductor"],
    "ai_infra": ["hbm", "gpu", "ai server", "ethernet", "infiniband", "ai compute", "gpu cluster", "asic"],
    "data_center_power": ["data center", "datacenter", "power bottleneck", "power constraint", "grid interconnect"],
}

HIGH_IMPACT_KEYWORDS = {
    "raises guidance",
    "cuts guidance",
    "major contract",
    "design win",
    "customer qualification",
    "public offering",
    "stock offering",
    "sec investigation",
    "bankruptcy",
    "going concern",
    "earnings beat",
    "earnings miss",
}

COMMON_SYMBOL_ALIASES = {
    "GOOGL": ["GOOGL", "GOOG", "GOOGLE", "ALPHABET"],
    "HIMS": ["HIMS", "HIMS & HERS", "HIMS AND HERS", "HIMS HERS"],
    "LWLG": ["LWLG", "LIGHTWAVE LOGIC", "LIGHTWAVE"],
    "LITE": ["LITE", "LUMENTUM"],
    "AEHR": ["AEHR", "AEHR TEST"],
    "COHU": ["COHU", "COHU INC"],
    "POET": ["POET"],
    "LITX": ["LITX"],
    "SNXX": ["SNXX", "SNDK", "SANDISK"],
    "SNDK": ["SNDK", "SANDISK"],
    "INTC": ["INTC", "INTEL"],
    "AMD": ["AMD", "ADVANCED MICRO DEVICES"],
    "NVDA": ["NVDA", "NVIDIA"],
    "TSM": ["TSM", "TSMC", "TAIWAN SEMICONDUCTOR"],
    "ASML": ["ASML"],
    "AMAT": ["AMAT", "APPLIED MATERIALS"],
    "LRCX": ["LRCX", "LAM RESEARCH"],
    "KLAC": ["KLAC", "KLA"],
    "TER": ["TER", "TERADYNE"],
    "ON": ["ON", "ON SEMICONDUCTOR", "ON SEMI", "ONSEMI"],
}


@dataclass(slots=True)
class MetricInput:
    symbol: str
    quote: Quote | None
    news: list[NewsItem]
    exposure: dict[str, Any]
    partials: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class MetricOpSpec:
    name: str
    backend: MetricBackend = "sync"
    dependencies: tuple[str, ...] = ()


class MetricOp:
    name: str = ""
    dependencies: tuple[str, ...] = ()

    def run_symbol(self, item: MetricInput) -> dict[str, Any]:
        raise NotImplementedError


class QuoteOp(MetricOp):
    name = "quote"

    def run_symbol(self, item: MetricInput) -> dict[str, Any]:
        quote = item.quote
        return {
            "price": quote.price if quote else None,
            "change_percent": quote.change_percent if quote else None,
            "previous_close": quote.previous_close if quote else None,
            "volume": quote.volume if quote else None,
            "timestamp": quote.timestamp.isoformat() if quote else "",
        }


class ExposureOp(MetricOp):
    name = "exposure"

    def run_symbol(self, item: MetricInput) -> dict[str, Any]:
        return dict(item.exposure or {})


class BehaviorOp(MetricOp):
    name = "behavior"
    dependencies = ("quote", "exposure")

    def run_symbol(self, item: MetricInput) -> dict[str, Any]:
        quote = item.quote
        exposure = dict(item.exposure or {})
        change = float(quote.change_percent) if quote and quote.change_percent is not None else 0.0
        price = float(quote.price) if quote else 0.0
        previous_close = float(quote.previous_close) if quote and quote.previous_close else 0.0
        gap = ((price / previous_close) - 1.0) * 100.0 if price > 0 and previous_close > 0 else 0.0
        volume = int(quote.volume or 0) if quote else 0
        market_value = float(exposure.get("market_value") or 0.0)

        followthrough = bounded(change * 0.75, -8.0, 8.0)
        if change > 12:
            followthrough = max(0.0, 8.0 - (change - 12.0) * 0.8)
        shock = min(20.0, abs(change) * 1.25)
        overextension = -min(12.0, max(0.0, change - 10.0) * 1.4)
        capitulation = min(4.0, max(0.0, -change - 8.0) * 0.5)
        position_pressure = 0.0
        if market_value > 50000 and abs(change) >= 8:
            position_pressure = -min(8.0, (market_value / 50000.0) * 2.0 + abs(change) / 5.0)
        volume_signal = 1.0 if volume > 0 else 0.0
        score = bounded(followthrough + overextension + capitulation + position_pressure + volume_signal, -20.0, 20.0)

        flags: list[str] = []
        if abs(change) >= 8:
            flags.append("large_intraday_move")
        if change >= 12:
            flags.append("overextension_risk")
        if change <= -8:
            flags.append("drawdown_or_capitulation")
        if market_value > 50000:
            flags.append("large_existing_position")
        if volume <= 0:
            flags.append("missing_volume_confirmation")

        return {
            "intraday_return_pct": round(change, 3),
            "abs_intraday_return_pct": round(abs(change), 3),
            "gap_from_previous_close_pct": round(gap, 3),
            "shock_score": round(shock, 3),
            "followthrough_score": round(followthrough, 3),
            "overextension_score": round(overextension, 3),
            "capitulation_reversal_score": round(capitulation, 3),
            "position_pressure_score": round(position_pressure, 3),
            "volume_confirmation_score": round(volume_signal, 3),
            "score": round(score, 3),
            "flags": flags,
        }


class NewsRelevanceOp(MetricOp):
    name = "news"

    def run_symbol(self, item: MetricInput) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        relevance_scores = [news_relevance(news_item, item.symbol) for news_item in item.news]
        return {
            "count": len(item.news),
            "count_24h": sum(1 for news_item in item.news if is_recent(news_item.published_at, now, timedelta(hours=24))),
            "count_5d": sum(1 for news_item in item.news if is_recent(news_item.published_at, now, timedelta(days=5))),
            "high_impact_count": sum(
                1
                for news_item in item.news
                if any(keyword_matches_text(keyword, f"{news_item.title} {news_item.summary}") for keyword in HIGH_IMPACT_KEYWORDS)
            ),
            "relevance_score": round(sum(relevance_scores) / max(1, len(relevance_scores)), 3),
        }


class SentimentOp(MetricOp):
    name = "sentiment"
    dependencies = ("news",)

    def run_symbol(self, item: MetricInput) -> dict[str, Any]:
        text = news_text(item.news)
        positive = sum(weight for keyword, weight in POSITIVE_FEATURE_KEYWORDS.items() if keyword_matches_text(keyword, text))
        negative = sum(weight for keyword, weight in NEGATIVE_FEATURE_KEYWORDS.items() if keyword_matches_text(keyword, text))
        catalysts = [keyword for keyword in POSITIVE_FEATURE_KEYWORDS if keyword_matches_text(keyword, text)][:8]
        risks = [keyword for keyword in NEGATIVE_FEATURE_KEYWORDS if keyword_matches_text(keyword, text)][:8]
        news_count = int((item.partials.get("news") or {}).get("count") or len(item.news))
        return {
            "positive_score": positive,
            "negative_score": negative,
            "score": positive - negative,
            "catalysts": catalysts,
            "risks": risks,
            "catalyst_density": round(positive / max(1, news_count), 3),
            "risk_density": round(negative / max(1, news_count), 3),
        }


class ChainExposureOp(MetricOp):
    name = "chain_exposure"
    dependencies = ("news",)

    def run_symbol(self, item: MetricInput) -> dict[str, Any]:
        text = news_text(item.news)
        hits = [chain for chain, keywords in CHAIN_FEATURE_KEYWORDS.items() if any(keyword_matches_text(keyword, text) for keyword in keywords)]
        return {"items": hits, "count": len(hits)}


BUILTIN_OPS: dict[str, type[MetricOp]] = {
    QuoteOp.name: QuoteOp,
    ExposureOp.name: ExposureOp,
    BehaviorOp.name: BehaviorOp,
    NewsRelevanceOp.name: NewsRelevanceOp,
    SentimentOp.name: SentimentOp,
    ChainExposureOp.name: ChainExposureOp,
}


DEFAULT_OP_SPECS = [
    MetricOpSpec("quote"),
    MetricOpSpec("exposure"),
    MetricOpSpec("behavior"),
    MetricOpSpec("news"),
    MetricOpSpec("sentiment"),
    MetricOpSpec("chain_exposure"),
]


class MetricPipeline:
    def __init__(self, op_specs: list[MetricOpSpec] | None = None, *, backend: MetricBackend = "sync", max_workers: int = 4):
        self.op_specs = op_specs or DEFAULT_OP_SPECS
        self.backend = normalize_backend(backend)
        self.max_workers = max(1, int(max_workers or 1))

    def run(
        self,
        symbols: list[str],
        quotes: dict[str, Quote | None],
        news_by_symbol: dict[str, list[NewsItem]],
        exposure_by_symbol_map: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, dict[str, Any]]]:
        symbols = [symbol.upper() for symbol in symbols if symbol]
        partials: dict[str, dict[str, dict[str, Any]]] = {symbol: {} for symbol in symbols}
        for spec in self.op_specs:
            op = build_metric_op(spec.name)
            inputs = [
                MetricInput(
                    symbol=symbol,
                    quote=quotes.get(symbol) or quotes.get(symbol.upper()),
                    news=news_by_symbol.get(symbol, []),
                    exposure=exposure_by_symbol_map.get(symbol, {}),
                    partials=partials[symbol],
                )
                for symbol in symbols
            ]
            backend = normalize_backend(spec.backend if spec.backend != "sync" else self.backend)
            results = self._run_op(op, inputs, backend)
            for symbol, value in results.items():
                partials[symbol][spec.name] = value
        return partials

    def _run_op(self, op: MetricOp, inputs: list[MetricInput], backend: MetricBackend) -> dict[str, dict[str, Any]]:
        if backend == "sync" or len(inputs) <= 1:
            return {item.symbol: op.run_symbol(item) for item in inputs}
        if backend == "thread":
            return self._run_thread(op, inputs)
        if backend == "async":
            return asyncio.run(self._run_async(op, inputs))
        if backend == "process":
            return self._run_process(op, inputs)
        return {item.symbol: op.run_symbol(item) for item in inputs}

    def _run_thread(self, op: MetricOp, inputs: list[MetricInput]) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(inputs))) as executor:
            futures = {executor.submit(op.run_symbol, item): item.symbol for item in inputs}
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        return results

    async def _run_async(self, op: MetricOp, inputs: list[MetricInput]) -> dict[str, dict[str, Any]]:
        async def run_one(item: MetricInput) -> tuple[str, dict[str, Any]]:
            return item.symbol, await asyncio.to_thread(op.run_symbol, item)

        rows = await asyncio.gather(*(run_one(item) for item in inputs))
        return dict(rows)

    def _run_process(self, op: MetricOp, inputs: list[MetricInput]) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        with ProcessPoolExecutor(max_workers=min(self.max_workers, len(inputs))) as executor:
            futures = {executor.submit(run_metric_op_process, op.name, item): item.symbol for item in inputs}
            for future in as_completed(futures):
                symbol, value = future.result()
                results[symbol] = value
        return results


class MetricService:
    def __init__(
        self,
        runtime: RuntimeStore,
        *,
        backend: MetricBackend = "sync",
        max_workers: int = 4,
        op_specs: list[MetricOpSpec] | None = None,
    ):
        self.runtime = runtime
        self.backend = normalize_backend(backend)
        self.max_workers = max(1, int(max_workers or 1))
        self.op_specs = op_specs or DEFAULT_OP_SPECS

    def compute_many(
        self,
        symbols: list[str],
        quotes: dict[str, Quote | None],
        news: list[NewsItem],
        *,
        holdings: list[Holding] | None = None,
        commit: bool = True,
        backend: MetricBackend | None = None,
    ) -> dict[str, FeatureBundle]:
        normalized = [symbol.upper() for symbol in symbols if symbol]
        by_symbol = news_by_symbol(news, normalized)
        exposure_map = exposure_by_symbol(holdings or [])
        pipeline = MetricPipeline(self.op_specs, backend=backend or self.backend, max_workers=self.max_workers)
        partials = pipeline.run(normalized, quotes, by_symbol, exposure_map)
        bundles = {symbol: bundle_from_partials(symbol, partials[symbol]) for symbol in normalized}
        if commit:
            for symbol, bundle in bundles.items():
                features = bundle.to_features()
                self.runtime.save_feature_snapshot(symbol, features)
                self.runtime.save_metric_snapshot(symbol, "feature_bundle", asdict(bundle), source="metric_service", as_of=bundle.computed_at)
        return bundles

    def compute_symbol(
        self,
        symbol: str,
        quote: Quote | None,
        news: list[NewsItem],
        *,
        exposure: dict[str, Any] | None = None,
        commit: bool = True,
        backend: MetricBackend | None = None,
    ) -> FeatureBundle:
        bundles = self.compute_many([symbol], {symbol.upper(): quote}, news, holdings=[], commit=False, backend=backend)
        bundle = bundles[symbol.upper()]
        if exposure:
            bundle.exposure.update(exposure)
        if commit:
            features = bundle.to_features()
            self.runtime.save_feature_snapshot(symbol, features)
            self.runtime.save_metric_snapshot(symbol, "feature_bundle", asdict(bundle), source="metric_service", as_of=bundle.computed_at)
        return bundle


def run_metric_op_process(op_name: str, item: MetricInput) -> tuple[str, dict[str, Any]]:
    return item.symbol, build_metric_op(op_name).run_symbol(item)


def build_metric_op(name: str) -> MetricOp:
    if name not in BUILTIN_OPS:
        raise ValueError(f"unknown metric op: {name}")
    return BUILTIN_OPS[name]()


def bundle_from_partials(symbol: str, partials: dict[str, dict[str, Any]]) -> FeatureBundle:
    sentiment = partials.get("sentiment", {})
    news = dict(partials.get("news", {}))
    news["catalyst_density"] = sentiment.get("catalyst_density", 0.0)
    news["risk_density"] = sentiment.get("risk_density", 0.0)
    chain = partials.get("chain_exposure", {})
    return FeatureBundle(
        symbol=symbol,
        quote=partials.get("quote", {}),
        exposure=partials.get("exposure", {}),
        news=news,
        sentiment={
            "positive_score": sentiment.get("positive_score", 0),
            "negative_score": sentiment.get("negative_score", 0),
            "score": sentiment.get("score", 0),
        },
        catalysts=list(sentiment.get("catalysts") or []),
        risks=list(sentiment.get("risks") or []),
        chain_exposure=list(chain.get("items") or []),
        strategy={},
        paper={},
        backtest={},
        behavior=dict(partials.get("behavior", {})),
        computed_at=datetime.now(timezone.utc),
    )


def bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def news_by_symbol(news: list[NewsItem], symbols: list[str]) -> dict[str, list[NewsItem]]:
    by_symbol: dict[str, list[NewsItem]] = {symbol.upper(): [] for symbol in symbols}
    known_symbols = set(by_symbol)
    for item in news:
        item_symbols = {value.upper() for value in item.symbols if value}
        candidate_symbols = item_symbols & known_symbols
        if not candidate_symbols:
            text = f"{item.title} {item.summary}"
            candidate_symbols = {symbol for symbol in known_symbols if symbol_matches_text(symbol, text)}
        for symbol in candidate_symbols:
            if news_relevance(item, symbol) >= 0.45:
                by_symbol[symbol].append(item)
    return by_symbol


def news_relevance(item: NewsItem, symbol: str) -> float:
    return _news_relevance_cached(
        symbol.upper(),
        item.title or "",
        item.summary or "",
        tuple(sorted({value.upper() for value in item.symbols if value})),
    )


@lru_cache(maxsize=50000)
def _news_relevance_cached(symbol: str, title: str, summary: str, item_symbols_tuple: tuple[str, ...]) -> float:
    symbol = symbol.upper()
    text = f"{title} {summary}"
    item_symbols = set(item_symbols_tuple)
    score = 0.0
    if symbol in item_symbols:
        score += 0.35
    if symbol_matches_text(symbol, text):
        score += 0.45
    lowered = text.lower()
    if any(keyword_matches_text(keyword, lowered) for keyword in POSITIVE_FEATURE_KEYWORDS):
        score += 0.12
    if any(keyword_matches_text(keyword, lowered) for keyword in NEGATIVE_FEATURE_KEYWORDS):
        score += 0.12
    if any(keyword_matches_text(keyword, lowered) for keywords in CHAIN_FEATURE_KEYWORDS.values() for keyword in keywords):
        score += 0.08
    if generic_market_article(lowered) and score < 0.7:
        score -= 0.18
    return round(max(0.0, min(1.0, score)), 3)


def symbol_aliases(symbol: str) -> list[str]:
    symbol = symbol.upper().strip()
    aliases = [alias.upper().strip() for alias in COMMON_SYMBOL_ALIASES.get(symbol, [symbol]) if alias]
    if symbol and symbol not in aliases:
        aliases.insert(0, symbol)
    return aliases


def symbol_matches_text(symbol: str, text: str) -> bool:
    return any(alias_matches_text(alias, text or "") for alias in symbol_aliases(symbol))


def alias_matches_text(alias: str, text: str) -> bool:
    alias_upper = alias.upper().strip()
    if not alias_upper:
        return False
    if alias_upper == "ON":
        return re.search(r"(?<![A-Za-z0-9])ON(?![A-Za-z0-9])", text) is not None
    text_upper = text.upper()
    if re.fullmatch(r"[A-Z0-9.]+", alias_upper) and len(alias_upper) <= 5:
        return re.search(rf"(?<![A-Z0-9]){re.escape(alias_upper)}(?![A-Z0-9])", text_upper) is not None
    return alias_upper in text_upper


def keyword_matches_text(keyword: str, text: str) -> bool:
    keyword_lower = keyword.lower().strip()
    text_lower = (text or "").lower()
    if not keyword_lower:
        return False
    if re.fullmatch(r"[a-z0-9.+-]+", keyword_lower) and len(keyword_lower) <= 4:
        return re.search(rf"(?<![a-z0-9]){re.escape(keyword_lower)}(?![a-z0-9])", text_lower) is not None
    return keyword_lower in text_lower


def generic_market_article(text: str) -> bool:
    generic_terms = [
        "stocks to buy",
        "market-beating",
        "biggest moves",
        "whale alerts",
        "top initiations",
        "s&p500 movers",
        "today's session",
    ]
    return any(term in text for term in generic_terms)


def exposure_by_symbol(holdings: list[Holding]) -> dict[str, dict[str, Any]]:
    exposure: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        if holding.asset_type == "option":
            symbol = str(holding.metadata.get("underlying", "")).strip().upper()
        else:
            symbol = holding.normalized_symbol()
        if not symbol:
            continue
        row = exposure.setdefault(symbol, {"quantity": 0.0, "market_value": 0.0, "asset_types": []})
        row["quantity"] += float(holding.quantity or 0.0)
        row["market_value"] += float(holding.market_value or 0.0)
        row["asset_types"].append(holding.asset_type)
        leveraged = leveraged_exposure(symbol)
        if leveraged:
            underlying_row = exposure.setdefault(
                leveraged.underlying,
                {"quantity": 0.0, "market_value": 0.0, "asset_types": []},
            )
            underlying_row["market_value"] += float(holding.market_value or 0.0) * leveraged.multiplier
            underlying_row["asset_types"].append(f"{symbol}_{leveraged.direction}_{leveraged.multiplier:g}x")
    return exposure


def is_recent(published_at: datetime | None, now: datetime, window: timedelta) -> bool:
    if published_at is None:
        return False
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return now - published_at <= window


def news_text(news: list[NewsItem]) -> str:
    return " ".join(f"{item.title} {item.summary}" for item in news).lower()


def normalize_backend(value: str) -> MetricBackend:
    normalized = str(value or "sync").strip().lower()
    if normalized in {"sync", "thread", "async", "process"}:
        return normalized  # type: ignore[return-value]
    return "sync"
