from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import BotConfig
from ..memory import MemoryStore, memory_path
from ..models import Quote, StrategySignal


@dataclass(slots=True)
class FactorAttributionSummary:
    factor_name: str
    observation_count: int
    avg_contribution: float
    avg_forward_return: float
    directional_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_name": self.factor_name,
            "observation_count": self.observation_count,
            "avg_contribution": self.avg_contribution,
            "avg_forward_return": self.avg_forward_return,
            "directional_score": self.directional_score,
        }


class FactorAttributionStore:
    def __init__(self, path: Path, *, memory: MemoryStore | None = None, enabled: bool = True):
        self.path = path
        self.memory = memory
        self.enabled = enabled
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._ensure()

    @classmethod
    def from_config(cls, config: BotConfig, *, memory: MemoryStore | None = None) -> "FactorAttributionStore":
        return cls(
            config.data_dir / "factor_attribution.sqlite",
            memory=memory or MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled),
            enabled=True,
        )

    def _ensure(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS factor_attribution (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    factor_name TEXT NOT NULL,
                    factor_value REAL NOT NULL,
                    contribution REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    observed_at TEXT NOT NULL,
                    forward_1d_return REAL,
                    forward_3d_return REAL,
                    forward_5d_return REAL,
                    metadata_json TEXT NOT NULL,
                    UNIQUE(signal_id, factor_name)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_factor_attribution_factor ON factor_attribution(factor_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_factor_attribution_symbol ON factor_attribution(symbol)")

    def record_signal(self, signal: StrategySignal, quote: Quote | None, *, remember: bool = True) -> int:
        if not self.enabled:
            return 0
        entry_price = float(quote.price) if quote and quote.price else 0.0
        if entry_price <= 0:
            return 0
        rows = []
        for factor in signal.metadata.get("factor_breakdown", []) or []:
            if not isinstance(factor, dict) or not factor.get("name"):
                continue
            rows.append(
                (
                    signal.signal_id,
                    signal.symbol.upper(),
                    signal.strategy_name,
                    str(factor.get("name")),
                    float(factor.get("value") or 0.0),
                    float(factor.get("contribution") or 0.0),
                    entry_price,
                    signal.created_at.isoformat(),
                    json.dumps({"factor": factor, "score": signal.score, "action": signal.action}, ensure_ascii=False, default=str),
                )
            )
        if not rows:
            return 0
        with sqlite3.connect(self.path) as conn:
            conn.executemany(
                """
                INSERT INTO factor_attribution (
                    signal_id, symbol, strategy_name, factor_name, factor_value,
                    contribution, entry_price, observed_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id, factor_name) DO UPDATE SET
                    factor_value=excluded.factor_value,
                    contribution=excluded.contribution,
                    entry_price=excluded.entry_price,
                    observed_at=excluded.observed_at,
                    metadata_json=excluded.metadata_json
                """,
                rows,
            )
        if remember and self.memory:
            self.memory.add(
                "factor_attribution",
                f"{signal.symbol} recorded {len(rows)} factor contributions for {signal.strategy_name}",
                symbol=signal.symbol,
                strategy=signal.strategy_name,
                importance=0.66,
                confidence=0.75,
                source="factor_attribution",
                metadata={"signal_id": signal.signal_id, "factor_count": len(rows), "entry_price": entry_price},
            )
        return len(rows)

    def update_forward_returns(self, quotes: dict[str, Quote | None], *, horizon: str = "1d", remember: bool = True) -> int:
        if horizon not in {"1d", "3d", "5d"}:
            raise ValueError(f"unsupported horizon: {horizon}")
        column = f"forward_{horizon}_return"
        updates: list[tuple[float, int]] = []
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT id, symbol, entry_price FROM factor_attribution WHERE {column} IS NULL AND entry_price > 0"
            ).fetchall()
            for row in rows:
                quote = quotes.get(str(row["symbol"]).upper())
                if not quote or quote.price <= 0:
                    continue
                forward_return = ((float(quote.price) / float(row["entry_price"])) - 1.0) * 100.0
                updates.append((forward_return, int(row["id"])))
            conn.executemany(f"UPDATE factor_attribution SET {column}=? WHERE id=?", updates)
        if remember and updates and self.memory:
            self.memory.add(
                "factor_attribution",
                f"updated {len(updates)} factor forward returns horizon={horizon}",
                strategy="strategy_lab",
                importance=0.64,
                confidence=0.72,
                source="factor_attribution",
                metadata={"horizon": horizon, "updated": len(updates)},
            )
        return len(updates)

    def summary(self, *, horizon: str = "1d", min_observations: int = 1) -> list[FactorAttributionSummary]:
        if horizon not in {"1d", "3d", "5d"}:
            raise ValueError(f"unsupported horizon: {horizon}")
        column = f"forward_{horizon}_return"
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT factor_name,
                       COUNT(*) AS observation_count,
                       AVG(contribution) AS avg_contribution,
                       AVG({column}) AS avg_forward_return,
                       AVG(CASE
                           WHEN contribution > 0 THEN {column}
                           WHEN contribution < 0 THEN -{column}
                           ELSE 0
                       END) AS directional_score
                FROM factor_attribution
                WHERE {column} IS NOT NULL
                GROUP BY factor_name
                HAVING COUNT(*) >= ?
                ORDER BY directional_score DESC
                """,
                (max(1, int(min_observations or 1)),),
            ).fetchall()
        return [
            FactorAttributionSummary(
                factor_name=str(row["factor_name"]),
                observation_count=int(row["observation_count"] or 0),
                avg_contribution=round(float(row["avg_contribution"] or 0.0), 6),
                avg_forward_return=round(float(row["avg_forward_return"] or 0.0), 6),
                directional_score=round(float(row["directional_score"] or 0.0), 6),
            )
            for row in rows
        ]

    def symbols_with_open_attribution(self, *, horizon: str = "1d") -> list[str]:
        column = f"forward_{horizon}_return"
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(f"SELECT DISTINCT symbol FROM factor_attribution WHERE {column} IS NULL").fetchall()
        return sorted({str(row[0]).upper() for row in rows if row[0]})
