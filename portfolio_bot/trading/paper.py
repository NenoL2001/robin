from __future__ import annotations

import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..memory import MemoryStore


@dataclass(slots=True)
class PaperOrder:
    order_id: str
    side: str
    symbol: str
    asset_type: str
    quantity: float
    price: float
    gross_value: float
    commission: float
    strategy_name: str
    strategy_version: str
    signal_id: str
    reason: str
    memory_context: str
    multiplier: float
    created_at: str


@dataclass(slots=True)
class PaperPosition:
    symbol: str
    asset_type: str
    quantity: float
    avg_price: float
    multiplier: float
    strategy_name: str
    strategy_version: str
    market_price: float | None = None

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_price * self.multiplier

    @property
    def market_value(self) -> float:
        price = self.avg_price if self.market_price is None else self.market_price
        return self.quantity * price * self.multiplier

    @property
    def unrealized_pnl(self) -> float:
        return self.market_value - self.cost_basis


class PaperBroker:
    def __init__(self, path: Path, starting_cash: float = 100000.0, memory: MemoryStore | None = None):
        self.path = path
        self.starting_cash = starting_cash
        self.memory = memory
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._ensure_cash()

    def buy(
        self,
        *,
        symbol: str,
        asset_type: str,
        quantity: float,
        price: float,
        strategy_name: str,
        strategy_version: str,
        signal_id: str,
        reason: str,
        memory_context: str = "",
        multiplier: float | None = None,
        commission: float = 0.0,
    ) -> PaperOrder:
        self._validate_order(quantity, price, strategy_name, signal_id)
        symbol = symbol.upper()
        asset_type = asset_type.lower()
        multiplier = multiplier_for(asset_type, multiplier)
        gross = quantity * price * multiplier
        total = gross + commission
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cash = self._cash_conn(conn)
            if total > cash + 1e-9:
                raise ValueError(f"模拟账户现金不足: need={total:.2f}, cash={cash:.2f}")
            order = self._insert_order_conn(
                conn,
                "buy",
                symbol,
                asset_type,
                quantity,
                price,
                gross,
                commission,
                strategy_name,
                strategy_version,
                signal_id,
                reason,
                memory_context,
                multiplier,
            )
            self._set_cash_conn(conn, cash - total)
            self._add_position_conn(conn, symbol, asset_type, quantity, price, strategy_name, strategy_version, multiplier)
        self._remember_order(order)
        return order

    def sell(
        self,
        *,
        symbol: str,
        asset_type: str,
        quantity: float,
        price: float,
        strategy_name: str,
        strategy_version: str,
        signal_id: str,
        reason: str,
        memory_context: str = "",
        multiplier: float | None = None,
        commission: float = 0.0,
    ) -> PaperOrder:
        self._validate_order(quantity, price, strategy_name, signal_id)
        symbol = symbol.upper()
        asset_type = asset_type.lower()
        multiplier = multiplier_for(asset_type, multiplier)
        gross = quantity * price * multiplier
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            position = self._get_position_conn(conn, symbol)
            if not position or quantity > position.quantity + 1e-9:
                have = 0.0 if not position else position.quantity
                raise ValueError(f"模拟持仓不足: symbol={symbol}, sell={quantity:g}, have={have:g}")
            order = self._insert_order_conn(
                conn,
                "sell",
                symbol,
                asset_type,
                quantity,
                price,
                gross,
                commission,
                strategy_name,
                strategy_version,
                signal_id,
                reason,
                memory_context,
                multiplier,
            )
            self._set_cash_conn(conn, self._cash_conn(conn) + gross - commission)
            self._reduce_position_conn(conn, symbol, quantity)
        self._remember_order(order)
        return order

    def cash(self) -> float:
        with self._connect() as conn:
            row = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
        return float(row["cash"]) if row else self.starting_cash

    def positions(self) -> list[PaperPosition]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM positions WHERE ABS(quantity) > 1e-9 ORDER BY symbol").fetchall()
        return [position_from_row(row) for row in rows]

    def orders(self, limit: int = 20) -> list[PaperOrder]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [order_from_row(row) for row in rows]

    def get_position(self, symbol: str) -> PaperPosition | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM positions WHERE symbol = ?", (symbol.upper(),)).fetchone()
        return position_from_row(row) if row else None

    def snapshot(self, prices: dict[str, float] | None = None) -> dict[str, Any]:
        prices = prices or {}
        positions = self.positions()
        market_value = 0.0
        rows = []
        for pos in positions:
            pos.market_price = prices.get(pos.symbol)
            market_value += pos.market_value
            rows.append(position_to_dict(pos))
        cash = self.cash()
        equity = cash + market_value
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO equity_curve(created_at, cash, market_value, equity) VALUES (?, ?, ?, ?)",
                (now, cash, market_value, equity),
            )
        if self.memory:
            self.memory.add(
                "paper_position",
                f"模拟组合快照: cash={cash:.2f}, market_value={market_value:.2f}, equity={equity:.2f}",
                importance=0.55,
                confidence=0.8,
                source="paper",
                metadata={"positions": rows, "cash": cash, "market_value": market_value, "equity": equity},
            )
            self.memory.add(
                "paper_pnl",
                f"模拟组合 PnL: equity={equity:.2f}, starting_cash={self.starting_cash:.2f}, pnl={equity - self.starting_cash:.2f}",
                importance=0.6,
                confidence=0.8,
                source="paper",
                metadata={"cash": cash, "market_value": market_value, "equity": equity, "pnl": equity - self.starting_cash},
            )
        return {"cash": cash, "market_value": market_value, "equity": equity, "positions": rows}

    def equity_curve(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM equity_curve ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def review(self) -> str:
        snap = self.snapshot()
        positions = self.positions()
        orders = self.orders(limit=10)
        lines = [
            "模拟组合复盘",
            f"- 现金: ${snap['cash']:.2f}",
            f"- 持仓市值: ${snap['market_value']:.2f}",
            f"- 模拟净值: ${snap['equity']:.2f}",
            f"- 当前持仓数: {len(positions)}",
            f"- 最近订单数: {len(orders)}",
        ]
        for pos in positions[:12]:
            lines.append(f"- {pos.symbol}: {pos.quantity:g} {pos.asset_type}, 成本 ${pos.cost_basis:.2f}, 策略 {pos.strategy_name}")
        for order in orders[:8]:
            lines.append(f"- 订单 {order.side}: {order.symbol} {order.quantity:g} @ {order.price:g}, 策略 {order.strategy_name}, 理由: {order.reason}")
        review = "\n".join(lines)
        if self.memory:
            self.memory.add("daily_review", review, strategy="paper_portfolio", importance=0.75, confidence=0.8, source="paper")
        return review

    def _validate_order(self, quantity: float, price: float, strategy_name: str, signal_id: str) -> None:
        if quantity <= 0:
            raise ValueError("模拟订单数量必须大于 0")
        if price <= 0:
            raise ValueError("模拟订单价格必须大于 0")
        if not strategy_name:
            raise ValueError("模拟订单必须绑定 strategy_name")
        if not signal_id:
            raise ValueError("模拟订单必须绑定 signal_id")

    def _insert_order(
        self,
        side: str,
        symbol: str,
        asset_type: str,
        quantity: float,
        price: float,
        gross: float,
        commission: float,
        strategy_name: str,
        strategy_version: str,
        signal_id: str,
        reason: str,
        memory_context: str,
        multiplier: float,
    ) -> PaperOrder:
        with self._connect() as conn:
            return self._insert_order_conn(
                conn,
                side,
                symbol,
                asset_type,
                quantity,
                price,
                gross,
                commission,
                strategy_name,
                strategy_version,
                signal_id,
                reason,
                memory_context,
                multiplier,
            )

    def _insert_order_conn(
        self,
        conn: sqlite3.Connection,
        side: str,
        symbol: str,
        asset_type: str,
        quantity: float,
        price: float,
        gross: float,
        commission: float,
        strategy_name: str,
        strategy_version: str,
        signal_id: str,
        reason: str,
        memory_context: str,
        multiplier: float,
    ) -> PaperOrder:
        order_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO orders
            (order_id, side, symbol, asset_type, quantity, price, gross_value, commission,
             strategy_name, strategy_version, signal_id, reason, memory_context, multiplier, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                side,
                symbol,
                asset_type,
                quantity,
                price,
                gross,
                commission,
                strategy_name,
                strategy_version,
                signal_id,
                reason,
                memory_context,
                multiplier,
                now,
            ),
        )
        return PaperOrder(
            order_id,
            side,
            symbol,
            asset_type,
            quantity,
            price,
            gross,
            commission,
            strategy_name,
            strategy_version,
            signal_id,
            reason,
            memory_context,
            multiplier,
            now,
        )

    def _add_position(self, symbol: str, asset_type: str, quantity: float, price: float, strategy_name: str, strategy_version: str, multiplier: float) -> None:
        with self._connect() as conn:
            self._add_position_conn(conn, symbol, asset_type, quantity, price, strategy_name, strategy_version, multiplier)

    def _add_position_conn(self, conn: sqlite3.Connection, symbol: str, asset_type: str, quantity: float, price: float, strategy_name: str, strategy_version: str, multiplier: float) -> None:
        old = self._get_position_conn(conn, symbol)
        if old:
            new_qty = old.quantity + quantity
            new_avg = ((old.avg_price * old.quantity) + (price * quantity)) / new_qty
            conn.execute(
                "UPDATE positions SET quantity = ?, avg_price = ?, updated_at = ? WHERE symbol = ?",
                (new_qty, new_avg, datetime.now(timezone.utc).isoformat(), symbol),
            )
        else:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO positions
                (symbol, asset_type, quantity, avg_price, multiplier, strategy_name, strategy_version, opened_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (symbol, asset_type, quantity, price, multiplier, strategy_name, strategy_version, now, now),
            )

    def _reduce_position(self, symbol: str, quantity: float) -> None:
        with self._connect() as conn:
            self._reduce_position_conn(conn, symbol, quantity)

    def _reduce_position_conn(self, conn: sqlite3.Connection, symbol: str, quantity: float) -> None:
        old = self._get_position_conn(conn, symbol)
        if not old:
            return
        new_qty = old.quantity - quantity
        if new_qty <= 1e-9:
            conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
        else:
            conn.execute("UPDATE positions SET quantity = ?, updated_at = ? WHERE symbol = ?", (new_qty, datetime.now(timezone.utc).isoformat(), symbol))

    def _set_cash(self, cash: float) -> None:
        with self._connect() as conn:
            self._set_cash_conn(conn, cash)

    def _set_cash_conn(self, conn: sqlite3.Connection, cash: float) -> None:
        conn.execute("UPDATE account SET cash = ?, updated_at = ? WHERE id = 1", (cash, datetime.now(timezone.utc).isoformat()))

    def _cash_conn(self, conn: sqlite3.Connection) -> float:
        row = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
        return float(row["cash"]) if row else self.starting_cash

    def _get_position_conn(self, conn: sqlite3.Connection, symbol: str) -> PaperPosition | None:
        row = conn.execute("SELECT * FROM positions WHERE symbol = ?", (symbol.upper(),)).fetchone()
        return position_from_row(row) if row else None

    def _ensure_cash(self) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM account WHERE id = 1").fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO account(id, cash, starting_cash, updated_at) VALUES (1, ?, ?, ?)",
                    (self.starting_cash, self.starting_cash, datetime.now(timezone.utc).isoformat()),
                )

    def _remember_order(self, order: PaperOrder) -> None:
        if not self.memory:
            return
        self.memory.add(
            "paper_order",
            f"模拟{('买入' if order.side == 'buy' else '卖出')} {order.symbol} {order.quantity:g} @ {order.price:g}, 策略 {order.strategy_name}, 理由: {order.reason}",
            symbol=order.symbol,
            strategy=order.strategy_name,
            importance=0.8,
            confidence=0.8,
            source="paper",
            metadata=asdict(order),
        )

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
                CREATE TABLE IF NOT EXISTS account (
                  id INTEGER PRIMARY KEY CHECK (id = 1),
                  cash REAL NOT NULL,
                  starting_cash REAL NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                  symbol TEXT PRIMARY KEY,
                  asset_type TEXT NOT NULL,
                  quantity REAL NOT NULL,
                  avg_price REAL NOT NULL,
                  multiplier REAL NOT NULL,
                  strategy_name TEXT NOT NULL,
                  strategy_version TEXT NOT NULL,
                  opened_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                  order_id TEXT PRIMARY KEY,
                  side TEXT NOT NULL,
                  symbol TEXT NOT NULL,
                  asset_type TEXT NOT NULL,
                  quantity REAL NOT NULL,
                  price REAL NOT NULL,
                  gross_value REAL NOT NULL,
                  commission REAL NOT NULL,
                  strategy_name TEXT NOT NULL,
                  strategy_version TEXT NOT NULL,
                  signal_id TEXT NOT NULL,
                  reason TEXT NOT NULL,
                  memory_context TEXT NOT NULL,
                  multiplier REAL NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS equity_curve (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_at TEXT NOT NULL,
                  cash REAL NOT NULL,
                  market_value REAL NOT NULL,
                  equity REAL NOT NULL
                )
                """
            )


def multiplier_for(asset_type: str, multiplier: float | None = None) -> float:
    if multiplier is not None:
        return float(multiplier)
    return 100.0 if asset_type.lower() == "option" else 1.0


def order_from_row(row: sqlite3.Row) -> PaperOrder:
    return PaperOrder(
        order_id=str(row["order_id"]),
        side=str(row["side"]),
        symbol=str(row["symbol"]),
        asset_type=str(row["asset_type"]),
        quantity=float(row["quantity"]),
        price=float(row["price"]),
        gross_value=float(row["gross_value"]),
        commission=float(row["commission"]),
        strategy_name=str(row["strategy_name"]),
        strategy_version=str(row["strategy_version"]),
        signal_id=str(row["signal_id"]),
        reason=str(row["reason"]),
        memory_context=str(row["memory_context"]),
        multiplier=float(row["multiplier"]),
        created_at=str(row["created_at"]),
    )


def position_from_row(row: sqlite3.Row) -> PaperPosition:
    return PaperPosition(
        symbol=str(row["symbol"]),
        asset_type=str(row["asset_type"]),
        quantity=float(row["quantity"]),
        avg_price=float(row["avg_price"]),
        multiplier=float(row["multiplier"]),
        strategy_name=str(row["strategy_name"]),
        strategy_version=str(row["strategy_version"]),
    )


def position_to_dict(position: PaperPosition) -> dict[str, Any]:
    return {
        "symbol": position.symbol,
        "asset_type": position.asset_type,
        "quantity": position.quantity,
        "avg_price": position.avg_price,
        "multiplier": position.multiplier,
        "strategy_name": position.strategy_name,
        "strategy_version": position.strategy_version,
        "cost_basis": position.cost_basis,
        "market_value": position.market_value,
        "unrealized_pnl": position.unrealized_pnl,
    }
