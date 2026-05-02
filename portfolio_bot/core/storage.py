from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

from .models import Holding, NewsItem, Quote


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Object is not JSON serializable: {type(value)!r}")


class Storage:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / "state.json"
        self.news_seen_path = self.data_dir / "seen_news.json"
        self.memory_path = self.data_dir / "strategy_memory.jsonl"

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text())

    def save_state(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state, indent=2, default=_json_default), encoding="utf-8")

    def load_seen_news(self) -> set[str]:
        if not self.news_seen_path.exists():
            return set()
        value = json.loads(self.news_seen_path.read_text())
        return set(value)

    def save_seen_news(self, seen: set[str]) -> None:
        self.news_seen_path.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")

    def append_memory(self, record: dict[str, Any]) -> None:
        with self.memory_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=_json_default, ensure_ascii=False) + "\n")


def load_holdings(path: Path) -> list[Holding]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("holdings", raw if isinstance(raw, list) else [])
    holdings: list[Holding] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("symbol"):
            continue
        holdings.append(
            Holding(
                symbol=str(item["symbol"]).upper(),
                name=str(item.get("name", "")),
                asset_type=item.get("asset_type", "equity"),
                quantity=float(item.get("quantity", 0) or 0),
                avg_cost=float(item["avg_cost"]) if item.get("avg_cost") is not None else None,
                market_value=float(item["market_value"]) if item.get("market_value") is not None else None,
                metadata=dict(item.get("metadata", {})),
            )
        )
    return holdings


def save_holdings(path: Path, holdings: Iterable[Holding]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "holdings": [
            {
                "symbol": h.symbol.upper(),
                "name": h.name,
                "asset_type": h.asset_type,
                "quantity": h.quantity,
                "avg_cost": h.avg_cost,
                "market_value": h.market_value,
                "metadata": h.metadata,
            }
            for h in holdings
        ]
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def load_analyst_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"analysts": [], "keywords": [], "cashtags": []}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {"analysts": [], "keywords": [], "cashtags": []}
    return raw


def quote_to_snapshot(quote: Quote) -> dict[str, Any]:
    return {
        "symbol": quote.symbol,
        "price": quote.price,
        "timestamp": quote.timestamp.isoformat(),
        "change_percent": quote.change_percent,
        "previous_close": quote.previous_close,
        "volume": quote.volume,
    }


def snapshot_to_quote(snapshot: dict[str, Any]) -> Quote:
    return Quote(
        symbol=str(snapshot["symbol"]),
        price=float(snapshot["price"]),
        timestamp=datetime.fromisoformat(snapshot["timestamp"]),
        change_percent=snapshot.get("change_percent"),
        previous_close=snapshot.get("previous_close"),
        volume=snapshot.get("volume"),
    )


def dedupe_news(items: Iterable[NewsItem], seen: set[str]) -> list[NewsItem]:
    fresh: list[NewsItem] = []
    for item in items:
        key = item.dedupe_key()
        if key in seen:
            continue
        fresh.append(item)
        seen.add(key)
    return fresh
