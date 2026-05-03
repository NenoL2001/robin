from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from robin.core.types import SourceTier


class SourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    source_type: str = "fixture"
    tier: SourceTier = SourceTier.UNSPECIFIED
    enabled: bool = True
    path: Path | None = None
    url: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceRegistry(BaseModel):
    model_config = ConfigDict(frozen=True)

    sources: list[SourceConfig] = Field(default_factory=list)

    def enabled_sources(self) -> list[SourceConfig]:
        return [source for source in self.sources if source.enabled]

    def by_id(self, source_id: str) -> SourceConfig | None:
        return next((source for source in self.sources if source.source_id == source_id), None)


def load_source_registry(path: Path) -> SourceRegistry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    items = raw.get("sources", []) if isinstance(raw, dict) else []
    return SourceRegistry(sources=[SourceConfig.model_validate(item) for item in items])
