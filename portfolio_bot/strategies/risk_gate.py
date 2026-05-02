from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import StrategyRiskConfig
from ..models import NewsItem, RiskGateVerdict, StrategySignal
from ..market.metrics import news_relevance, symbol_matches_text
from ..market.strategy_news_scout import is_official_evidence


@dataclass(slots=True)
class RiskGateContext:
    portfolio_equity: float
    paper_drawdown: float
    real_exposure: float = 0.0
    paper_exposure: float = 0.0
    asset_type: str = "equity"
    latest_backtest: Any | None = None
    evidence: list[NewsItem] | None = None
    price: float | None = None
    intraday_change_percent: float | None = None


class StrategyRiskGate:
    """Deterministic paper-only gate for strategy order proposals."""

    def __init__(self, config: StrategyRiskConfig):
        self.config = config

    def evaluate(self, signal: StrategySignal, context: RiskGateContext) -> RiskGateVerdict:
        blocked: list[str] = []
        warnings: list[str] = []
        reasons: list[str] = []
        asset_type = context.asset_type.lower()
        equity = max(0.0, float(context.portfolio_equity or 0.0))
        paper_drawdown = float(context.paper_drawdown or 0.0)
        evidence = list(context.evidence or [])

        if signal.action != "paper_buy":
            blocked.append("not_paper_buy")
            reasons.append("risk gate only evaluates paper_buy proposals")
        if signal.score < self.config.min_signal_score:
            blocked.append("weak_score")
            reasons.append(f"score {signal.score:.1f} below {self.config.min_signal_score:.1f}")
        if signal.confidence < self.config.min_signal_confidence:
            blocked.append("weak_confidence")
            reasons.append(f"confidence {signal.confidence:.2f} below {self.config.min_signal_confidence:.2f}")

        qualified_evidence = qualified_order_evidence(evidence, signal.symbol)
        if len(qualified_evidence) < self.config.min_evidence_count:
            blocked.append("missing_evidence")
            reasons.append("not enough fresh cited evidence matched to symbol")
        if signal.metadata.get("requires_official_source") and not any(is_official_evidence(item) for item in qualified_evidence):
            blocked.append("missing_official_evidence")
            reasons.append("earnings or guidance-driven order requires official source evidence")

        backtest = context.latest_backtest
        if backtest is None:
            blocked.append("missing_backtest")
            reasons.append("latest backtest is missing")
        else:
            trade_count = int(getattr(backtest, "trade_count", 0) or 0)
            max_drawdown = float(getattr(backtest, "max_drawdown", 0.0) or 0.0)
            if trade_count < self.config.min_backtest_trades:
                blocked.append("weak_backtest_trades")
                reasons.append(f"backtest trades {trade_count} below {self.config.min_backtest_trades}")
            if max_drawdown < self.config.max_backtest_drawdown:
                blocked.append("weak_backtest_drawdown")
                reasons.append(f"backtest drawdown {max_drawdown:.2%} below limit {self.config.max_backtest_drawdown:.2%}")

        size_multiplier = 1.0
        if paper_drawdown <= self.config.paper_drawdown_stop:
            blocked.append("paper_drawdown_stop")
            reasons.append(f"paper drawdown {paper_drawdown:.2%} is at or beyond stop")
        elif paper_drawdown <= self.config.paper_drawdown_warn:
            warnings.append("paper_drawdown_warn")
            reasons.append(f"paper drawdown {paper_drawdown:.2%} triggers reduced sizing")
            size_multiplier = min(size_multiplier, 0.5)
        if context.intraday_change_percent is not None and abs(float(context.intraday_change_percent)) >= 15:
            warnings.append("earnings_volatility_warn")
            reasons.append(f"intraday move {float(context.intraday_change_percent):+.2f}% triggers reduced sizing")
            size_multiplier = min(size_multiplier, 0.5)

        order_pct = self.config.max_option_order_equity_pct if asset_type == "option" else self.config.max_paper_order_equity_pct
        max_notional = equity * max(0.0, order_pct) * size_multiplier
        total_exposure = max(0.0, float(context.real_exposure or 0.0)) + max(0.0, float(context.paper_exposure or 0.0))
        exposure_limit = symbol_exposure_limit(equity, asset_type)
        if equity <= 0:
            blocked.append("missing_equity")
            reasons.append("portfolio equity is missing")
        elif total_exposure >= exposure_limit:
            blocked.append("portfolio_exposure_cap")
            reasons.append(f"symbol exposure ${total_exposure:.2f} exceeds cap ${exposure_limit:.2f}")

        if max_notional <= 0:
            blocked.append("zero_proposed_size")
            reasons.append("proposed notional is zero")

        severity = "block" if blocked else "warn" if warnings else "pass"
        return RiskGateVerdict(
            signal_id=signal.signal_id,
            allowed=not blocked,
            severity=severity,  # type: ignore[arg-type]
            reasons=reasons or ["risk gate passed"],
            size_multiplier=size_multiplier if not blocked else 0.0,
            max_notional=max_notional if not blocked else 0.0,
            blocked_checks=blocked,
            warning_checks=warnings,
            metadata={
                "symbol": signal.symbol,
                "strategy_name": signal.strategy_name,
                "asset_type": asset_type,
                "qualified_evidence_count": len(qualified_evidence),
                "evidence_count": len(evidence),
                "paper_drawdown": paper_drawdown,
                "real_exposure": context.real_exposure,
                "paper_exposure": context.paper_exposure,
                "latest_backtest": asdict(backtest) if hasattr(backtest, "__dataclass_fields__") else {},
            },
        )


def qualified_order_evidence(items: list[NewsItem], symbol: str, *, now: datetime | None = None) -> list[NewsItem]:
    now = now or datetime.now(timezone.utc)
    qualified: list[NewsItem] = []
    for item in items:
        if not item.url:
            continue
        if not evidence_matches_symbol(item, symbol):
            continue
        if item.kind == "web_evidence":
            if float(item.raw.get("confidence") or 0.0) < 0.5:
                continue
            if not item.published_at or not is_fresh(item.published_at, now, timedelta(days=7)):
                continue
        elif item.published_at and not is_fresh(item.published_at, now, timedelta(days=14)):
            continue
        elif not item.published_at:
            continue
        if news_relevance(item, symbol) < 0.45:
            continue
        qualified.append(item)
    return qualified


def evidence_matches_symbol(item: NewsItem, symbol: str) -> bool:
    symbol = symbol.upper()
    if symbol in {value.upper() for value in item.symbols if value}:
        return True
    return symbol_matches_text(symbol, f"{item.title} {item.summary}")


def is_fresh(value: datetime, now: datetime, window: timedelta) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return now - value <= window


def symbol_exposure_limit(equity: float, asset_type: str) -> float:
    pct = 0.03 if asset_type.lower() == "option" else 0.10
    return max(0.0, equity * pct)
