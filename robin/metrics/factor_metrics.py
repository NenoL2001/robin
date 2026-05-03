from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import polars as pl


@dataclass(frozen=True, slots=True)
class FactorMetricSummary:
    factor_name: str
    observation_count: int
    rank_ic: float
    icir: float
    quantile_spread: float
    turnover: float
    t_stat: float
    p_value: float
    q_value: float
    monotonic: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_factor_frame(
    frame: pl.DataFrame,
    factor_name: str,
    *,
    forward_col: str = "forward_return",
    quantiles: int = 5,
) -> FactorMetricSummary:
    required = {"date", "entity_id", factor_name, forward_col}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"factor frame missing columns: {sorted(missing)}")
    clean = frame.drop_nulls([factor_name, forward_col])
    if clean.is_empty():
        return empty_summary(factor_name)
    daily_ic = []
    spreads = []
    turnovers = []
    previous_top: set[str] | None = None
    for _, group in clean.group_by("date", maintain_order=True):
        if group.height < 2:
            continue
        ic = spearman_rank_corr(
            group.get_column(factor_name).to_list(),
            group.get_column(forward_col).to_list(),
        )
        daily_ic.append(ic)
        ranked = group.with_columns(
            pl.col(factor_name).rank(method="average").alias("_rank")
        )
        bucketed = ranked.with_columns(
            ((pl.col("_rank") - 1) / max(1, ranked.height) * quantiles)
            .floor()
            .clip(0, quantiles - 1)
            .alias("_bucket")
        )
        returns = (
            bucketed.group_by("_bucket")
            .agg(pl.col(forward_col).mean().alias("_ret"))
            .sort("_bucket")
        )
        if returns.height >= 2:
            spreads.append(
                float(
                    returns.tail(1).get_column("_ret")[0]
                    - returns.head(1).get_column("_ret")[0]
                )
            )
        top = set(
            bucketed.filter(pl.col("_bucket") == quantiles - 1)
            .get_column("entity_id")
            .to_list()
        )
        if previous_top is not None:
            overlap = len(top & previous_top) / max(1, len(top | previous_top))
            turnovers.append(1.0 - overlap)
        previous_top = top
    rank_ic = mean(daily_ic)
    ic_std = std(daily_ic)
    icir = rank_ic / ic_std if ic_std > 1e-12 else 0.0
    t = t_statistic(daily_ic)
    p = normal_two_sided_pvalue(t)
    return FactorMetricSummary(
        factor_name=factor_name,
        observation_count=clean.height,
        rank_ic=round(rank_ic, 6),
        icir=round(icir, 6),
        quantile_spread=round(mean(spreads), 6),
        turnover=round(mean(turnovers), 6),
        t_stat=round(t, 6),
        p_value=round(p, 6),
        q_value=round(p, 6),
        monotonic=mean(spreads) >= 0 and rank_ic >= 0,
    )


def apply_bh_qvalues(summaries: list[FactorMetricSummary]) -> list[FactorMetricSummary]:
    if not summaries:
        return []
    ordered = sorted(enumerate(summaries), key=lambda item: item[1].p_value)
    m = len(summaries)
    qvalues = [1.0] * m
    running = 1.0
    for rank, (idx, summary) in reversed(list(enumerate(ordered, start=1))):
        running = min(running, summary.p_value * m / max(1, rank))
        qvalues[idx] = min(1.0, running)
    return [
        FactorMetricSummary(
            factor_name=summary.factor_name,
            observation_count=summary.observation_count,
            rank_ic=summary.rank_ic,
            icir=summary.icir,
            quantile_spread=summary.quantile_spread,
            turnover=summary.turnover,
            t_stat=summary.t_stat,
            p_value=summary.p_value,
            q_value=round(qvalues[idx], 6),
            monotonic=summary.monotonic,
        )
        for idx, summary in enumerate(summaries)
    ]


def spearman_rank_corr(x_values: list[float], y_values: list[float]) -> float:
    if len(x_values) != len(y_values) or len(x_values) < 2:
        return 0.0
    return pearson_corr(ranks(x_values), ranks(y_values))


def ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(values)
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        avg = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            result[ordered[k][0]] = avg
        i = j + 1
    return result


def pearson_corr(x_values: list[float], y_values: list[float]) -> float:
    x_mean = mean(x_values)
    y_mean = mean(y_values)
    cov = sum(
        (x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values, strict=False)
    )
    x_var = sum((x - x_mean) ** 2 for x in x_values)
    y_var = sum((y - y_mean) ** 2 for y in y_values)
    denom = (x_var * y_var) ** 0.5
    return cov / denom if denom > 1e-12 else 0.0


def t_statistic(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    sigma = std(values)
    return mean(values) / (sigma / (len(values) ** 0.5)) if sigma > 1e-12 else 0.0


def normal_two_sided_pvalue(t_value: float) -> float:
    return min(1.0, math.erfc(abs(t_value) / math.sqrt(2.0)))


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = mean(values)
    return (sum((value - mu) ** 2 for value in values) / (len(values) - 1)) ** 0.5


def empty_summary(factor_name: str) -> FactorMetricSummary:
    return FactorMetricSummary(factor_name, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, False)
