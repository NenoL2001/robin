from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..config import BotConfig
from ..data.x_api import XApiClient
from ..models import NewsItem, Quote
from ..runtime import RuntimeStore, runtime_path
from .sources import SourceRegistry, build_source_registry


class DataHub:
    """Unified local data access layer for quotes, news, and cached source payloads."""

    def __init__(
        self,
        config: BotConfig,
        *,
        runtime: RuntimeStore | None = None,
        finnhub=None,
        x_api: XApiClient | None = None,
        sources: SourceRegistry | None = None,
    ):
        self.config = config
        self.runtime = runtime or RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
        self.sources = sources or build_source_registry(config, finnhub=finnhub, x_api=x_api)
        self.finnhub = finnhub
        self.x_api = x_api or XApiClient(config.x_bearer_token)

    def quote(self, symbol: str, *, commit: bool = True) -> Quote | None:
        cached = self.runtime.quote_snapshot(symbol)
        if cached and quote_is_fresh(cached, self.config.data_hub.quote_cache_seconds):
            return cached
        try:
            quote = self._fetch_quote(symbol)
        except Exception as exc:
            self.runtime.record_log("WARNING", "data_hub", "quote", "quote fetch failed", {"symbol": symbol, "error": str(exc)})
            return cached
        if quote and commit:
            self.runtime.save_quote_snapshot(quote)
        return quote or cached

    def quotes(self, symbols: list[str], *, commit: bool = True) -> dict[str, Quote | None]:
        unique = sorted({symbol.upper() for symbol in symbols if symbol})
        if not unique:
            return {}
        workers = max(1, min(self.config.rate_limits.finnhub_concurrency, len(unique)))
        quotes: dict[str, Quote | None] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self.quote, symbol, commit=commit): symbol for symbol in unique}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    quotes[symbol] = future.result()
                except Exception as exc:
                    self.runtime.record_log("WARNING", "data_hub", "quotes", "quote batch item failed", {"symbol": symbol, "error": str(exc)})
                    quotes[symbol] = self.runtime.quote_snapshot(symbol)
        return quotes

    def collect_news(self, symbols: list[str], days: int = 3, *, commit: bool = True, force_refresh: bool = False, fresh_only: bool = False) -> list[NewsItem]:
        symbols = sorted({symbol.upper() for symbol in symbols if symbol})
        since = datetime.now(timezone.utc) - timedelta(days=days)
        refresh_symbols = [symbol for symbol in symbols if force_refresh or self._news_cache_stale(symbol)]
        fetched: list[NewsItem] = []
        if refresh_symbols:
            fetched.extend(self._fetch_news_sources(refresh_symbols, days))
            if commit:
                self._store_news(fetched)
                self.runtime.touch_news_cache(refresh_symbols)
        cached = self._cached_news(symbols, since=since)
        merged = {item.dedupe_key(): item for item in cached}
        for item in fetched:
            merged[item.dedupe_key()] = item
        items = list(merged.values())
        if commit:
            if fresh_only:
                fresh_keys = self.runtime.filter_fresh_news_keys({item.dedupe_key() for item in items}, commit=True)
                return [item for item in items if item.dedupe_key() in fresh_keys]
            return items
        return items

    async def collect_news_async(self, symbols: list[str], days: int = 3, *, commit: bool = True, force_refresh: bool = False, fresh_only: bool = False) -> list[NewsItem]:
        symbols = sorted({symbol.upper() for symbol in symbols if symbol})
        since = datetime.now(timezone.utc) - timedelta(days=days)
        refresh_symbols = [symbol for symbol in symbols if force_refresh or self._news_cache_stale(symbol)]
        fetched: list[NewsItem] = []
        if refresh_symbols:
            fetched.extend(await asyncio.to_thread(self._fetch_news_sources, refresh_symbols, days))
            if commit:
                self._store_news(fetched)
                self.runtime.touch_news_cache(refresh_symbols)
        cached = self._cached_news(symbols, since=since)
        merged = {item.dedupe_key(): item for item in cached}
        for item in fetched:
            merged[item.dedupe_key()] = item
        items = list(merged.values())
        if commit:
            if fresh_only:
                fresh_keys = self.runtime.filter_fresh_news_keys({item.dedupe_key() for item in items}, commit=True)
                return [item for item in items if item.dedupe_key() in fresh_keys]
            return items
        return items

    def _fetch_quote(self, symbol: str) -> Quote | None:
        for source in self.sources.quote_sources:
            try:
                quote = source.quote(symbol)
            except Exception as exc:
                self.runtime.record_log("WARNING", "data_hub", "quote_source_failed", "quote source failed", {"source": getattr(source, "name", source.__class__.__name__), "symbol": symbol, "error": str(exc)})
                continue
            if quote:
                return quote
        return None

    def _fetch_news_sources(self, symbols: list[str], days: int) -> list[NewsItem]:
        end = date.today()
        start = end - timedelta(days=days)
        items: list[NewsItem] = []
        for source in self.sources.news_sources:
            source_name = getattr(source, "name", source.__class__.__name__)
            batch_fetch = getattr(source, "company_news_many", None)
            if callable(batch_fetch):
                try:
                    items.extend(batch_fetch(symbols, start, end))
                except Exception as exc:
                    self.runtime.record_log("WARNING", "data_hub", "news_source_failed", "news source batch failed", {"source": source_name, "symbols": symbols[:50], "error": str(exc)})
                continue
            workers = max(1, min(self.config.rate_limits.finnhub_concurrency, len(symbols)))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(source.company_news, symbol, start, end): symbol for symbol in symbols}
                for future in as_completed(futures):
                    symbol = futures[future]
                    try:
                        items.extend(future.result())
                    except Exception as exc:
                        self.runtime.record_log("WARNING", "data_hub", "news_source_failed", "news source failed", {"source": source_name, "symbol": symbol, "error": str(exc)})
        return items

    def _store_news(self, items: list[NewsItem]) -> None:
        for item in items:
            self.runtime.upsert_news_item(
                item.dedupe_key(),
                title=item.title,
                url=item.url,
                source=item.source,
                symbols=item.symbols,
                summary=item.summary,
                kind=item.kind,
                published_at=item.published_at.isoformat() if item.published_at else "",
                raw=item.raw,
            )

    def _cached_news(self, symbols: list[str], *, since: datetime) -> list[NewsItem]:
        rows = self.runtime.recent_news_items(symbols, since=since, limit=1000)
        return [news_item_from_cache(row) for row in rows]

    def _news_cache_stale(self, symbol: str) -> bool:
        age = self.runtime.news_cache_age_seconds(symbol)
        return age is None or age > max(1, self.config.data_hub.news_cache_minutes) * 60


def quote_is_fresh(quote: Quote, max_age_seconds: int) -> bool:
    timestamp = quote.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - timestamp <= timedelta(seconds=max(1, max_age_seconds))


def news_item_from_cache(row: dict[str, Any]) -> NewsItem:
    published_at = None
    if row.get("published_at"):
        try:
            published_at = datetime.fromisoformat(str(row["published_at"]))
        except ValueError:
            published_at = None
    symbols = [part for part in str(row.get("symbols_text", "")).split(",") if part]
    return NewsItem(
        title=str(row.get("title", "")),
        url=str(row.get("url", "")),
        source=str(row.get("source", "")),
        published_at=published_at,
        symbols=symbols,
        summary=str(row.get("summary", "")),
        kind=str(row.get("kind", "news")),
        raw=dict(row.get("raw", {}) or {}),
    )
