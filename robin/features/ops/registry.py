from __future__ import annotations

import hashlib
import inspect
import json
from datetime import date, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import polars as pl

from robin.contracts.op import OpExecutionMetadata, OpRunContext, OpSpec
from robin.core.ids import stable_hash


class FeatureOp(Protocol):
    spec: OpSpec

    def compute(self, ctx: OpRunContext, **inputs: pl.DataFrame) -> pl.DataFrame: ...


@dataclass(slots=True)
class OpRegistry:
    ops: dict[str, FeatureOp]

    def register(self, op: FeatureOp) -> None:
        if op.spec.name in self.ops:
            raise ValueError(f"duplicate op registration: {op.spec.name}")
        self.ops[op.spec.name] = op

    def get(self, name: str) -> FeatureOp:
        try:
            return self.ops[name]
        except KeyError as exc:
            raise KeyError(f"unknown feature op: {name}") from exc

    def list_specs(self) -> list[OpSpec]:
        return [self.ops[name].spec for name in sorted(self.ops)]


def execute_op(
    op: FeatureOp,
    ctx: OpRunContext,
    *,
    cache_dir: Path | None = None,
    **inputs: pl.DataFrame,
) -> tuple[pl.DataFrame, OpExecutionMetadata]:
    cache_key = op_cache_key(op.spec, ctx, inputs)
    if cache_dir:
        path = cache_dir / f"{cache_key}.parquet"
        if path.exists():
            frame = pl.read_parquet(path)
            return frame, metadata_for(op.spec, ctx, frame, cache_key)
    frame = enforce_output_contract(op.compute(ctx, **inputs), op.spec, ctx)
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(cache_dir / f"{cache_key}.parquet")
    return frame, metadata_for(op.spec, ctx, frame, cache_key)


def enforce_output_contract(
    frame: pl.DataFrame, spec: OpSpec, ctx: OpRunContext
) -> pl.DataFrame:
    required = {"date", "entity_id"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"op {spec.name} output missing columns: {sorted(missing)}")
    output = frame
    asof_date = parse_asof_date(ctx.asof_ts)
    if asof_date is not None:
        output = output.filter(pl.col("date") <= asof_date)
    output = output.with_columns(
        [
            pl.lit(spec.version).alias("op_version"),
            pl.lit(ctx.snapshot_id).alias("snapshot_id"),
        ]
    )
    if "asof_ts" not in output.columns:
        output = output.with_columns(pl.lit(ctx.asof_ts).alias("asof_ts"))
    return output


def parse_asof_date(value: str) -> date | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def metadata_for(
    spec: OpSpec, ctx: OpRunContext, frame: pl.DataFrame, cache_key: str
) -> OpExecutionMetadata:
    return OpExecutionMetadata(
        op_name=spec.name,
        op_version=spec.version,
        row_count=frame.height,
        output_table=spec.output_table,
        snapshot_id=ctx.snapshot_id,
        cache_key=cache_key,
        upstream_tables=spec.input_tables,
        upstream_op_versions=spec.depends_on,
    )


def op_cache_key(
    spec: OpSpec, ctx: OpRunContext, inputs: dict[str, pl.DataFrame]
) -> str:
    fingerprints = {
        name: frame_fingerprint(frame) for name, frame in sorted(inputs.items())
    }
    return stable_hash(
        {
            "op": spec.name,
            "version": spec.version,
            "params": ctx.params,
            "snapshot_id": ctx.snapshot_id,
            "partition": ctx.partition_key,
            "inputs": fingerprints,
        }
    )


def frame_fingerprint(frame: pl.DataFrame) -> str:
    payload = {
        "columns": frame.columns,
        "height": frame.height,
        "sample": frame.head(5).to_dicts(),
        "tail": frame.tail(5).to_dicts(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def code_hash(obj: object) -> str:
    target: Any = obj
    if not (
        inspect.ismodule(obj)
        or inspect.isclass(obj)
        or inspect.ismethod(obj)
        or inspect.isfunction(obj)
        or inspect.istraceback(obj)
        or inspect.isframe(obj)
        or inspect.iscode(obj)
    ):
        target = obj.__class__
    try:
        source = inspect.getsource(target)
    except (OSError, TypeError):
        source = repr(obj)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
