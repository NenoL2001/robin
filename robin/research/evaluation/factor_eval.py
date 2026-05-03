from __future__ import annotations

import math
from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True, slots=True)
class FactorEval:
    factor_name: str
    rank_ic: float
    ir: float
    hit_rate: float
    turnover: float
    stability: float
    factor_eval_id: str


def evaluate_factor(frame: pl.DataFrame, factor_name: str, forward_return_col: str = "forward_return") -> FactorEval:
    if frame.is_empty() or factor_name not in frame.columns or forward_return_col not in frame.columns:
        return FactorEval(factor_name, 0.0, 0.0, 0.0, 0.0, 0.0, f"factor_eval:{factor_name}:empty")
    by_date = []
    for _, group in frame.group_by("date", maintain_order=True):
        if group.height < 2:
            continue
        corr = group.select(pl.corr(factor_name, forward_return_col, method="spearman")).item()
        by_date.append(float(corr or 0.0))
    avg = sum(by_date) / len(by_date) if by_date else 0.0
    std = math.sqrt(sum((value - avg) ** 2 for value in by_date) / max(1, len(by_date) - 1)) if len(by_date) > 1 else 0.0
    ir = avg / std if std else 0.0
    hit_rate = sum(1 for value in by_date if value > 0) / max(1, len(by_date))
    return FactorEval(factor_name, round(avg, 6), round(ir, 6), round(hit_rate, 6), 0.0, round(1.0 / (1.0 + std), 6), f"factor_eval:{factor_name}:{len(by_date)}")
