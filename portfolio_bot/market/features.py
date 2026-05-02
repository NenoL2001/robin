from __future__ import annotations

from typing import Any

from ..models import Holding, NewsItem, Quote
from ..runtime import RuntimeStore
from .metrics import MetricService


FeatureSnapshot = dict[str, Any]


class FeatureEngine:
    def __init__(self, runtime: RuntimeStore, *, backend: str = "sync", max_workers: int = 4):
        self.runtime = runtime
        self.metrics = MetricService(runtime, backend=backend, max_workers=max_workers)

    def compute_many(
        self,
        symbols: list[str],
        quotes: dict[str, Quote | None],
        news: list[NewsItem],
        *,
        holdings: list[Holding] | None = None,
        commit: bool = True,
        backend: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        bundles = self.metrics.compute_many(symbols, quotes, news, holdings=holdings, commit=commit, backend=backend)
        return {symbol: bundle.to_features() for symbol, bundle in bundles.items()}

    def compute_symbol(
        self,
        symbol: str,
        quote: Quote | None,
        news: list[NewsItem],
        *,
        exposure: dict[str, Any] | None = None,
        commit: bool = True,
        backend: str | None = None,
    ) -> dict[str, Any]:
        return self.metrics.compute_symbol(symbol, quote, news, exposure=exposure, commit=commit, backend=backend).to_features()
