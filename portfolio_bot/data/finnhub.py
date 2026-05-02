from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from ..models import NewsItem, Quote


class FinnhubClient:
    def __init__(self, api_key: str, timeout: int = 10):
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = "https://finnhub.io/api/v1"
        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def quote(self, symbol: str) -> Quote | None:
        if not self.configured:
            return None
        data = self._get("/quote", {"symbol": symbol.upper()})
        price = data.get("c")
        if price is None or float(price) <= 0:
            return None
        return Quote(
            symbol=symbol.upper(),
            price=float(price),
            timestamp=datetime.now(timezone.utc),
            change_percent=float(data["dp"]) if data.get("dp") is not None else None,
            previous_close=float(data["pc"]) if data.get("pc") is not None else None,
        )

    def company_news(self, symbol: str, start: date, end: date) -> list[NewsItem]:
        if not self.configured:
            return []
        rows = self._get("/company-news", {"symbol": symbol.upper(), "from": start.isoformat(), "to": end.isoformat()})
        if not isinstance(rows, list):
            return []
        items: list[NewsItem] = []
        for row in rows:
            ts = row.get("datetime")
            published = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            items.append(
                NewsItem(
                    title=str(row.get("headline", "")),
                    url=str(row.get("url", "")),
                    source=str(row.get("source", "Finnhub")),
                    published_at=published,
                    symbols=[symbol.upper()],
                    summary=str(row.get("summary", "")),
                    kind="company_news",
                    raw=row,
                )
            )
        return items

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        params = dict(params)
        params["token"] = self.api_key
        response = self._session.get(self.base_url + path, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()
