from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from robin.contracts.entity import CanonicalEntity, EntityMention
from robin.core.ids import stable_id
from robin.core.types import AssetType


class SecurityMasterRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    security_id: str
    symbol: str
    name: str = ""
    asset_type: AssetType = AssetType.EQUITY
    exchange: str = ""
    aliases: tuple[str, ...] = ()


class SecurityMaster:
    def __init__(self, records: list[SecurityMasterRecord]):
        self.records = records

    @classmethod
    def from_csv(cls, path: Path) -> "SecurityMaster":
        rows: list[SecurityMasterRecord] = []
        with path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                aliases = tuple(part.strip() for part in str(row.get("aliases", "")).split("|") if part.strip())
                rows.append(
                    SecurityMasterRecord(
                        security_id=row.get("security_id") or row["symbol"].upper(),
                        symbol=row["symbol"].upper(),
                        name=row.get("name", ""),
                        asset_type=AssetType(row.get("asset_type") or "equity"),
                        exchange=row.get("exchange", ""),
                        aliases=aliases,
                    )
                )
        return cls(rows)

    def resolve(self, mention: EntityMention) -> CanonicalEntity | None:
        text = mention.text.upper().strip()
        for record in self.records:
            candidates = {record.symbol.upper(), record.name.upper(), *(alias.upper() for alias in record.aliases)}
            if text in candidates or any(text == alias for alias in candidates if alias):
                return CanonicalEntity(
                    id=stable_id("entity", {"mention": mention.id, "security": record.security_id}),
                    mention_id=mention.id,
                    security_id=record.security_id,
                    symbol=record.symbol,
                    name=record.name,
                    asset_type=record.asset_type,
                    exchange=record.exchange,
                    confidence=max(0.6, mention.confidence),
                    lineage=[mention.id],
                )
        return None
