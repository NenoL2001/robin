from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import polars as pl

from robin.contracts.op import OpRunContext, OpSpec
from robin.core.ids import stable_id
from robin.features.ops.registry import OpRegistry, code_hash


def default_op_registry() -> OpRegistry:
    registry = OpRegistry(ops={})
    for op in (
        MaGapOp(),
        VolumeZScoreOp(),
        IndustryNeutralizationOp(),
        EarningsSurpriseOp(),
        NewsSentimentScoreOp(),
        EventShockLabelOp(),
        FilingNoveltyScoreOp(),
        PeerDiffusionScoreOp(),
    ):
        registry.register(op)
    return registry


@dataclass(slots=True)
class MaGapOp:
    name: str = "ma_gap_n"
    spec: OpSpec = field(init=False)

    def __post_init__(self) -> None:
        self.spec = make_spec(
            self.name,
            "gold.features.price",
            ["market_bar"],
            "Moving-average gap over a trailing window.",
            lookback=20,
        )

    def compute(self, ctx: OpRunContext, **inputs: pl.DataFrame) -> pl.DataFrame:
        bars = normalize_entity(inputs["bars"])
        n = int(ctx.params.get("n", 20))
        ma_col = f"ma_{n}"
        gap_col = f"ma_gap_{n}"
        return (
            bars.sort(["entity_id", "date"])
            .with_columns(
                pl.col("close")
                .rolling_mean(window_size=n, min_samples=1)
                .over("entity_id")
                .alias(ma_col)
            )
            .with_columns(
                ((pl.col("close") / pl.col(ma_col)) - 1.0).fill_null(0.0).alias(gap_col)
            )
            .select(["date", "entity_id", ma_col, gap_col])
        )


@dataclass(slots=True)
class VolumeZScoreOp:
    name: str = "volume_zscore_n"
    spec: OpSpec = field(init=False)

    def __post_init__(self) -> None:
        self.spec = make_spec(
            self.name,
            "gold.features.price",
            ["market_bar"],
            "Volume z-score over a trailing window.",
            lookback=20,
        )

    def compute(self, ctx: OpRunContext, **inputs: pl.DataFrame) -> pl.DataFrame:
        bars = normalize_entity(inputs["bars"])
        n = int(ctx.params.get("n", 20))
        mean_col = f"volume_mean_{n}"
        std_col = f"volume_std_{n}"
        z_col = f"volume_zscore_{n}"
        return (
            bars.sort(["entity_id", "date"])
            .with_columns(
                [
                    pl.col("volume")
                    .rolling_mean(window_size=n, min_samples=1)
                    .over("entity_id")
                    .alias(mean_col),
                    pl.col("volume")
                    .rolling_std(window_size=n, min_samples=2)
                    .over("entity_id")
                    .alias(std_col),
                ]
            )
            .with_columns(
                ((pl.col("volume") - pl.col(mean_col)) / pl.col(std_col))
                .fill_nan(0.0)
                .fill_null(0.0)
                .alias(z_col)
            )
            .select(["date", "entity_id", z_col])
        )


@dataclass(slots=True)
class IndustryNeutralizationOp:
    name: str = "industry_neutralization"
    spec: OpSpec = field(init=False)

    def __post_init__(self) -> None:
        self.spec = make_spec(
            self.name,
            "gold.features.neutralized",
            ["factor_frame", "entity_master"],
            "Demean a feature by industry bucket.",
            lookback=1,
        )

    def compute(self, ctx: OpRunContext, **inputs: pl.DataFrame) -> pl.DataFrame:
        feature = normalize_entity(inputs["features"])
        entity = normalize_entity(inputs["entity_master"]).select(
            ["entity_id", "industry"]
        )
        value_col = str(ctx.params.get("value_col", "value"))
        return (
            feature.join(entity, on="entity_id", how="left")
            .with_columns(
                (pl.col(value_col) - pl.col(value_col).mean().over("industry"))
                .fill_null(0.0)
                .alias(f"{value_col}_industry_neutral")
            )
            .select(
                [
                    "date",
                    "entity_id",
                    "industry",
                    value_col,
                    f"{value_col}_industry_neutral",
                ]
            )
        )


@dataclass(slots=True)
class EarningsSurpriseOp:
    name: str = "earnings_surprise"
    spec: OpSpec = field(init=False)

    def __post_init__(self) -> None:
        self.spec = make_spec(
            self.name,
            "gold.features.events",
            ["earnings_actual", "earnings_consensus"],
            "EPS surprise versus point-in-time consensus.",
            lookback=1,
        )

    def compute(self, ctx: OpRunContext, **inputs: pl.DataFrame) -> pl.DataFrame:
        actual = normalize_entity(inputs["earnings_actual"])
        consensus = normalize_entity(inputs["consensus"])
        return (
            actual.join(consensus, on=["entity_id", "fiscal_period"], how="left")
            .with_columns(
                (
                    (pl.col("actual_eps") - pl.col("consensus_eps"))
                    / pl.when(pl.col("consensus_eps").abs() < 1e-6)
                    .then(None)
                    .otherwise(pl.col("consensus_eps").abs())
                )
                .fill_nan(0.0)
                .fill_null(0.0)
                .alias("eps_surprise")
            )
            .rename({"announce_date": "date"})
            .select(["date", "entity_id", "eps_surprise"])
        )


@dataclass(slots=True)
class NewsSentimentScoreOp:
    name: str = "news_sentiment_score"
    spec: OpSpec = field(init=False)

    def __post_init__(self) -> None:
        self.spec = make_spec(
            self.name,
            "gold.features.news",
            ["verified_claim"],
            "Confidence-weighted decayed news sentiment.",
            lookback=2,
        )

    def compute(self, ctx: OpRunContext, **inputs: pl.DataFrame) -> pl.DataFrame:
        claims = normalize_entity(inputs["claims"])
        half_life_hours = float(ctx.params.get("half_life_hours", 18))
        asof = parse_asof(ctx.asof_ts)
        return (
            claims.with_columns(
                [
                    (
                        (
                            pl.lit(asof.timestamp())
                            - pl.col("published_ts").dt.timestamp("ms") / 1000.0
                        )
                        / 3600.0
                    )
                    .clip(0.0)
                    .alias("_age_hours"),
                ]
            )
            .with_columns(
                (0.5 ** (pl.col("_age_hours") / half_life_hours)).alias("_decay")
            )
            .with_columns(
                [
                    (
                        pl.col("sentiment") * pl.col("source_score") * pl.col("_decay")
                    ).alias("_w_score"),
                    (pl.col("source_score") * pl.col("_decay")).alias("_w"),
                ]
            )
            .group_by(["date", "entity_id"])
            .agg(
                [
                    (pl.col("_w_score").sum() / pl.col("_w").sum())
                    .fill_nan(0.0)
                    .fill_null(0.0)
                    .alias("news_sent_1d"),
                    pl.col("source_score").mean().alias("avg_source_score"),
                    pl.len().alias("claim_cnt"),
                ]
            )
        )


@dataclass(slots=True)
class EventShockLabelOp:
    name: str = "event_shock_label"
    spec: OpSpec = field(init=False)

    def __post_init__(self) -> None:
        self.spec = make_spec(
            self.name,
            "gold.labels.events",
            ["verified_event"],
            "High-impact event shock label.",
            lookback=3,
            unit="label",
        )

    def compute(self, ctx: OpRunContext, **inputs: pl.DataFrame) -> pl.DataFrame:
        events = normalize_entity(inputs["events"])
        high_impact = ctx.params.get(
            "high_impact_events",
            ["earnings_guidance_up", "major_contract", "subsidy_win"],
        )
        novelty_threshold = float(ctx.params.get("novelty_threshold", 0.65))
        confidence_threshold = float(ctx.params.get("confidence_threshold", 0.75))
        return events.with_columns(
            (
                pl.col("event_type").is_in(high_impact)
                & (pl.col("novelty") > novelty_threshold)
                & (pl.col("confidence") > confidence_threshold)
            )
            .cast(pl.Int8)
            .alias("event_shock_3d")
        ).select(["date", "entity_id", "event_type", "event_shock_3d", "confidence"])


@dataclass(slots=True)
class FilingNoveltyScoreOp:
    name: str = "filing_novelty_score"
    spec: OpSpec = field(init=False)

    def __post_init__(self) -> None:
        self.spec = make_spec(
            self.name,
            "gold.features.filings",
            ["filing_embedding"],
            "Filing novelty from max prior cosine similarity.",
            lookback=8,
        )

    def compute(self, ctx: OpRunContext, **inputs: pl.DataFrame) -> pl.DataFrame:
        rows = (
            normalize_entity(inputs["filing_embeddings"])
            .sort(["entity_id", "date"])
            .to_dicts()
        )
        output: list[dict[str, Any]] = []
        history: dict[str, list[list[float]]] = {}
        for row in rows:
            entity_id = str(row["entity_id"])
            embedding = [float(value) for value in row["embedding"]]
            sims = [
                cosine_similarity(embedding, prior)
                for prior in history.get(entity_id, [])
            ]
            novelty = 1.0 - max(sims, default=0.0)
            output.append(
                {"date": row["date"], "entity_id": entity_id, "filing_novelty": novelty}
            )
            history.setdefault(entity_id, []).append(embedding)
        return pl.DataFrame(output)


@dataclass(slots=True)
class PeerDiffusionScoreOp:
    name: str = "peer_diffusion_score"
    spec: OpSpec = field(init=False)

    def __post_init__(self) -> None:
        self.spec = make_spec(
            self.name,
            "gold.features.peer",
            ["event_cluster", "peer_return"],
            "Peer abnormal return diffusion around event clusters.",
            lookback=3,
        )

    def compute(self, ctx: OpRunContext, **inputs: pl.DataFrame) -> pl.DataFrame:
        events = normalize_entity(inputs["event_cluster"])
        peer_returns = inputs["peer_returns"]
        return (
            events.explode("peer_ids")
            .rename({"peer_ids": "peer_id"})
            .join(
                peer_returns,
                left_on=["date", "peer_id"],
                right_on=["date", "entity_id"],
                how="left",
            )
            .group_by(["date", "entity_id"])
            .agg(
                pl.col("abnormal_return")
                .mean()
                .fill_null(0.0)
                .alias("peer_diffusion_1d")
            )
        )


def make_spec(
    name: str,
    output_table: str,
    input_tables: list[str],
    description: str,
    *,
    lookback: int,
    unit: str = "ratio",
) -> OpSpec:
    return OpSpec(
        id=stable_id("op", {"name": name, "version": "1.0.0"}),
        name=name,
        version="1.0.0",
        frequency="1d",
        deterministic=True,
        stateful=False,
        input_tables=input_tables,
        output_table=output_table,
        owner="robin.vnext.ops",
        description=description,
        lookback_days=lookback,
        feature_tags=[name.split("_", 1)[0]],
        unit=unit,
        code_hash=code_hash(name),
    )


def normalize_entity(frame: pl.DataFrame) -> pl.DataFrame:
    if "entity_id" in frame.columns:
        return frame
    if "symbol" in frame.columns:
        return frame.with_columns(
            pl.col("symbol").str.to_uppercase().alias("entity_id")
        )
    return frame


def parse_asof(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return numerator / (left_norm * right_norm)
