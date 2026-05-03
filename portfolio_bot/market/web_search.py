from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import requests

from ..config import BotConfig
from ..memory import MemoryStore, memory_path
from ..models import WebEvidence
from ..runtime import RuntimeStore, runtime_path
from .metrics import symbol_matches_text
from .news_strategy import enrich_web_evidence


class WebSearchProvider(Protocol):
    name: str

    def search(self, query: str, *, symbols: list[str], max_results: int, timeout: int) -> list[WebEvidence]:
        ...


class DuckDuckGoHtmlProvider:
    name = "duckduckgo"

    def __init__(self, session: Any | None = None):
        self.session = session or requests.Session()

    def search(self, query: str, *, symbols: list[str], max_results: int, timeout: int) -> list[WebEvidence]:
        response = self.session.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "portfolio-bot/0.1 (+paper research)"},
            timeout=timeout,
        )
        response.raise_for_status()
        parser = DuckDuckGoHtmlParser()
        parser.feed(response.text)
        rows = parser.results[: max(1, max_results)]
        return [
            web_evidence_from_search_row(
                row.get("title", ""),
                clean_duckduckgo_url(row.get("url", "")),
                row.get("snippet", ""),
                query,
                symbols,
                provider=self.name,
            )
            for row in rows
            if row.get("title") or row.get("url")
        ]


class TavilySearchProvider:
    name = "tavily"

    def __init__(self, api_key: str | None = None, session: Any | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("TAVILY_API_KEY", "")
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, *, symbols: list[str], max_results: int, timeout: int) -> list[WebEvidence]:
        if not self.configured:
            return []
        response = self.session.post(
            "https://api.tavily.com/search",
            json={
                "api_key": self.api_key,
                "query": query,
                "max_results": max(1, max_results),
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("results") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return []
        results: list[WebEvidence] = []
        for row in rows[: max(1, max_results)]:
            if not isinstance(row, dict):
                continue
            published_at = parse_datetime(row.get("published_date") or row.get("published_at"))
            results.append(
                web_evidence_from_search_row(
                    str(row.get("title", "")),
                    str(row.get("url", "")),
                    str(row.get("content", "") or row.get("snippet", "")),
                    query,
                    symbols,
                    provider=self.name,
                    published_at=published_at,
                    raw={key: value for key, value in row.items() if key not in {"title", "url", "content", "snippet"}},
                )
            )
        return results


class WebSearchService:
    def __init__(
        self,
        config: BotConfig,
        *,
        runtime: RuntimeStore | None = None,
        memory: MemoryStore | None = None,
        duckduckgo: WebSearchProvider | None = None,
        tavily: WebSearchProvider | None = None,
    ):
        self.config = config
        self.runtime = runtime or RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
        self.memory = memory or MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
        self.duckduckgo = duckduckgo or DuckDuckGoHtmlProvider()
        self.tavily = tavily or TavilySearchProvider()

    def search(self, query: str, *, symbols: list[str] | None = None, commit: bool = True) -> list[WebEvidence]:
        if not self.config.research.web_search_enabled:
            return []
        symbols = [symbol.upper() for symbol in (symbols or []) if symbol]
        provider = self._provider()
        try:
            results = provider.search(
                query,
                symbols=symbols,
                max_results=self.config.research.web_search_max_results,
                timeout=self.config.research.web_search_timeout_seconds,
            )
        except Exception as exc:
            self.runtime.record_log("WARNING", "web_search", "web_search", "web search failed", {"query": query, "error": str(exc), "provider": getattr(provider, "name", "")})
            return []
        enriched = [enrich_web_evidence(item, symbols) for item in results]
        deduped = dedupe_web_evidence(enriched)
        if commit:
            self._remember(query, deduped)
        return deduped

    def _provider(self) -> WebSearchProvider:
        api_provider = self.config.research.web_search_api_provider.lower()
        if api_provider == "tavily" and getattr(self.tavily, "configured", False):
            return self.tavily
        return self.duckduckgo

    def _remember(self, query: str, results: list[WebEvidence]) -> None:
        for item in results:
            self.memory.add(
                "web_evidence",
                f"{item.source}: {item.title} {item.summary}".strip(),
                symbol=",".join(item.symbols[:8]),
                importance=min(0.75, 0.3 + item.relevance_score),
                confidence=item.confidence,
                source=item.provider,
                metadata={
                    "query": query,
                    "url": item.url,
                    "source": item.source,
                    "symbols": item.symbols,
                    "published_at": item.published_at.isoformat() if item.published_at else "",
                    "discovered_at": item.discovered_at.isoformat(),
                    "relevance_score": item.relevance_score,
                    "risk_flags": item.risk_flags,
                },
            )


class DuckDuckGoHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._field = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        klass = attr.get("class", "")
        if tag == "a" and "result__a" in klass:
            self._current = {"title": "", "url": attr.get("href", ""), "snippet": ""}
            self._field = "title"
        elif self._current is not None and "result__snippet" in klass:
            self._field = "snippet"

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._field:
            self._current[self._field] = (self._current.get(self._field, "") + " " + data).strip()

    def handle_endtag(self, tag: str) -> None:
        if self._current is not None and tag == "a" and self._field == "title":
            if self._current.get("title") or self._current.get("url"):
                self.results.append(self._current)
            self._field = ""
        elif self._current is not None and self._field == "snippet" and tag in {"a", "div"}:
            self._current = None
            self._field = ""


def web_evidence_from_search_row(
    title: str,
    url: str,
    summary: str,
    query: str,
    symbols: list[str],
    *,
    provider: str,
    published_at: datetime | None = None,
    raw: dict[str, Any] | None = None,
) -> WebEvidence:
    matched = match_symbols(symbols, f"{title} {summary}")
    relevance = 0.6 if matched else 0.25
    confidence = 0.35
    risk_flags = ["web_result_low_confidence"]
    if url:
        confidence += 0.05
    if matched:
        confidence += 0.1
    if published_at:
        confidence += 0.08
    else:
        risk_flags.append("missing_fresh_timestamp")
    return WebEvidence(
        title=" ".join(title.split()),
        url=url,
        source=source_from_url(url) or provider,
        query=query,
        symbols=matched,
        summary=" ".join(summary.split()),
        published_at=published_at,
        discovered_at=datetime.now(timezone.utc),
        confidence=round(min(0.75, confidence), 2),
        relevance_score=relevance,
        provider=provider,
        risk_flags=risk_flags,
        raw=raw or {},
    )


def match_symbols(symbols: list[str], text: str) -> list[str]:
    return [symbol.upper() for symbol in symbols if symbol_matches_text(symbol, text)]


def clean_duckduckgo_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if ("duckduckgo.com" in parsed.netloc or not parsed.netloc) and parsed.path.startswith("/l/"):
        values = parse_qs(parsed.query).get("uddg")
        if values:
            return unquote(values[0])
    return url


def source_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    host = re.sub(r"^www\.", "", host)
    return host


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def dedupe_web_evidence(items: list[WebEvidence]) -> list[WebEvidence]:
    seen: set[str] = set()
    result: list[WebEvidence] = []
    for item in items:
        key = item.dedupe_key()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
