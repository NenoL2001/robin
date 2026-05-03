from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from ..config import BotConfig
from ..memory import MemoryStore, memory_path
from ..models import NewsItem, WebEvidence
from ..runtime import RuntimeStore, runtime_path
from .data_hub import DataHub
from .metrics import news_relevance
from .news_strategy import build_news_query_plan, dedupe_rank_news_items, enrich_news_item, enrich_web_evidence
from .strategy_news_scout import curated_evidence_for
from .web_search import WebSearchService


@dataclass(slots=True)
class DailyNewsDigest:
    digest_id: str
    symbols: list[str]
    summary: str
    items: list[NewsItem]
    web_evidence: list[WebEvidence]
    created_at: datetime
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        for item in payload["items"]:
            if item.get("published_at"):
                item["published_at"] = item["published_at"].isoformat() if hasattr(item["published_at"], "isoformat") else str(item["published_at"])
        for item in payload["web_evidence"]:
            if item.get("published_at"):
                item["published_at"] = item["published_at"].isoformat() if hasattr(item["published_at"], "isoformat") else str(item["published_at"])
            if item.get("discovered_at"):
                item["discovered_at"] = item["discovered_at"].isoformat() if hasattr(item["discovered_at"], "isoformat") else str(item["discovered_at"])
        return payload


class DailyNewsDigestBuilder:
    def __init__(
        self,
        config: BotConfig,
        *,
        data_hub: DataHub | None = None,
        web_search: WebSearchService | None = None,
        runtime: RuntimeStore | None = None,
        memory: MemoryStore | None = None,
    ):
        self.config = config
        self.runtime = runtime or RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
        self.memory = memory or MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
        self.data_hub = data_hub or DataHub(config, runtime=self.runtime)
        self.web_search = web_search or WebSearchService(config, runtime=self.runtime, memory=self.memory)

    def build(self, symbols: list[str], *, days: int = 3, commit: bool = True, include_web: bool = True) -> DailyNewsDigest:
        normalized = sorted({symbol.upper() for symbol in symbols if symbol})
        news = [enrich_news_item(item, normalized) for item in self.data_hub.collect_news(normalized, days=days, commit=commit)]
        web_items: list[WebEvidence] = [
            enrich_web_evidence(item, normalized, query_context={"source": "curated_regression_evidence"})
            for item in curated_evidence_for(normalized)
        ]
        query_log: list[dict[str, Any]] = []
        if include_web and normalized and self.config.research.web_search_enabled:
            max_queries = max(1, min(max(4, len(normalized) * 2), self.config.strategy_research.max_queries_per_strategy))
            plan = build_news_query_plan(normalized, max_queries=max_queries, official_sources_first=self.config.strategy_research.official_sources_first)
            for plan_item in plan:
                query_log.append(plan_item.to_dict())
                results = self.web_search.search(plan_item.query, symbols=[plan_item.symbol, *normalized], commit=commit)
                web_items.extend(enrich_web_evidence(item, normalized, query_context=plan_item.to_dict()) for item in results)
        items = dedupe_rank_news_items(dedupe_news_items([*news, *(item.to_news_item() for item in web_items)]), normalized)
        summary = render_digest_summary(normalized, items, web_items, query_log=query_log)
        digest = DailyNewsDigest(
            digest_id=f"daily_news_summary:{datetime.now(timezone.utc).date().isoformat()}:{','.join(normalized[:8])}",
            symbols=normalized,
            summary=summary,
            items=items,
            web_evidence=web_items,
            created_at=datetime.now(timezone.utc),
            metadata={"news_count": len(news), "web_evidence_count": len(web_items), "days": days, "query_log": query_log},
        )
        if commit:
            self._remember(digest)
        return digest

    def _remember(self, digest: DailyNewsDigest) -> None:
        self.memory.add(
            "daily_news_summary",
            digest.summary,
            symbol=",".join(digest.symbols[:12]),
            importance=0.78,
            confidence=0.65,
            source="daily_digest",
            metadata={
                "digest_id": digest.digest_id,
                "symbols": digest.symbols,
                "news_count": digest.metadata["news_count"],
                "web_evidence_count": digest.metadata["web_evidence_count"],
                "query_log": digest.metadata.get("query_log", []),
                "source_urls": [item.url for item in digest.items if item.url][:40],
            },
        )


def web_query(symbols: list[str]) -> str:
    if len(symbols) == 1:
        return f"{symbols[0]} semiconductor company news catalyst"
    return " ".join(symbols[:8]) + " semiconductor company news catalysts"


def dedupe_news_items(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    result: list[NewsItem] = []
    for item in items:
        key = item.dedupe_key()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def news_sort_key(item: NewsItem) -> tuple[float, float]:
    published = item.published_at
    if published is None:
        timestamp = 0.0
    else:
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        timestamp = published.timestamp()
    relevance = max((news_relevance(item, symbol) for symbol in item.symbols), default=0.0)
    return timestamp, relevance


def render_digest_summary(symbols: list[str], items: list[NewsItem], web_items: list[WebEvidence], *, query_log: list[dict[str, Any]] | None = None) -> str:
    query_log = query_log or []
    lines = [
        "每日新闻摘要",
        f"- 覆盖标的: {', '.join(symbols) or '暂无'}",
        f"- 合并新闻/网页证据: {len(items)} 条，其中 web evidence {len(web_items)} 条",
    ]
    if query_log:
        lines.append("- 已执行新闻 query: " + " | ".join(str(item.get("query", "")) for item in query_log[:8]))
    for item in items[:12]:
        published = item.published_at.date().isoformat() if item.published_at else "未知时间"
        symbols_text = ",".join(item.symbols[:5]) if item.symbols else "未匹配"
        evidence_label = source_evidence_label(item)
        event_values = ((item.raw or {}).get("event_types", []) or [])[:4] if isinstance(item.raw, dict) else []
        event_types = ",".join(str(value) for value in event_values)
        tier = str((item.raw or {}).get("source_tier", ""))
        quality = max((float(row.get("score") or 0.0) for row in (item.raw or {}).get("news_quality", []) if isinstance(row, dict)), default=0.0)
        url = f" {item.url}" if item.url else ""
        lines.append(f"- {published} {item.source}{evidence_label} [{symbols_text}] tier={tier or 'UNSPECIFIED'} quality={quality:.2f} events={event_types or 'none'}: {item.title}{url}")
    if web_items:
        weak = sum(1 for item in web_items if "web_result_low_confidence" in item.risk_flags)
        lines.append(f"- Web 证据默认低置信；低置信标记 {weak} 条，需匹配标的、来源和新鲜时间后才可支持订单证据。")
    return "\n".join(lines)


def source_evidence_label(item: NewsItem) -> str:
    handle = str(item.raw.get("handle", "")).lstrip("@")
    topics = [str(topic) for topic in item.raw.get("macro_topics", []) or [] if topic]
    parts = []
    if handle:
        parts.append(f"@{handle}")
    if topics:
        parts.append("topics=" + ",".join(topics[:3]))
    return " (" + "; ".join(parts) + ")" if parts else ""
