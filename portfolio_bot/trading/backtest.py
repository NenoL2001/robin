from __future__ import annotations

import csv
import json
import math
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..memory import MemoryStore


@dataclass(slots=True)
class BacktestResult:
    backtest_id: str
    strategy_name: str
    strategy_version: str
    asset_type: str
    total_return: float
    max_drawdown: float
    win_rate: float
    trade_count: int
    average_trade_return: float
    losing_trades: int
    metadata: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


class BacktestStore:
    def __init__(self, path: Path, memory: MemoryStore | None = None):
        self.path = path
        self.memory = memory
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def save(self, result: BacktestResult) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO backtests
                (backtest_id, strategy_name, strategy_version, asset_type, total_return,
                 max_drawdown, win_rate, trade_count, average_trade_return, losing_trades,
                 metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.backtest_id,
                    result.strategy_name,
                    result.strategy_version,
                    result.asset_type,
                    result.total_return,
                    result.max_drawdown,
                    result.win_rate,
                    result.trade_count,
                    result.average_trade_return,
                    result.losing_trades,
                    json.dumps(result.metadata, ensure_ascii=False, default=str),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        if self.memory:
            self.memory.add(
                "backtest_result",
                format_backtest_result(result),
                strategy=result.strategy_name,
                importance=0.8,
                confidence=0.75,
                source="backtest",
                metadata=asdict(result),
            )

    def recent(self, strategy_name: str = "", limit: int = 8) -> list[BacktestResult]:
        with self._connect() as conn:
            if strategy_name:
                rows = conn.execute(
                    "SELECT * FROM backtests WHERE strategy_name = ? ORDER BY created_at DESC LIMIT ?",
                    (strategy_name, limit),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM backtests ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [backtest_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS backtests (
                  backtest_id TEXT PRIMARY KEY,
                  strategy_name TEXT NOT NULL,
                  strategy_version TEXT NOT NULL,
                  asset_type TEXT NOT NULL,
                  total_return REAL NOT NULL,
                  max_drawdown REAL NOT NULL,
                  win_rate REAL NOT NULL,
                  trade_count INTEGER NOT NULL,
                  average_trade_return REAL NOT NULL,
                  losing_trades INTEGER NOT NULL,
                  metadata_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )


def run_equity_backtest(
    prices: list[float],
    *,
    strategy_name: str,
    strategy_version: str = "1.0.0",
    slippage_bps: float = 10.0,
    commission: float = 0.0,
) -> BacktestResult:
    clean = [float(p) for p in prices if float(p) > 0]
    if len(clean) < 3:
        raise ValueError("回测至少需要 3 个有效价格")
    trades: list[float] = []
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    in_position = False
    entry = 0.0
    fee = commission / 10000.0
    slip = slippage_bps / 10000.0
    for idx in range(1, len(clean)):
        prev_price = clean[idx - 1]
        price = clean[idx]
        momentum = price > prev_price
        if momentum and not in_position:
            in_position = True
            entry = price * (1 + slip)
        elif in_position and not momentum:
            exit_price = price * (1 - slip)
            ret = (exit_price - entry) / entry - fee
            trades.append(ret)
            equity *= 1 + ret
            in_position = False
        peak = max(peak, equity)
        max_dd = min(max_dd, (equity - peak) / peak)
    if in_position:
        exit_price = clean[-1] * (1 - slip)
        ret = (exit_price - entry) / entry - fee
        trades.append(ret)
        equity *= 1 + ret
        peak = max(peak, equity)
        max_dd = min(max_dd, (equity - peak) / peak)
    return build_result(strategy_name, strategy_version, "equity", trades, equity - 1, max_dd, {"price_count": len(clean)})


def run_long_call_backtest(
    underlying_prices: list[float],
    *,
    strike: float,
    premium: float,
    strategy_name: str,
    strategy_version: str = "1.0.0",
    contracts: int = 1,
) -> BacktestResult:
    clean = [float(p) for p in underlying_prices if float(p) > 0]
    if len(clean) < 2:
        raise ValueError("长期 call 回测至少需要 2 个底层价格")
    if strike <= 0 or premium <= 0 or contracts <= 0:
        raise ValueError("strike、premium、contracts 必须大于 0")
    initial_cost = premium * 100 * contracts
    terminal_value = max(clean[-1] - strike, 0) * 100 * contracts
    pnl = terminal_value - initial_cost
    total_return = pnl / initial_cost
    path_values = [max(price - strike, 0) * 100 * contracts for price in clean]
    max_value = max(initial_cost, path_values[0])
    max_dd = 0.0
    for value in path_values:
        max_value = max(max_value, value)
        max_dd = min(max_dd, (value - max_value) / max_value if max_value else 0.0)
    trade_return = total_return
    return build_result(
        strategy_name,
        strategy_version,
        "option",
        [trade_return],
        total_return,
        max_dd,
        {
            "strike": strike,
            "premium": premium,
            "contracts": contracts,
            "max_loss": initial_cost,
            "breakeven": strike + premium,
            "terminal_underlying": clean[-1],
            "terminal_value": terminal_value,
        },
    )


def load_prices_csv(path: Path) -> list[float]:
    prices: list[float] = []
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            value = row.get("close") or row.get("price") or row.get("adj_close")
            if value:
                prices.append(float(value))
    return prices


def default_demo_prices() -> list[float]:
    return [20, 20.5, 21, 20.8, 22, 23.5, 22.8, 24, 26, 25.2, 27.5, 30]


def build_result(strategy_name: str, strategy_version: str, asset_type: str, trades: list[float], total_return: float, max_drawdown: float, metadata: dict[str, Any]) -> BacktestResult:
    trade_count = len(trades)
    wins = sum(1 for trade in trades if trade > 0)
    losing = sum(1 for trade in trades if trade < 0)
    avg = sum(trades) / trade_count if trade_count else 0.0
    win_rate = wins / trade_count if trade_count else 0.0
    return BacktestResult(
        backtest_id=str(uuid.uuid4()),
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        asset_type=asset_type,
        total_return=total_return,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        trade_count=trade_count,
        average_trade_return=avg,
        losing_trades=losing,
        metadata=metadata,
    )


def backtest_from_row(row: sqlite3.Row) -> BacktestResult:
    return BacktestResult(
        backtest_id=str(row["backtest_id"]),
        strategy_name=str(row["strategy_name"]),
        strategy_version=str(row["strategy_version"]),
        asset_type=str(row["asset_type"]),
        total_return=float(row["total_return"]),
        max_drawdown=float(row["max_drawdown"]),
        win_rate=float(row["win_rate"]),
        trade_count=int(row["trade_count"]),
        average_trade_return=float(row["average_trade_return"]),
        losing_trades=int(row["losing_trades"]),
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


def format_backtest_result(result: BacktestResult) -> str:
    return (
        f"回测 {result.strategy_name} {result.strategy_version} ({result.asset_type}): "
        f"收益 {result.total_return:.2%}, 最大回撤 {result.max_drawdown:.2%}, "
        f"胜率 {result.win_rate:.2%}, 交易 {result.trade_count}, "
        f"平均单笔 {result.average_trade_return:.2%}, 亏损交易 {result.losing_trades}"
    )
