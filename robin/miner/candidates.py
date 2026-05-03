from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import polars as pl

from robin.metrics.factor_metrics import (
    FactorMetricSummary,
    apply_bh_qvalues,
    evaluate_factor_frame,
)


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    factor_name: str
    accepted: bool
    rejection_reason: str
    metrics: FactorMetricSummary

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["metrics"] = self.metrics.to_dict()
        return payload


def mine_factor_candidates(
    frame: pl.DataFrame,
    candidate_factors: list[str],
    *,
    forward_col: str = "forward_return",
    min_observations: int = 20,
    min_abs_rank_ic: float = 0.03,
    max_q_value: float = 0.2,
    max_turnover: float = 1.0,
) -> list[CandidateDecision]:
    summaries = apply_bh_qvalues(
        [
            evaluate_factor_frame(frame, name, forward_col=forward_col)
            for name in candidate_factors
        ]
    )
    decisions: list[CandidateDecision] = []
    for summary in summaries:
        reason = ""
        if summary.observation_count < min_observations:
            reason = "insufficient_observations"
        elif abs(summary.rank_ic) < min_abs_rank_ic:
            reason = "weak_rank_ic"
        elif summary.q_value > max_q_value:
            reason = "failed_fdr_control"
        elif summary.turnover > max_turnover:
            reason = "turnover_too_high"
        elif not summary.monotonic:
            reason = "non_monotonic_quantiles"
        decisions.append(
            CandidateDecision(summary.factor_name, not reason, reason, summary)
        )
    return decisions


def write_candidate_outputs(
    decisions: list[CandidateDecision], output_dir: Path
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    accepted = [decision.to_dict() for decision in decisions if decision.accepted]
    rejected = [decision.to_dict() for decision in decisions if not decision.accepted]
    accepted_path = output_dir / "accepted_candidates.parquet"
    rejected_path = output_dir / "rejected_candidates.parquet"
    pl.DataFrame(
        accepted or [{"factor_name": "", "accepted": True, "rejection_reason": ""}]
    ).write_parquet(accepted_path)
    pl.DataFrame(
        rejected or [{"factor_name": "", "accepted": False, "rejection_reason": ""}]
    ).write_parquet(rejected_path)
    return accepted_path, rejected_path
