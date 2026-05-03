from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LakeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: Path = Path("data/vnext")
    bronze_path: str = "bronze"
    silver_path: str = "silver"
    gold_path: str = "gold"
    catalog_path: str = "catalog.duckdb"
    metadata_path: str = "metadata.sqlite"
    artifact_path: str = "artifacts"


class VNextConfig(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    enabled: bool = True
    root: Path = Path(".")
    lake: LakeConfig = Field(default_factory=LakeConfig)
    default_source_tier: str = "P2"
    live_trading_enabled: bool = False
    broker_permission: str = "UNSPECIFIED_BROKER_PERMISSION"
    market_data_source: str = "UNSPECIFIED_MARKET_SOURCE"
    llm_provider: str = "UNSPECIFIED_DATA_SOURCE"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def lake_root(self) -> Path:
        return self.root / self.lake.root


def load_vnext_config(raw: dict[str, Any] | None = None, *, root: Path | None = None) -> VNextConfig:
    raw = dict(raw or {})
    if root is not None:
        raw.setdefault("root", root)
    return VNextConfig.model_validate(raw)
