from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import unescape
import re
from typing import Any
from urllib.parse import urlparse

import requests

from ..config import BotConfig
from ..memory import MemoryStore, memory_path
from ..models import NewsItem, WebEvidence
from ..runtime import RuntimeStore, runtime_path
from .exposures import leveraged_exposure
from .metrics import COMMON_SYMBOL_ALIASES, symbol_matches_text
from .news_strategy import build_news_query_plan, enrich_web_evidence
from .web_search import WebSearchService, dedupe_web_evidence


OFFICIAL_SOURCE_HOSTS = {
    "investor.sandisk.com",
    "documents.sandisk.com",
    "sec.gov",
    "www.sec.gov",
}

SNDK_EVIDENCE_URLS = {
    "official_q3": "https://investor.sandisk.com/node/7896/pdf",
    "transcript_q3": "https://www.fool.com/earnings/call-transcripts/2026/04/30/sandisk-sndk-q3-2026-earnings-transcript/",
    "hbf_fact_sheet": "https://documents.sandisk.com/content/dam/asset-library/en_us/assets/public/sandisk/collateral/company/Sandisk-HBF-Fact-Sheet.pdf",
    "hbf_industry": "https://www.tomshardware.com/pc-components/ssds/sk-hynix-and-sandisk-announce-new-high-bandwidth-flash-speedy-hbf-standard-is-targeted-at-inference-ai-servers",
}


@dataclass(slots=True)
class EventObservation:
    symbol: str
    event_type: str
    title: str
    url: str
    source: str
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return payload


@dataclass(slots=True)
class SymbolRelationship:
    source_symbol: str
    related_symbol: str
    relation_type: str
    confidence: float
    evidence_title: str
    evidence_url: str
    query: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return payload


@dataclass(slots=True)
class StrategyScoutResult:
    strategy_name: str
    symbols: list[str]
    queries: list[str]
    evidence: list[WebEvidence]
    events: list[EventObservation]
    gaps: list[dict[str, Any]]
    relationships: list[SymbolRelationship] = field(default_factory=list)
    allow_external: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def news_items(self) -> list[NewsItem]:
        return [item.to_news_item() for item in self.evidence]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        for item in payload["evidence"]:
            if item.get("published_at"):
                item["published_at"] = item["published_at"].isoformat() if hasattr(item["published_at"], "isoformat") else str(item["published_at"])
            if item.get("discovered_at"):
                item["discovered_at"] = item["discovered_at"].isoformat() if hasattr(item["discovered_at"], "isoformat") else str(item["discovered_at"])
        for item in payload["events"]:
            if item.get("created_at"):
                item["created_at"] = item["created_at"].isoformat() if hasattr(item["created_at"], "isoformat") else str(item["created_at"])
        for item in payload["relationships"]:
            if item.get("created_at"):
                item["created_at"] = item["created_at"].isoformat() if hasattr(item["created_at"], "isoformat") else str(item["created_at"])
        return payload

    def related_symbols(self) -> list[str]:
        return sorted({item.related_symbol for item in self.relationships if item.confidence >= 0.55})

    def summary(self) -> str:
        lines = [
            "策略自主深挖",
            f"- strategy={self.strategy_name}; symbols={', '.join(self.symbols) or 'none'}; queries={len(self.queries)}; evidence={len(self.evidence)}; events={len(self.events)}; relations={len(self.relationships)}; gaps={len(self.gaps)}",
        ]
        if self.queries:
            label = "已执行 query" if self.allow_external else "只读报告 query 模板"
            lines.append(f"- {label}: " + " | ".join(self.queries[:8]))
        for event in self.events[:12]:
            lines.append(f"- {event.symbol} {event.event_type}: {event.title} {event.url}")
        for relation in self.relationships[:6]:
            lines.append(
                f"- relation {relation.source_symbol}->{relation.related_symbol} ({relation.relation_type}, confidence={relation.confidence:.2f}): {relation.evidence_title} {relation.evidence_url}"
            )
        for gap in self.gaps[:6]:
            lines.append(f"- research_gap {gap.get('symbol')}: checked={'; '.join(gap.get('queries', [])[:4])}")
        return "\n".join(lines)


class StrategyNewsScout:
    def __init__(
        self,
        config: BotConfig,
        *,
        web_search: WebSearchService | None = None,
        runtime: RuntimeStore | None = None,
        memory: MemoryStore | None = None,
    ):
        self.config = config
        self.runtime = runtime or RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
        self.memory = memory or MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
        self.web_search = web_search or WebSearchService(config, runtime=self.runtime, memory=self.memory)

    def scout(
        self,
        strategy_name: str,
        symbols: list[str],
        *,
        themes: list[str] | None = None,
        commit: bool = True,
        deep: bool = False,
        allow_external: bool = True,
    ) -> StrategyScoutResult:
        normalized = expand_strategy_symbols(symbols)
        if not self.config.strategy_research.enabled:
            return StrategyScoutResult(strategy_name, normalized, [], [], [], [], [])
        queries = build_strategy_queries(
            normalized,
            themes=themes or [],
            max_queries=self.config.strategy_research.max_queries_per_strategy,
            official_sources_first=self.config.strategy_research.official_sources_first,
            deep=deep,
        )
        evidence: list[WebEvidence] = []
        if allow_external:
            for query in queries:
                evidence.extend(enrich_web_evidence(item, normalized, query_context={"strategy": strategy_name, "query": query}) for item in self.web_search.search(query, symbols=normalized, commit=commit))
        if allow_external and self.config.research.web_search_enabled:
            evidence.extend(direct_product_evidence_for(normalized, timeout=self.config.research.web_search_timeout_seconds))
        evidence.extend(curated_evidence_for(normalized))
        evidence = dedupe_web_evidence(evidence)
        relationships = extract_symbol_relationships(evidence, normalized)
        related_symbols = sorted({item.related_symbol for item in relationships if item.confidence >= 0.55})
        if related_symbols:
            for query in build_related_symbol_queries(related_symbols, themes=themes or [], max_queries=max(1, self.config.strategy_research.max_queries_per_strategy - len(queries))):
                if query in queries:
                    continue
                queries.append(query)
                if allow_external:
                    evidence.extend(
                        enrich_web_evidence(item, [*normalized, *related_symbols], query_context={"strategy": strategy_name, "query": query, "related_symbols": related_symbols})
                        for item in self.web_search.search(query, symbols=[*normalized, *related_symbols], commit=commit)
                    )
            evidence = dedupe_web_evidence(evidence)
            relationships = extract_symbol_relationships(evidence, normalized)
        events = extract_event_observations(evidence, normalized)
        gaps = []
        if allow_external and self.config.strategy_research.secondary_search_on_gap:
            covered = {symbol for item in evidence for symbol in item.symbols}
            for symbol in normalized:
                if symbol not in covered and not related_symbol_has_evidence(symbol, covered):
                    gaps.append({"symbol": symbol, "queries": [query for query in queries if symbol in query.upper()] or queries[:3]})
        result = StrategyScoutResult(strategy_name, normalized, queries, evidence, events, gaps, relationships, allow_external=allow_external)
        if commit:
            self._remember(result)
        return result

    def _remember(self, result: StrategyScoutResult) -> None:
        for item in result.evidence:
            payload = {
                "query": item.query,
                "url": item.url,
                "source": item.source,
                "symbols": item.symbols,
                "source_tier": item.raw.get("source_tier", source_tier(item.url)),
                "risk_flags": item.risk_flags,
                "relevance_score": item.relevance_score,
            }
            content = f"{item.source}: {item.title} {item.summary}".strip()
            self.memory.add(
                "strategy_evidence",
                content,
                symbol=",".join(item.symbols[:8]),
                strategy=result.strategy_name,
                importance=0.82 if payload["source_tier"] == "official" else 0.68,
                confidence=item.confidence,
                source="strategy_news_scout",
                metadata=payload,
                evidence_links=[item.url] if item.url else [],
            )
            self.memory.add(
                "web_evidence",
                content,
                symbol=",".join(item.symbols[:8]),
                strategy=result.strategy_name,
                importance=0.75,
                confidence=item.confidence,
                source=item.provider,
                metadata=payload,
                evidence_links=[item.url] if item.url else [],
            )
        for event in result.events:
            self.memory.add(
                "event_observation",
                f"{event.symbol} {event.event_type}: {event.title}",
                symbol=event.symbol,
                strategy=result.strategy_name,
                importance=0.84,
                confidence=event.confidence,
                source="strategy_news_scout",
                metadata=event.to_dict(),
                evidence_links=[event.url] if event.url else [],
            )
        for gap in result.gaps:
            self.memory.add(
                "research_gap",
                f"{gap.get('symbol')}: strategy scout found no confirmed evidence after secondary search",
                symbol=str(gap.get("symbol", "")),
                strategy=result.strategy_name,
                importance=0.55,
                confidence=0.5,
                source="strategy_news_scout",
                metadata=gap,
            )
        for relation in result.relationships:
            self.memory.add(
                "symbol_relationship",
                f"{relation.source_symbol}->{relation.related_symbol} {relation.relation_type}: {relation.evidence_title}",
                symbol=relation.source_symbol,
                strategy=result.strategy_name,
                importance=0.74,
                confidence=relation.confidence,
                source="strategy_news_scout",
                metadata=relation.to_dict(),
                evidence_links=[relation.evidence_url] if relation.evidence_url else [],
            )


def expand_strategy_symbols(symbols: list[str]) -> list[str]:
    expanded: set[str] = set()
    for raw in symbols:
        symbol = str(raw or "").strip().upper()
        if not symbol:
            continue
        expanded.add(symbol)
        exposure = leveraged_exposure(symbol)
        if exposure:
            expanded.add(exposure.underlying)
    return sorted(expanded)


def strategy_aliases(symbol: str) -> list[str]:
    symbol = symbol.upper()
    if symbol in {"SNXX", "SNDK"}:
        return ["SNDK", "SNXX", "Sandisk", "SanDisk", "High Bandwidth Flash", "HBF", "BiCS8", "NBM", "Stargate"]
    return [symbol]


def build_strategy_queries(
    symbols: list[str],
    *,
    themes: list[str],
    max_queries: int,
    official_sources_first: bool,
    deep: bool = False,
) -> list[str]:
    queries: list[str] = []
    if any(symbol in {"SNDK", "SNXX"} for symbol in symbols):
        official = [
            "site:investor.sandisk.com SNDK fiscal third quarter 2026 financial results revenue EPS guidance",
            "site:investor.sandisk.com Sandisk High Bandwidth Flash HBF BiCS8 NBM",
            "site:documents.sandisk.com Sandisk HBF fact sheet High Bandwidth Flash",
        ]
        secondary = [
            "Sandisk SNDK Q3 2026 earnings transcript datacenter NBM guidance HBF",
            "SanDisk High Bandwidth Flash HBF SK hynix AI inference samples 2027",
            "SNDK earnings beat guidance data center revenue 233% NBM contracts",
        ]
        queries.extend([*official, *secondary] if official_sources_first else [*secondary, *official])
    for symbol in symbols:
        if symbol in {"SNXX"}:
            continue
        theme_text = " ".join(themes[:4]) if themes else "earnings guidance catalyst product roadmap"
        queries.extend(
            [
                f"{symbol} investor relations earnings guidance press release",
                f"{symbol} earnings transcript {theme_text}",
                f"{symbol} holdings underlying ETF 2x long fund prospectus",
                f"{symbol} issuer daily leveraged ETF underlying symbol",
            ]
        )
        if deep:
            queries.append(f"{symbol} SEC filing 10-Q risk factors business update {theme_text}")
    plan_queries = [
        item.query
        for item in build_news_query_plan(
            symbols,
            max_queries=max_queries,
            official_sources_first=official_sources_first,
            include_social=deep,
        )
    ]
    return dedupe_strings([*queries, *plan_queries])[: max(1, max_queries)]


def build_related_symbol_queries(symbols: list[str], *, themes: list[str], max_queries: int) -> list[str]:
    queries: list[str] = []
    theme_text = " ".join(themes[:3]) if themes else "earnings guidance daily stock behavior catalyst"
    for symbol in symbols:
        queries.extend(
            [
                f"{symbol} investor relations latest news {theme_text}",
                f"{symbol} stock daily move catalyst volume earnings guidance",
            ]
        )
    return dedupe_strings(queries)[: max(0, max_queries)]


def direct_product_evidence_for(symbols: list[str], *, timeout: int) -> list[WebEvidence]:
    evidence: list[WebEvidence] = []
    for symbol in symbols:
        if not should_direct_lookup_symbol(symbol):
            continue
        evidence.extend(fetch_direct_product_pages(symbol, timeout=timeout))
    return evidence


def should_direct_lookup_symbol(symbol: str) -> bool:
    symbol = symbol.upper()
    return symbol.endswith("X") or symbol in {"USD", "SOXL"}


def fetch_direct_product_pages(symbol: str, *, timeout: int) -> list[WebEvidence]:
    symbol = symbol.upper()
    urls = [
        f"https://www.tradretfs.com/{symbol.lower()}?hsLang=en",
        f"https://stockanalysis.com/etf/{symbol.lower()}/",
    ]
    results: list[WebEvidence] = []
    for url in urls:
        try:
            response = requests.get(url, headers={"User-Agent": "portfolio-bot/0.1 (+paper research)"}, timeout=max(2, int(timeout)))
            if response.status_code >= 400:
                continue
        except requests.RequestException:
            continue
        title, summary = extract_html_title_summary(response.text)
        text = f"{title} {summary}"
        if not symbol_matches_text(symbol, text):
            continue
        results.append(
            WebEvidence(
                title=title or f"{symbol} product page",
                url=url,
                source=host_from_url(url),
                query=f"direct_product_lookup:{symbol}",
                symbols=[symbol],
                summary=summary[:900],
                published_at=None,
                confidence=0.6,
                relevance_score=0.82,
                provider="direct_product_lookup",
                risk_flags=["missing_fresh_timestamp"],
                raw={"source_tier": "issuer_or_profile", "direct_lookup": True},
            )
        )
    return results


def extract_html_title_summary(html_text: str) -> tuple[str, str]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    title = clean_html_text(title_match.group(1)) if title_match else ""
    description = ""
    desc_match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html_text, flags=re.IGNORECASE | re.DOTALL)
    if not desc_match:
        desc_match = re.search(r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']', html_text, flags=re.IGNORECASE | re.DOTALL)
    if desc_match:
        description = clean_html_text(desc_match.group(1))
    visible = clean_html_text(re.sub(r"<(script|style).*?</\1>", " ", html_text, flags=re.IGNORECASE | re.DOTALL))
    return title, " ".join([description, visible[:1600]]).strip()


def clean_html_text(value: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", unescape(value or "")).split())


def curated_evidence_for(symbols: list[str]) -> list[WebEvidence]:
    if not any(symbol in {"SNDK", "SNXX"} for symbol in symbols):
        return []
    published = datetime(2026, 4, 30, tzinfo=timezone.utc)
    return [
        curated_web_evidence(
            "Sandisk Reports Fiscal Third Quarter 2026 Financial Results",
            SNDK_EVIDENCE_URLS["official_q3"],
            "Official release: Q3 revenue $5.95B, up 97% sequentially and above guidance; non-GAAP EPS $23.41; Datacenter revenue up 233%; Q4 revenue guide $7.75B-$8.25B and EPS guide $30-$33; five NBM agreements and buyback authorization.",
            "official_sndk_q3",
            published,
            ["earnings_surprise", "guidance_revision", "datacenter_mix_shift", "contracted_revenue_visibility", "official_source_strength"],
        ),
        curated_web_evidence(
            "Sandisk Q3 2026 Earnings Transcript",
            SNDK_EVIDENCE_URLS["transcript_q3"],
            "Transcript confirms revenue and margin outperformance, data center demand, NBM commitments, variable pricing, financial guarantees, and QLC Stargate ramp preparation.",
            "sndk_q3_transcript",
            published,
            ["earnings_surprise", "guidance_revision", "datacenter_mix_shift", "contracted_revenue_visibility"],
            source_tier="transcript",
        ),
        curated_web_evidence(
            "Sandisk HBF Fact Sheet",
            SNDK_EVIDENCE_URLS["hbf_fact_sheet"],
            "Official HBF fact sheet: first-generation product targets 1.6 TB/s read bandwidth, 512GB per 16-die stack, HBM4-like footprint and AI inference workloads.",
            "official_hbf_fact_sheet",
            datetime(2025, 7, 1, tzinfo=timezone.utc),
            ["hbf_ai_inference_moat", "product_roadmap_acceleration", "official_source_strength"],
        ),
        curated_web_evidence(
            "SK hynix and SanDisk announce High Bandwidth Flash for AI inference servers",
            SNDK_EVIDENCE_URLS["hbf_industry"],
            "Industry report on SK hynix and SanDisk HBF standardization for AI inference servers and the memory tier between HBM DRAM and flash SSDs.",
            "hbf_industry",
            datetime(2026, 2, 26, tzinfo=timezone.utc),
            ["hbf_ai_inference_moat", "product_roadmap_acceleration"],
            source_tier="industry_media",
        ),
    ]


def curated_web_evidence(
    title: str,
    url: str,
    summary: str,
    query: str,
    published_at: datetime,
    event_types: list[str],
    *,
    source_tier: str = "official",
) -> WebEvidence:
    return WebEvidence(
        title=title,
        url=url,
        source=host_from_url(url),
        query=query,
        symbols=["SNDK", "SNXX"],
        summary=summary,
        published_at=published_at,
        confidence=0.82 if source_tier == "official" else 0.68,
        relevance_score=0.95,
        provider="strategy_curated",
        risk_flags=[] if source_tier == "official" else ["non_official_source"],
        raw={"source_tier": source_tier, "event_types": event_types},
    )


def extract_event_observations(evidence: list[WebEvidence], symbols: list[str]) -> list[EventObservation]:
    events: list[EventObservation] = []
    seen: set[tuple[str, str, str]] = set()
    for item in evidence:
        text = f"{item.title} {item.summary}".lower()
        event_types = list(item.raw.get("event_types") or [])
        event_types.extend(infer_event_types(text, item.url))
        for symbol in item.symbols or symbols:
            canonical = "SNDK" if symbol.upper() == "SNXX" and "SNDK" in symbols else symbol.upper()
            for event_type in dedupe_strings(event_types):
                key = (canonical, event_type, item.url)
                if key in seen:
                    continue
                seen.add(key)
                events.append(
                    EventObservation(
                        symbol=canonical,
                        event_type=event_type,
                        title=item.title,
                        url=item.url,
                        source=item.source,
                        confidence=max(0.45, item.confidence),
                        metadata={
                            "source_tier": item.raw.get("source_tier", source_tier(item.url)),
                            "query": item.query,
                            "summary": item.summary,
                        },
                    )
                )
    return events


def extract_symbol_relationships(evidence: list[WebEvidence], source_symbols: list[str]) -> list[SymbolRelationship]:
    source_set = {symbol.upper() for symbol in source_symbols}
    candidates = {symbol for symbol in COMMON_SYMBOL_ALIASES if symbol not in source_set}
    relationships: list[SymbolRelationship] = []
    seen: set[tuple[str, str, str]] = set()
    for item in evidence:
        text = f"{item.title} {item.summary}"
        item_symbol_set = {value.upper() for value in item.symbols}
        if item_symbol_set:
            source_candidates = source_set & item_symbol_set
        else:
            source_candidates = {symbol for symbol in source_set if symbol_matches_text(symbol, text)}
        if not source_candidates:
            continue
        relation_text = text.lower()
        for source_symbol in source_candidates:
            relation_context = relationship_context_for_source(text, source_symbol)
            explicit_related = explicit_related_symbols(text, source_symbol)
            for related_symbol in candidates:
                if related_symbol == source_symbol:
                    continue
                if explicit_related:
                    if related_symbol not in explicit_related:
                        continue
                elif not relation_context or not symbol_matches_text(related_symbol, relation_context):
                    continue
                relation_type = infer_relationship_type(relation_context.lower())
                if relation_type == "weak_mention":
                    continue
                key = (source_symbol, related_symbol, item.url or item.title)
                if key in seen:
                    continue
                seen.add(key)
                confidence = relationship_confidence(relation_text, item, relation_type)
                relationships.append(
                    SymbolRelationship(
                        source_symbol=source_symbol,
                        related_symbol=related_symbol,
                        relation_type=relation_type,
                        confidence=confidence,
                        evidence_title=item.title,
                        evidence_url=item.url,
                        query=item.query,
                        metadata={
                            "source": item.source,
                            "provider": item.provider,
                            "summary": item.summary,
                            "relationship_context": relation_context[:500],
                            "matched_symbols": item.symbols,
                        },
                    )
                )
    return dedupe_relationships(relationships)


def dedupe_relationships(items: list[SymbolRelationship]) -> list[SymbolRelationship]:
    best: dict[tuple[str, str, str], SymbolRelationship] = {}
    for item in items:
        key = (item.source_symbol, item.related_symbol, item.relation_type)
        current = best.get(key)
        if current is None or item.confidence > current.confidence:
            best[key] = item
    return sorted(best.values(), key=lambda item: item.confidence, reverse=True)


def explicit_related_symbols(text: str, source_symbol: str) -> set[str]:
    source = re.escape(source_symbol.upper())
    text_upper = text.upper()
    related: set[str] = set()
    patterns = [
        rf"2X\s+LONG\s+([A-Z0-9.]+)\s+DAILY\s+ETF\s*\(\s*{source}\s*\)",
        rf"{source}\s+2X\s+([A-Z0-9.]+)",
        rf"{source}[^.()]+NASDAQ:\s*([A-Z0-9.]+)",
        rf"{source}[^.()]+TICKER\s+([A-Z0-9.]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text_upper):
            candidate = match.group(1).strip(" .,:;()")
            if candidate and candidate != source_symbol.upper():
                related.add(candidate)
    return related


def relationship_context_for_source(text: str, source_symbol: str) -> str:
    source_symbol = source_symbol.upper()
    text_upper = text.upper()
    contexts: list[str] = []
    for match in re.finditer(rf"(?<![A-Z0-9]){re.escape(source_symbol)}(?![A-Z0-9])", text_upper):
        start = max(0, match.start() - 120)
        end = min(len(text), match.end() + 120)
        contexts.append(text[start:end])
    title = text.split(" ", 24)
    if title and source_symbol in " ".join(title).upper():
        contexts.append(" ".join(title))
    return " ".join(contexts)


def infer_relationship_type(text: str) -> str:
    if any(term in text for term in ("2x long", "2 x long", "two times", "200%", "daily leveraged", "leveraged etf")):
        return "leveraged_underlying_candidate"
    if any(term in text for term in ("underlying", "seeks daily", "fund holdings", "prospectus", "tracks", "etf")):
        return "fund_underlying_candidate"
    if any(term in text for term in ("read-through", "supplier", "customer", "peer", "competitor")):
        return "economic_link_candidate"
    return "weak_mention"


def relationship_confidence(text: str, item: WebEvidence, relation_type: str) -> float:
    confidence = 0.5
    if relation_type == "leveraged_underlying_candidate":
        confidence += 0.18
    elif relation_type == "fund_underlying_candidate":
        confidence += 0.1
    if any(term in text for term in ("prospectus", "issuer", "graniteshares", "direxion", "proshares", "rex shares")):
        confidence += 0.08
    if item.url:
        confidence += 0.04
    if item.provider != "strategy_curated":
        confidence += 0.02
    return round(min(0.85, confidence), 2)


def infer_event_types(text: str, url: str = "") -> list[str]:
    events: list[str] = []
    if ("eps" in text or "revenue" in text or "earnings" in text) and any(term in text for term in ("above guidance", "exceed", "surpass", "beat", "outperformance")):
        events.append("earnings_surprise")
    if "guidance" in text or "guide" in text or "outlook" in text:
        events.append("guidance_revision")
    if "data center" in text or "datacenter" in text:
        events.append("datacenter_mix_shift")
    if "nbm" in text or "new business model" in text or "multiyear" in text or "financial guarantee" in text or "supply agreement" in text:
        events.append("contracted_revenue_visibility")
    if "hbf" in text or "high bandwidth flash" in text or "bics8" in text or "stargate" in text:
        events.append("hbf_ai_inference_moat")
    if "sample" in text or "shipping" in text or "ramp" in text or "roadmap" in text:
        events.append("product_roadmap_acceleration")
    if source_tier(url) == "official":
        events.append("official_source_strength")
    if "sell-the-news" in text or "profit-taking" in text or "parabolic" in text or "volatility" in text:
        events.append("sell_the_news_volatility")
    return dedupe_strings(events)


def source_tier(url: str) -> str:
    host = host_from_url(url)
    if host in OFFICIAL_SOURCE_HOSTS:
        return "official"
    if host.endswith("sec.gov"):
        return "official"
    if "fool.com" in host:
        return "transcript"
    return "web"


def is_official_evidence(item: NewsItem) -> bool:
    tier = str(item.raw.get("source_tier", "") or item.raw.get("source_tier".upper(), "")).lower()
    if tier == "official":
        return True
    return source_tier(item.url) == "official"


def related_symbol_has_evidence(symbol: str, covered: set[str]) -> bool:
    if symbol == "SNXX" and "SNDK" in covered:
        return True
    if symbol == "SNDK" and "SNXX" in covered:
        return True
    return False


def host_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value).strip()
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result
