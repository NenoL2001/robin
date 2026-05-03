from __future__ import annotations

from typing import Any

from pydantic import Field

from robin.contracts.base import ContractModel


class OpSpec(ContractModel):
    name: str
    version: str
    frequency: str = "1d"
    deterministic: bool = True
    stateful: bool = False
    depends_on: list[str] = Field(default_factory=list)
    input_tables: list[str] = Field(default_factory=list)
    output_table: str
    owner: str = "robin.vnext"
    description: str = ""
    lookback_days: int = 1
    feature_tags: list[str] = Field(default_factory=list)
    unit: str = "ratio"
    point_in_time_safe: bool = True
    code_hash: str


class OpRunContext(ContractModel):
    run_id: str
    snapshot_id: str
    partition_key: str
    asof_ts: str
    params: dict[str, Any] = Field(default_factory=dict)


class OpExecutionMetadata(ContractModel):
    op_name: str
    op_version: str
    row_count: int
    output_table: str
    snapshot_id: str
    cache_key: str
    upstream_tables: list[str] = Field(default_factory=list)
    upstream_op_versions: list[str] = Field(default_factory=list)
