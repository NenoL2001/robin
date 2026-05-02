from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from ..config import BotConfig
from ..data.finnhub import FinnhubClient
from ..data.x_api import XApiClient
from ..models import NewsItem, Quote
from ..storage import load_analyst_config


class QuoteSource(Protocol):
    name: str

    def quote(self, symbol: str) -> Quote | None:
        ...


class NewsSource(Protocol):
    name: str

    def company_news(self, symbol: str, start: date, end: date) -> list[NewsItem]:
        ...


@dataclass(slots=True)
class SourceRegistry:
    quote_sources: list[QuoteSource]
    news_sources: list[NewsSource]


class XRecentPostsSource:
    name = "x_recent_posts"

    def __init__(self, config: BotConfig, client: XApiClient | None = None):
        self.config = config
        self.client = client or XApiClient(config.x_bearer_token)

    def company_news(self, symbol: str, start: date, end: date) -> list[NewsItem]:
        analyst_config = load_analyst_config(self.config.analysts_path)
        posts = self.client.recent_semiconductor_posts(analyst_config)
        symbol = symbol.upper()
        return [item for item in posts if symbol in {s.upper() for s in item.symbols} or symbol in item.title.upper()]

    def company_news_many(self, symbols: list[str], start: date, end: date) -> list[NewsItem]:
        analyst_config = load_analyst_config(self.config.analysts_path)
        posts = self.client.recent_semiconductor_posts(analyst_config)
        symbol_set = {symbol.upper() for symbol in symbols}
        seen: set[str] = set()
        items: list[NewsItem] = []
        for item in posts:
            item_symbols = {symbol.upper() for symbol in item.symbols}
            title = item.title.upper()
            if item_symbols & symbol_set or any(symbol in title for symbol in symbol_set):
                key = item.dedupe_key()
                if key not in seen:
                    seen.add(key)
                    items.append(item)
        return items


def build_source_registry(config: BotConfig, *, finnhub: FinnhubClient | None = None, x_api: XApiClient | None = None) -> SourceRegistry:
    quote_sources: list[QuoteSource] = []
    news_sources: list[NewsSource] = []
    finnhub_client = finnhub or FinnhubClient(config.finnhub_api_key)
    if getattr(finnhub_client, "configured", True):
        quote_sources.append(finnhub_client)
        news_sources.append(finnhub_client)
    x_source = XRecentPostsSource(config, x_api)
    news_sources.append(x_source)
    return SourceRegistry(quote_sources=quote_sources, news_sources=news_sources)
