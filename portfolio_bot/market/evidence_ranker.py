from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from ..config import BotConfig
from ..memory import MemoryStore, memory_path
from ..models import NewsItem
from .metrics import generic_market_article, news_relevance, symbol_matches_text
from .news_strategy import classify_source_tier, enrich_news_item, news_quality_score


OFFICIAL_HOST_HINTS = {
    "investor.",
    "ir.",
    "sec.gov",
    "documents.",
}


@dataclass(slots=True)
class RankedEvidence:
    symbol: str
    title: str
    url: str
    source: str
    published_at: datetime | None
    score: float
    reasons: list[str] = field(default_factory=list)
    item: NewsItem | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at.isoformat() if self.published_at else "",
            "score": self.score,
            "reasons": self.reasons,
        }
        if self.item:
            payload["item"] = {
                "title": self.item.title,
                "url": self.item.url,
                "source": self.item.source,
                "symbols": self.item.symbols,
                "summary": self.item.summary,
                "kind": self.item.kind,
                "raw": self.item.raw,
            }
        return payload


class EvidenceRanker:
    def __init__(self, config: BotConfig, *, memory: MemoryStore | None = None):
        self.config = config
        self.memory = memory or MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)

    def rank_news(
        self,
        news: list[NewsItem],
        symbols: list[str],
        *,
        commit: bool = False,
        max_items_per_symbol: int | None = None,
    ) -> list[RankedEvidence]:
        normalized = [symbol.upper() for symbol in symbols if symbol]
        ranked: list[RankedEvidence] = []
        seen_urls: set[str] = set()
        source_counts: dict[str, int] = {}
        enriched_news = [enrich_news_item(item, normalized) for item in news]
        for item in enriched_news:
            text = f"{item.title} {item.summary}"
            item_symbols = {symbol.upper() for symbol in item.symbols if symbol}
            matched = item_symbols & set(normalized)
            if not matched:
                matched = {symbol for symbol in normalized if symbol_matches_text(symbol, text)}
            for symbol in matched:
                score, reasons = score_evidence(item, symbol)
                source_key = (item.source or source_host(item.url)).lower()
                source_counts[source_key] = source_counts.get(source_key, 0) + 1
                if source_counts[source_key] > 3:
                    score -= 0.08
                    reasons.append("source_duplicate_penalty")
                url_key = (item.url or f"{item.source}:{item.title}").lower()
                if url_key in seen_urls:
                    score -= 0.12
                    reasons.append("url_duplicate_penalty")
                seen_urls.add(url_key)
                ranked.append(
                    RankedEvidence(
                        symbol=symbol,
                        title=item.title,
                        url=item.url,
                        source=item.source or source_host(item.url),
                        published_at=item.published_at,
                        score=round(max(0.0, min(1.0, score)), 3),
                        reasons=reasons,
                        item=item,
                    )
                )
        ranked.sort(key=lambda row: (row.symbol, row.score, row.published_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        limited = limit_ranked(ranked, max_items_per_symbol or self.config.evidence_ranker.max_items_per_symbol)
        if commit:
            self._remember(limited)
        return limited

    def top_news_items(self, news: list[NewsItem], symbols: list[str], *, commit: bool = False) -> list[NewsItem]:
        ranked = self.rank_news(news, symbols, commit=commit)
        selected: list[NewsItem] = []
        seen: set[str] = set()
        for row in ranked:
            if not row.item:
                continue
            key = row.item.dedupe_key()
            if key in seen:
                continue
            raw = dict(row.item.raw or {})
            raw["evidence_rank"] = {"symbol": row.symbol, "score": row.score, "reasons": row.reasons}
            selected.append(
                NewsItem(
                    title=row.item.title,
                    url=row.item.url,
                    source=row.item.source,
                    published_at=row.item.published_at,
                    symbols=row.item.symbols,
                    summary=row.item.summary,
                    kind=row.item.kind,
                    raw=raw,
                )
            )
            seen.add(key)
        return selected

    def _remember(self, ranked: list[RankedEvidence]) -> None:
        for item in ranked:
            self.memory.add(
                "ranked_evidence",
                f"{item.symbol} evidence score={item.score:.3f}: {item.source} {item.title}",
                symbol=item.symbol,
                importance=min(0.9, 0.35 + item.score * 0.55),
                confidence=item.score,
                source="evidence_ranker",
                metadata=item.to_dict(),
                evidence_links=[item.url] if item.url else [],
            )


def score_evidence(item: NewsItem, symbol: str) -> tuple[float, list[str]]:
    quality_score, quality_reasons = news_quality_score(item, symbol)
    text = f"{item.title} {item.summary}"
    lowered = text.lower()
    score = max(0.18, quality_score)
    reasons: list[str] = list(quality_reasons)
    relevance = news_relevance(item, symbol)
    score += relevance * 0.12
    if relevance >= 0.7:
        reasons.append("strong_symbol_match")
    elif relevance >= 0.45:
        reasons.append("symbol_match")
    tier = str((item.raw or {}).get("source_tier", "") or classify_source_tier(item.url, item.source)).lower()
    host = source_host(item.url)
    if tier in {"official", "p0_official"} or any(hint in host for hint in OFFICIAL_HOST_HINTS):
        score += 0.1
        reasons.append("official_or_primary")
    elif tier in {"transcript", "issuer_or_profile", "p1_transcript", "p2_profile_or_database"}:
        score += 0.07
        reasons.append(tier)
    elif tier in {"industry_media", "mainstream_finance", "p1_industry_media", "p1_mainstream_finance"}:
        score += 0.04
        reasons.append(tier)
    freshness = freshness_bonus(item.published_at)
    if freshness > 0:
        score += freshness
        reasons.append("fresh")
    event_types = [str(value) for value in (item.raw or {}).get("event_types", []) or []]
    if event_types:
        score += min(0.16, 0.05 * len(event_types))
        reasons.append("structured_event:" + ",".join(event_types[:3]))
    if item.url:
        score += 0.03
        reasons.append("cited")
    if generic_market_article(lowered):
        score -= 0.22
        reasons.append("generic_market_penalty")
    if "yahoo" in (item.source or "").lower() and not symbol_matches_text(symbol, text):
        score -= 0.14
        reasons.append("generic_yahoo_penalty")
    if "nvda" in lowered and symbol.upper() != "NVDA" and not symbol_matches_text(symbol, text):
        score -= 0.12
        reasons.append("off_symbol_nvda_penalty")
    return score, reasons


def freshness_bonus(published_at: datetime | None) -> float:
    if not published_at:
        return 0.0
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - published_at
    if age <= timedelta(days=2):
        return 0.14
    if age <= timedelta(days=7):
        return 0.1
    if age <= timedelta(days=30):
        return 0.04
    return 0.0


def source_host(url: str) -> str:
    try:
        return urlparse(url or "").netloc.lower()
    except ValueError:
        return ""


def limit_ranked(ranked: list[RankedEvidence], max_items_per_symbol: int) -> list[RankedEvidence]:
    counts: dict[str, int] = {}
    result: list[RankedEvidence] = []
    for item in sorted(ranked, key=lambda row: row.score, reverse=True):
        counts[item.symbol] = counts.get(item.symbol, 0)
        if counts[item.symbol] >= max(1, max_items_per_symbol):
            continue
        result.append(item)
        counts[item.symbol] += 1
    return sorted(result, key=lambda row: (row.symbol, row.score), reverse=True)
