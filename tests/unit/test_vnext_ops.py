from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl

from robin.contracts.op import OpRunContext
from robin.core.ids import stable_hash
from robin.features.ops import default_op_registry, execute_op


def test_all_default_ops_emit_contract_columns_and_cache(tmp_path):
    registry = default_op_registry()
    ctx = ctx_for("2026-05-03", params={"n": 2, "value_col": "value"})
    inputs = fixture_inputs()
    cache = tmp_path / "cache"

    for spec in registry.list_specs():
        op = registry.get(spec.name)
        frame, metadata = execute_op(
            op, ctx, cache_dir=cache, **inputs_for_op(spec.name, inputs)
        )
        cached, cached_metadata = execute_op(
            op, ctx, cache_dir=cache, **inputs_for_op(spec.name, inputs)
        )

        assert {"date", "entity_id", "op_version", "snapshot_id", "asof_ts"} <= set(
            frame.columns
        )
        assert frame.height > 0
        assert metadata.cache_key == cached_metadata.cache_key
        assert cached.equals(frame)


def test_ops_filter_future_rows_for_point_in_time_safety():
    registry = default_op_registry()
    frame, _ = execute_op(
        registry.get("ma_gap_n"),
        ctx_for("2026-05-02", params={"n": 2}),
        bars=fixture_inputs()["bars"],
    )

    assert frame.get_column("date").max() <= date(2026, 5, 2)


def fixture_inputs():
    bars = pl.DataFrame(
        {
            "date": [
                date(2026, 5, 1),
                date(2026, 5, 2),
                date(2026, 5, 3),
                date(2026, 5, 1),
                date(2026, 5, 2),
                date(2026, 5, 3),
            ],
            "symbol": ["AAA", "AAA", "AAA", "BBB", "BBB", "BBB"],
            "open": [10.0, 11.0, 12.0, 20.0, 19.0, 18.0],
            "high": [11.0, 12.0, 13.0, 21.0, 20.0, 19.0],
            "low": [9.0, 10.0, 11.0, 19.0, 18.0, 17.0],
            "close": [10.0, 11.0, 12.0, 20.0, 19.0, 18.0],
            "volume": [100, 120, 180, 200, 190, 160],
        }
    )
    return {
        "bars": bars,
        "features": pl.DataFrame(
            {
                "date": [date(2026, 5, 2), date(2026, 5, 2)],
                "entity_id": ["AAA", "BBB"],
                "value": [1.0, 3.0],
            }
        ),
        "entity_master": pl.DataFrame(
            {"entity_id": ["AAA", "BBB"], "industry": ["semis", "semis"]}
        ),
        "earnings_actual": pl.DataFrame(
            {
                "entity_id": ["AAA"],
                "fiscal_period": ["2026Q1"],
                "announce_date": [date(2026, 5, 2)],
                "actual_eps": [1.2],
            }
        ),
        "consensus": pl.DataFrame(
            {"entity_id": ["AAA"], "fiscal_period": ["2026Q1"], "consensus_eps": [1.0]}
        ),
        "claims": pl.DataFrame(
            {
                "date": [date(2026, 5, 2)],
                "entity_id": ["AAA"],
                "sentiment": [0.8],
                "source_score": [0.95],
                "published_ts": [datetime(2026, 5, 2, 12, tzinfo=timezone.utc)],
            }
        ),
        "events": pl.DataFrame(
            {
                "date": [date(2026, 5, 2)],
                "entity_id": ["AAA"],
                "event_type": ["major_contract"],
                "novelty": [0.8],
                "confidence": [0.9],
            }
        ),
        "filing_embeddings": pl.DataFrame(
            {
                "date": [date(2026, 5, 1), date(2026, 5, 2)],
                "entity_id": ["AAA", "AAA"],
                "embedding": [[1.0, 0.0], [0.0, 1.0]],
            }
        ),
        "event_cluster": pl.DataFrame(
            {"date": [date(2026, 5, 2)], "entity_id": ["AAA"], "peer_ids": [["BBB"]]}
        ),
        "peer_returns": pl.DataFrame(
            {
                "date": [date(2026, 5, 2)],
                "entity_id": ["BBB"],
                "abnormal_return": [0.04],
            }
        ),
    }


def inputs_for_op(name: str, data):
    if name in {"ma_gap_n", "volume_zscore_n"}:
        return {"bars": data["bars"]}
    if name == "industry_neutralization":
        return {"features": data["features"], "entity_master": data["entity_master"]}
    if name == "earnings_surprise":
        return {
            "earnings_actual": data["earnings_actual"],
            "consensus": data["consensus"],
        }
    if name == "news_sentiment_score":
        return {"claims": data["claims"]}
    if name == "event_shock_label":
        return {"events": data["events"]}
    if name == "filing_novelty_score":
        return {"filing_embeddings": data["filing_embeddings"]}
    if name == "peer_diffusion_score":
        return {
            "event_cluster": data["event_cluster"],
            "peer_returns": data["peer_returns"],
        }
    raise AssertionError(name)


def ctx_for(as_of: str, params=None):
    return OpRunContext(
        run_id="test",
        snapshot_id=stable_hash({"as_of": as_of}),
        partition_key=as_of,
        asof_ts=f"{as_of}T23:59:59+00:00",
        params=params or {},
    )
