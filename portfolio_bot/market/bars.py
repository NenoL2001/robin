from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import BotConfig
from ..memory import MemoryStore, memory_path
from ..models import Quote
from ..runtime import RuntimeStore, runtime_path


@dataclass(slots=True)
class BarSnapshot:
    symbol: str
    window: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None
    source: str = "quote_snapshot"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat()
        return payload


class BarStore:
    def __init__(self, path: Path, *, memory: MemoryStore | None = None):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.memory = memory
        self._ensure()

    @classmethod
    def from_config(cls, config: BotConfig, *, memory: MemoryStore | None = None) -> "BarStore":
        return cls(config.data_dir / config.market_bars.sqlite_path, memory=memory)

    def upsert(self, bar: BarSnapshot, *, remember: bool = False) -> None:
        payload = bar.to_dict()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bar_snapshots(symbol, window, timestamp, open, high, low, close, volume, source, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, window, timestamp) DO UPDATE SET
                  open = excluded.open,
                  high = excluded.high,
                  low = excluded.low,
                  close = excluded.close,
                  volume = excluded.volume,
                  source = excluded.source,
                  metadata_json = excluded.metadata_json
                """,
                (
                    bar.symbol.upper(),
                    bar.window,
                    bar.timestamp.isoformat(),
                    float(bar.open),
                    float(bar.high),
                    float(bar.low),
                    float(bar.close),
                    int(bar.volume) if bar.volume is not None else None,
                    bar.source,
                    json.dumps(bar.metadata, ensure_ascii=False, default=str),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        if remember and self.memory:
            self.memory.add(
                "bar_snapshot",
                f"{bar.symbol.upper()} {bar.window} bar close={bar.close:g}, volume={bar.volume if bar.volume is not None else 'unknown'}",
                symbol=bar.symbol.upper(),
                importance=0.45,
                confidence=0.7 if bar.source != "quote_snapshot" else 0.55,
                source="bar_store",
                metadata=payload,
            )

    def latest(self, symbol: str, *, window: str = "1d", limit: int = 30) -> list[BarSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM bar_snapshots
                WHERE symbol = ? AND window = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (symbol.upper(), window, int(limit)),
            ).fetchall()
        return [bar_from_row(dict(row)) for row in rows]

    def latest_by_symbol(self, symbols: list[str], *, window: str = "1d", limit: int = 30) -> dict[str, list[BarSnapshot]]:
        return {symbol.upper(): self.latest(symbol, window=window, limit=limit) for symbol in symbols if symbol}

    def refresh_from_quotes(self, quotes: dict[str, Quote | None], *, window: str = "1d", commit: bool = True) -> dict[str, BarSnapshot]:
        bars: dict[str, BarSnapshot] = {}
        for symbol, quote in quotes.items():
            if not quote or quote.price <= 0:
                continue
            previous = quote.previous_close if quote.previous_close and quote.previous_close > 0 else quote.price
            high = max(float(previous), float(quote.price))
            low = min(float(previous), float(quote.price))
            bar = BarSnapshot(
                symbol=symbol.upper(),
                window=window,
                timestamp=quote.timestamp if quote.timestamp.tzinfo else quote.timestamp.replace(tzinfo=timezone.utc),
                open=float(previous),
                high=high,
                low=low,
                close=float(quote.price),
                volume=quote.volume,
                source="quote_snapshot",
                metadata={"change_percent": quote.change_percent, "previous_close": quote.previous_close},
            )
            bars[symbol.upper()] = bar
            if commit:
                self.upsert(bar, remember=True)
        return bars

    def seed_from_runtime_quotes(self, runtime: RuntimeStore, symbols: list[str], *, commit: bool = True) -> dict[str, BarSnapshot]:
        quotes = {symbol.upper(): runtime.quote_snapshot(symbol) for symbol in symbols if symbol}
        return self.refresh_from_quotes(quotes, commit=commit)

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bar_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  symbol TEXT NOT NULL,
                  window TEXT NOT NULL,
                  timestamp TEXT NOT NULL,
                  open REAL NOT NULL,
                  high REAL NOT NULL,
                  low REAL NOT NULL,
                  close REAL NOT NULL,
                  volume INTEGER,
                  source TEXT NOT NULL,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  UNIQUE(symbol, window, timestamp)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bar_snapshots_symbol_window ON bar_snapshots(symbol, window, timestamp)")


def bar_from_row(row: dict[str, Any]) -> BarSnapshot:
    try:
        metadata = json.loads(row.get("metadata_json") or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return BarSnapshot(
        symbol=str(row["symbol"]).upper(),
        window=str(row["window"]),
        timestamp=datetime.fromisoformat(str(row["timestamp"])),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row["volume"]) if row.get("volume") is not None else None,
        source=str(row.get("source") or "unknown"),
        metadata=metadata,
    )


def bar_store_from_config(config: BotConfig) -> BarStore:
    return BarStore.from_config(config, memory=MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled))


def runtime_for_config(config: BotConfig) -> RuntimeStore:
    return RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
