from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from ..models import NewsItem, WebEvidence
from .exposures import expand_leveraged_symbols, leveraged_exposure
from .metrics import COMMON_SYMBOL_ALIASES, generic_market_article, news_relevance, symbol_matches_text


@dataclass(frozen=True, slots=True)
class NewsQueryPlanItem:
    symbol: str
    query: str
    intent: str
    source_tier: str
    priority: int
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class NewsQuality:
    symbol: str
    score: float
    source_tier: str
    event_types: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FactorCandidateProposal:
    name: str
    direction: str
    weight: float
    reason: str
    evidence_event_types: tuple[str, ...] = ()
    min_observations_for_orders: int = 30

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SOURCE_TIER_WEIGHTS = {
    "P0_official": 0.32,
    "P1_transcript": 0.24,
    "P1_mainstream_finance": 0.18,
    "P1_industry_media": 0.18,
    "P2_profile_or_database": 0.12,
    "P2_web": 0.07,
    "P3_social": 0.02,
    "UNSPECIFIED": 0.0,
}

MAINSTREAM_FINANCE_HOSTS = {
    "reuters.com",
    "bloomberg.com",
    "cnbc.com",
    "marketwatch.com",
    "wsj.com",
    "barrons.com",
    "finance.yahoo.com",
}

INDUSTRY_MEDIA_HOSTS = {
    "tomshardware.com",
    "semianalysis.com",
    "servethehome.com",
    "anandtech.com",
    "storagereview.com",
    "blocksandfiles.com",
    "thestack.technology",
    "lightwaveonline.com",
}

PROFILE_HOSTS = {
    "stockanalysis.com",
    "etf.com",
    "etfdb.com",
    "graniteshares.com",
    "direxion.com",
    "proshares.com",
    "rexshares.com",
    "tradretfs.com",
}

TRANSCRIPT_HOSTS = {
    "fool.com",
    "seekingalpha.com",
    "quartr.com",
}

EVENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "earnings_surprise": ("earnings beat", "beats estimates", "above guidance", "outperformance", "exceeding guidance", "surpassed guidance"),
    "guidance_revision": ("raises guidance", "raised guidance", "guidance", "outlook", "guide", "forecast"),
    "datacenter_mix_shift": ("data center", "datacenter", "ai infrastructure", "ai server"),
    "contracted_revenue_visibility": ("contract", "agreement", "supply agreement", "multiyear", "prepayment", "financial guarantee", "new business model", "nbm"),
    "product_roadmap_acceleration": ("sample", "sampling", "shipping", "ramp", "roadmap", "launch", "qualification"),
    "hbf_ai_inference_moat": ("high bandwidth flash", "hbf", "bics8", "stargate", "ai inference"),
    "analyst_revision": ("upgrade", "downgrade", "price target", "initiates", "rating", "estimates raised", "estimate revision"),
    "capital_return": ("share repurchase", "buyback", "capital return", "authorization"),
    "dilution_risk": ("public offering", "stock offering", "private placement", "dilution", "convertible note"),
    "regulatory_risk": ("sec investigation", "subpoena", "export controls", "antitrust", "regulatory"),
    "post_event_volatility": ("sell-the-news", "profit-taking", "parabolic", "volatility", "short squeeze", "halted"),
    "relationship_underlying_link": ("2x long", "daily leveraged", "underlying", "seeks daily", "fund holdings", "tracks"),
    "supply_chain_readthrough": ("supplier", "customer", "peer", "read-through", "supply chain", "order flow"),
}

FACTOR_PROPOSAL_LIBRARY: dict[str, FactorCandidateProposal] = {
    "news_quality_score": FactorCandidateProposal(
        "news_quality_score",
        "positive",
        5.0,
        "Use deterministic evidence quality as a local numeric input before LLM synthesis.",
        ("official_source_strength",),
    ),
    "evidence_freshness_decay": FactorCandidateProposal(
        "evidence_freshness_decay",
        "negative",
        -2.0,
        "Decay stale catalysts so old web results do not keep driving orders.",
        (),
    ),
    "source_diversity_confirmation": FactorCandidateProposal(
        "source_diversity_confirmation",
        "positive",
        3.0,
        "Reward independent official/transcript/media confirmation instead of repeated syndicated snippets.",
        (),
    ),
    "analyst_revision_breadth": FactorCandidateProposal(
        "analyst_revision_breadth",
        "positive",
        4.0,
        "Capture estimate or price-target revision breadth after confirmed events.",
        ("analyst_revision",),
    ),
    "relationship_event_pass_through": FactorCandidateProposal(
        "relationship_event_pass_through",
        "positive",
        4.5,
        "Connect leveraged ETF/single-stock product symbols to the underlying event stream.",
        ("relationship_underlying_link",),
    ),
    "post_event_drift_followthrough": FactorCandidateProposal(
        "post_event_drift_followthrough",
        "positive",
        0.4,
        "Measure whether price holds after verified news instead of fading immediately.",
        ("post_event_volatility",),
    ),
    "liquidity_break_risk": FactorCandidateProposal(
        "liquidity_break_risk",
        "negative",
        -4.0,
        "Penalize large moves with poor close location or weak liquidity confirmation.",
        ("post_event_volatility",),
    ),
}


def build_news_query_plan(
    symbols: list[str],
    *,
    max_queries: int,
    official_sources_first: bool = True,
    include_social: bool = False,
) -> list[NewsQueryPlanItem]:
    expanded = sorted(expand_leveraged_symbols(symbols))
    rows: list[NewsQueryPlanItem] = []
    for symbol in expanded:
        aliases = tuple(symbol_news_aliases(symbol))
        rows.extend(symbol_query_templates(symbol, aliases, include_social=include_social))
    rows = dedupe_query_plan(rows)
    rows.sort(key=lambda item: (item.priority if official_sources_first else source_sort_rank(item.source_tier), item.symbol, item.query))
    return rows[: max(1, int(max_queries or 1))]


def symbol_query_templates(symbol: str, aliases: tuple[str, ...], *, include_social: bool) -> list[NewsQueryPlanItem]:
    alias_text = " OR ".join(f'"{alias}"' if " " in alias else alias for alias in aliases[:5])
    primary_alias = aliases[0] if aliases else symbol
    rows = [
        NewsQueryPlanItem(symbol, f"{alias_text} investor relations earnings guidance press release", "official_ir", "P0_official", 10, aliases),
        NewsQueryPlanItem(symbol, f"site:sec.gov {primary_alias} 8-K 10-Q guidance revenue customer agreement", "sec_filings", "P0_official", 11, aliases),
        NewsQueryPlanItem(symbol, f"{alias_text} earnings transcript revenue margin guidance", "transcript", "P1_transcript", 20, aliases),
        NewsQueryPlanItem(symbol, f"{alias_text} product roadmap customer contract data center AI", "event_discovery", "P1_industry_media", 30, aliases),
        NewsQueryPlanItem(symbol, f"{alias_text} daily move catalyst volume short interest options", "market_behavior", "P2_web", 40, aliases),
    ]
    exposure = leveraged_exposure(symbol)
    if exposure:
        rows.insert(
            0,
            NewsQueryPlanItem(
                symbol,
                f"{symbol} {exposure.underlying} 2x long underlying issuer prospectus daily leveraged ETF",
                "relationship_underlying",
                "P2_profile_or_database",
                8,
                aliases,
            ),
        )
    if symbol in {"SNXX", "SNDK"}:
        rows.insert(0, NewsQueryPlanItem(symbol, "site:investor.sandisk.com SNDK fiscal third quarter 2026 financial results revenue EPS guidance", "official_ir", "P0_official", 1, aliases))
        rows.insert(1, NewsQueryPlanItem(symbol, "site:documents.sandisk.com Sandisk HBF fact sheet High Bandwidth Flash BiCS8", "official_product", "P0_official", 2, aliases))
        rows.insert(2, NewsQueryPlanItem(symbol, "Sandisk SNDK earnings transcript datacenter NBM financial guarantees HBF Stargate", "transcript", "P1_transcript", 15, aliases))
    if symbol in {"LITX", "LITE"}:
        rows.insert(0, NewsQueryPlanItem(symbol, "LITX LITE 2x long underlying issuer prospectus Lumentum", "relationship_underlying", "P2_profile_or_database", 3, aliases))
    if include_social:
        rows.append(NewsQueryPlanItem(symbol, f"{alias_text} stocktwits twitter reddit catalyst rumor", "social_leads", "P3_social", 80, aliases))
    return rows


def symbol_news_aliases(symbol: str) -> list[str]:
    symbol = symbol.upper().strip()
    aliases = [symbol]
    aliases.extend(COMMON_SYMBOL_ALIASES.get(symbol, []))
    exposure = leveraged_exposure(symbol)
    if exposure:
        aliases.append(exposure.underlying)
        aliases.extend(COMMON_SYMBOL_ALIASES.get(exposure.underlying, []))
    if symbol in {"SNXX", "SNDK"}:
        aliases.extend(["Sandisk", "SanDisk", "High Bandwidth Flash", "HBF", "BiCS8", "NBM", "Stargate"])
    if symbol in {"LITX", "LITE"}:
        aliases.extend(["Lumentum", "2x Long LITE", "LITE Daily ETF"])
    return dedupe_strings(aliases)


def enrich_web_evidence(item: WebEvidence, requested_symbols: list[str], *, query_context: dict[str, Any] | None = None) -> WebEvidence:
    requested = sorted(expand_leveraged_symbols(requested_symbols))
    text = f"{item.title} {item.summary}"
    matched = attach_related_symbols(dedupe_strings([*item.symbols, *[symbol for symbol in requested if symbol_matches_text(symbol, text)]]), requested)
    tier = classify_source_tier(item.url, item.source or item.provider)
    event_types = dedupe_strings([*map(str, item.raw.get("event_types", []) or []), *extract_event_types(text, item.url)])
    risk_flags = dedupe_strings([*item.risk_flags, *evidence_risk_flags(tier, item.published_at)])
    quality_scores = [news_quality_for_text(text, item.url, item.source, symbol, item.published_at, event_types) for symbol in (matched or requested)]
    best_quality = max((score.score for score in quality_scores), default=item.relevance_score)
    raw = dict(item.raw or {})
    raw.update(
        {
            "source_tier": tier,
            "event_types": event_types,
            "news_quality": [score.to_dict() for score in quality_scores],
            "query_context": query_context or {},
        }
    )
    return WebEvidence(
        title=item.title,
        url=item.url,
        source=item.source,
        query=item.query,
        symbols=matched,
        summary=item.summary,
        published_at=item.published_at,
        discovered_at=item.discovered_at,
        confidence=round(max(item.confidence, min(0.9, 0.25 + best_quality * 0.65)), 2),
        relevance_score=round(max(item.relevance_score, best_quality), 3),
        provider=item.provider,
        risk_flags=risk_flags,
        raw=raw,
    )


def enrich_news_item(item: NewsItem, requested_symbols: list[str], *, query_context: dict[str, Any] | None = None) -> NewsItem:
    requested = sorted(expand_leveraged_symbols(requested_symbols))
    text = f"{item.title} {item.summary}"
    matched = attach_related_symbols(dedupe_strings([*item.symbols, *[symbol for symbol in requested if symbol_matches_text(symbol, text)]]), requested)
    tier = classify_source_tier(item.url, item.source)
    event_types = dedupe_strings([*map(str, item.raw.get("event_types", []) or []), *extract_event_types(text, item.url)])
    quality_scores = [news_quality_for_text(text, item.url, item.source, symbol, item.published_at, event_types) for symbol in (matched or requested)]
    raw = dict(item.raw or {})
    raw.update(
        {
            "source_tier": raw.get("source_tier") or tier,
            "event_types": event_types,
            "news_quality": [score.to_dict() for score in quality_scores],
            "query_context": query_context or raw.get("query_context", {}),
        }
    )
    return NewsItem(
        title=item.title,
        url=item.url,
        source=item.source,
        published_at=item.published_at,
        symbols=matched,
        summary=item.summary,
        kind=item.kind,
        raw=raw,
    )


def classify_source_tier(url: str, source: str = "") -> str:
    host = source_host(url)
    source_lower = (source or "").lower()
    if host.endswith("sec.gov") or "investor." in host or host.startswith("ir.") or "documents." in host:
        return "P0_official"
    if any(host.endswith(value) for value in TRANSCRIPT_HOSTS):
        return "P1_transcript"
    if any(host.endswith(value) for value in MAINSTREAM_FINANCE_HOSTS):
        return "P1_mainstream_finance"
    if any(host.endswith(value) for value in INDUSTRY_MEDIA_HOSTS):
        return "P1_industry_media"
    if any(host.endswith(value) for value in PROFILE_HOSTS):
        return "P2_profile_or_database"
    if host in {"x.com", "twitter.com", "reddit.com"} or source_lower in {"x", "twitter", "reddit"}:
        return "P3_social"
    if host:
        return "P2_web"
    return "UNSPECIFIED"


def extract_event_types(text: str, url: str = "") -> list[str]:
    lowered = (text or "").lower()
    events = [event_type for event_type, keywords in EVENT_KEYWORDS.items() if any(keyword in lowered for keyword in keywords)]
    if classify_source_tier(url) == "P0_official":
        events.append("official_source_strength")
    return dedupe_strings(events)


def evidence_risk_flags(source_tier: str, published_at: datetime | None) -> list[str]:
    flags: list[str] = []
    if source_tier in {"P2_web", "P3_social", "UNSPECIFIED"}:
        flags.append("low_source_tier")
    if source_tier == "P3_social":
        flags.append("social_lead_not_order_evidence")
    if not published_at:
        flags.append("missing_fresh_timestamp")
    return flags


def news_quality_score(item: NewsItem, symbol: str) -> tuple[float, list[str]]:
    qualities = item.raw.get("news_quality") if isinstance(item.raw, dict) else None
    if isinstance(qualities, list):
        for row in qualities:
            if isinstance(row, dict) and str(row.get("symbol", "")).upper() == symbol.upper():
                return float(row.get("score") or 0.0), [str(value) for value in row.get("reasons", []) or []]
    quality = news_quality_for_text(
        f"{item.title} {item.summary}",
        item.url,
        item.source,
        symbol,
        item.published_at,
        [str(value) for value in (item.raw or {}).get("event_types", []) or []],
    )
    return quality.score, list(quality.reasons)


def news_quality_for_text(
    text: str,
    url: str,
    source: str,
    symbol: str,
    published_at: datetime | None,
    event_types: list[str],
) -> NewsQuality:
    tier = classify_source_tier(url, source)
    relevance = 0.0
    pseudo = NewsItem(title=text[:220], url=url, source=source, published_at=published_at, symbols=[symbol] if symbol_matches_text(symbol, text) else [], summary=text)
    relevance = news_relevance(pseudo, symbol)
    score = 0.08 + SOURCE_TIER_WEIGHTS.get(tier, 0.0) + relevance * 0.32
    reasons: list[str] = [tier]
    if relevance >= 0.7:
        reasons.append("strong_symbol_match")
    elif relevance >= 0.45:
        reasons.append("symbol_match")
    if event_types:
        score += min(0.18, 0.045 * len(event_types))
        reasons.append("events:" + ",".join(event_types[:4]))
    freshness = freshness_score(published_at)
    if freshness:
        score += freshness
        reasons.append("fresh")
    if url:
        score += 0.025
        reasons.append("cited")
    if generic_market_article(text.lower()):
        score -= 0.22
        reasons.append("generic_market_penalty")
    return NewsQuality(symbol.upper(), round(max(0.0, min(1.0, score)), 3), tier, tuple(event_types), tuple(reasons))


def dedupe_rank_news_items(items: list[NewsItem], symbols: list[str], *, max_items: int | None = None) -> list[NewsItem]:
    enriched = [enrich_news_item(item, symbols) for item in items]
    best: dict[str, NewsItem] = {}
    for item in enriched:
        key = semantic_news_key(item)
        current = best.get(key)
        if current is None or best_item_quality(item, symbols) > best_item_quality(current, symbols):
            best[key] = item
    ordered = sorted(best.values(), key=lambda item: (best_item_quality(item, symbols), item.published_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return ordered[:max_items] if max_items else ordered


def propose_factor_candidates_from_news(items: list[NewsItem], existing_names: set[str]) -> list[FactorCandidateProposal]:
    event_types = {event for item in items for event in (item.raw or {}).get("event_types", []) or []}
    proposals: list[FactorCandidateProposal] = []
    mapping = {
        "analyst_revision": "analyst_revision_breadth",
        "relationship_underlying_link": "relationship_event_pass_through",
        "post_event_volatility": "post_event_drift_followthrough",
        "official_source_strength": "news_quality_score",
    }
    for event_type, factor_name in mapping.items():
        if event_type in event_types and factor_name not in existing_names:
            proposals.append(FACTOR_PROPOSAL_LIBRARY[factor_name])
    for factor_name in ("evidence_freshness_decay", "source_diversity_confirmation", "liquidity_break_risk"):
        if factor_name not in existing_names:
            proposals.append(FACTOR_PROPOSAL_LIBRARY[factor_name])
    return proposals


def attach_related_symbols(symbols: list[str], requested_symbols: list[str]) -> list[str]:
    result = {symbol.upper() for symbol in symbols if symbol}
    requested = {symbol.upper() for symbol in requested_symbols if symbol}
    for symbol in list(result | requested):
        exposure = leveraged_exposure(symbol)
        if exposure and exposure.underlying in result:
            result.add(symbol)
        if exposure and symbol in result:
            result.add(exposure.underlying)
    if "SNDK" in result and "SNXX" in requested:
        result.add("SNXX")
    if "LITE" in result and "LITX" in requested:
        result.add("LITX")
    return sorted(result)


def best_item_quality(item: NewsItem, symbols: list[str]) -> float:
    return max((news_quality_score(item, symbol)[0] for symbol in symbols if symbol), default=0.0)


def semantic_news_key(item: NewsItem) -> str:
    if item.url:
        return item.url.strip().lower().split("#", 1)[0]
    title = re.sub(r"[^a-z0-9]+", " ", item.title.lower()).strip()
    return f"{item.source.lower()}:{title[:120]}"


def freshness_score(published_at: datetime | None) -> float:
    if not published_at:
        return 0.0
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - published_at
    if age <= timedelta(days=2):
        return 0.12
    if age <= timedelta(days=7):
        return 0.08
    if age <= timedelta(days=30):
        return 0.035
    return 0.0


def source_host(url: str) -> str:
    try:
        return urlparse(url or "").netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def source_sort_rank(source_tier: str) -> int:
    order = {
        "P0_official": 0,
        "P1_transcript": 1,
        "P1_mainstream_finance": 2,
        "P1_industry_media": 3,
        "P2_profile_or_database": 4,
        "P2_web": 5,
        "P3_social": 6,
    }
    return order.get(source_tier, 9)


def dedupe_query_plan(rows: list[NewsQueryPlanItem]) -> list[NewsQueryPlanItem]:
    seen: set[str] = set()
    result: list[NewsQueryPlanItem] = []
    for row in rows:
        key = row.query.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result
