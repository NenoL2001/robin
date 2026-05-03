from __future__ import annotations

from robin.contracts.backtest import BacktestRun


def promotion_decision(champion: BacktestRun, challenger: BacktestRun, *, min_net_improvement: float = 0.02) -> str:
    champion_return = float(champion.net_metrics.get("total_return", 0.0))
    challenger_return = float(challenger.net_metrics.get("total_return", 0.0))
    if challenger_return - champion_return >= min_net_improvement:
        return "promote_challenger"
    return "keep_champion"
