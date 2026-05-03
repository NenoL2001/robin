from __future__ import annotations

from datetime import date

from pydantic import Field

from robin.contracts.base import ContractModel


class FactorDefinition(ContractModel):
    name: str
    owner: str = "UNSPECIFIED"
    status: str = "candidate"
    formula: str
    inputs: list[str] = Field(default_factory=list)
    lookback_days: int = 1
    neutralization: list[str] = Field(default_factory=list)
    leakage_policy: str = "no_future_data"
    config_hash: str


class FactorValueDaily(ContractModel):
    factor_id: str
    factor_name: str
    symbol: str
    as_of_date: date
    value: float
    rank: float | None = None
    sector_neutral_value: float | None = None
    market_cap_neutral_value: float | None = None
    input_ids: list[str] = Field(default_factory=list)
    snapshot_hash: str
