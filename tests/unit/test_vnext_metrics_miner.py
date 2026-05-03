from __future__ import annotations

from datetime import date

import polars as pl

from robin.metrics.factor_metrics import apply_bh_qvalues, evaluate_factor_frame
from robin.miner.candidates import mine_factor_candidates


def test_factor_metrics_compute_rank_ic_and_qvalues():
    frame = fixture_factor_frame()
    summary = evaluate_factor_frame(frame, "alpha")
    adjusted = apply_bh_qvalues([summary])

    assert summary.observation_count == 6
    assert summary.rank_ic > 0
    assert summary.quantile_spread > 0
    assert adjusted[0].q_value <= 1.0


def test_candidate_miner_accepts_good_and_rejects_bad_factor():
    decisions = mine_factor_candidates(
        fixture_factor_frame(),
        ["alpha", "noise"],
        min_observations=3,
        min_abs_rank_ic=0.2,
        max_q_value=1.0,
    )
    by_name = {decision.factor_name: decision for decision in decisions}

    assert by_name["alpha"].accepted is True
    assert by_name["noise"].accepted is False
    assert by_name["noise"].rejection_reason


def fixture_factor_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [date(2026, 5, 1)] * 3 + [date(2026, 5, 2)] * 3,
            "entity_id": ["A", "B", "C", "A", "B", "C"],
            "alpha": [1.0, 2.0, 3.0, 1.0, 2.0, 3.0],
            "noise": [1.0, 1.0, 1.0, 2.0, 2.0, 2.0],
            "forward_return": [0.01, 0.02, 0.03, 0.01, 0.02, 0.03],
        }
    )
