from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import BotConfig
from ..memory import MemoryStore, memory_path
from .exposures import LEVERAGED_EXPOSURES
from .strategy_news_scout import SymbolRelationship


@dataclass(slots=True)
class StoredSymbolRelation:
    source_symbol: str
    related_symbol: str
    relation_type: str
    multiplier: float = 1.0
    confidence: float = 0.0
    evidence_url: str = ""
    evidence_title: str = ""
    source: str = ""
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["observed_at"] = self.observed_at.isoformat()
        return payload


class RelationGraph:
    def __init__(self, path: Path, *, memory: MemoryStore | None = None, enabled: bool = True):
        self.path = path
        self.memory = memory
        self.enabled = enabled
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._ensure()

    @classmethod
    def from_config(cls, config: BotConfig, *, memory: MemoryStore | None = None) -> "RelationGraph":
        return cls(
            config.data_dir / config.relation_graph.sqlite_path,
            memory=memory or MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled),
            enabled=config.relation_graph.enabled,
        )

    def _ensure(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_relationships (
                    source_symbol TEXT NOT NULL,
                    related_symbol TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    multiplier REAL NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_url TEXT NOT NULL,
                    evidence_title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (source_symbol, related_symbol, relation_type)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol_relationships_related ON symbol_relationships(related_symbol)")

    def upsert(self, relation: StoredSymbolRelation, *, remember: bool = True) -> None:
        if not self.enabled:
            return
        relation.source_symbol = relation.source_symbol.upper().strip()
        relation.related_symbol = relation.related_symbol.upper().strip()
        if not relation.source_symbol or not relation.related_symbol:
            return
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO symbol_relationships (
                    source_symbol, related_symbol, relation_type, multiplier, confidence,
                    evidence_url, evidence_title, source, observed_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_symbol, related_symbol, relation_type) DO UPDATE SET
                    multiplier=excluded.multiplier,
                    confidence=MAX(symbol_relationships.confidence, excluded.confidence),
                    evidence_url=CASE WHEN excluded.confidence >= symbol_relationships.confidence THEN excluded.evidence_url ELSE symbol_relationships.evidence_url END,
                    evidence_title=CASE WHEN excluded.confidence >= symbol_relationships.confidence THEN excluded.evidence_title ELSE symbol_relationships.evidence_title END,
                    source=excluded.source,
                    observed_at=excluded.observed_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    relation.source_symbol,
                    relation.related_symbol,
                    relation.relation_type,
                    float(relation.multiplier or 1.0),
                    float(relation.confidence or 0.0),
                    relation.evidence_url or "",
                    relation.evidence_title or "",
                    relation.source or "",
                    relation.observed_at.isoformat(),
                    json.dumps(relation.metadata or {}, ensure_ascii=False, default=str),
                ),
            )
        if remember and self.memory:
            self.memory.add(
                "symbol_relationship",
                f"{relation.source_symbol}->{relation.related_symbol} {relation.relation_type} multiplier={relation.multiplier:g}: {relation.evidence_title}",
                symbol=relation.source_symbol,
                importance=0.76,
                confidence=relation.confidence,
                source=relation.source or "relation_graph",
                metadata=relation.to_dict(),
                evidence_links=[relation.evidence_url] if relation.evidence_url else [],
            )

    def upsert_many_from_scout(self, relationships: list[SymbolRelationship], *, remember: bool = True) -> list[StoredSymbolRelation]:
        stored = [stored_relation_from_scout(item) for item in relationships]
        for relation in stored:
            self.upsert(relation, remember=remember)
        return stored

    def seed_static(self, *, remember: bool = False) -> list[StoredSymbolRelation]:
        relations = static_symbol_relations()
        for relation in relations:
            self.upsert(relation, remember=remember)
        return relations

    def relationships_for(self, symbols: list[str] | set[str], *, min_confidence: float = 0.0) -> list[StoredSymbolRelation]:
        static = [
            relation
            for relation in static_symbol_relations()
            if relation.confidence >= float(min_confidence or 0.0)
            and (relation.source_symbol in {str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()} or relation.related_symbol in {str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
        ]
        if not self.enabled:
            return static
        normalized = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT * FROM symbol_relationships
                WHERE (source_symbol IN ({placeholders}) OR related_symbol IN ({placeholders}))
                  AND confidence >= ?
                ORDER BY confidence DESC, observed_at DESC
                """,
                [*normalized, *normalized, float(min_confidence or 0.0)],
            ).fetchall()
        by_key = {(relation.source_symbol, relation.related_symbol, relation.relation_type): relation for relation in static}
        for relation in [relation_from_row(row) for row in rows]:
            key = (relation.source_symbol, relation.related_symbol, relation.relation_type)
            current = by_key.get(key)
            if current is None or relation.confidence > current.confidence:
                by_key[key] = relation
        return sorted(by_key.values(), key=lambda item: item.confidence, reverse=True)

    def related_symbols(self, symbols: list[str] | set[str], *, min_confidence: float = 0.55) -> set[str]:
        related: set[str] = set()
        source_set = {str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()}
        for relation in self.relationships_for(source_set, min_confidence=min_confidence):
            if relation.source_symbol in source_set:
                related.add(relation.related_symbol)
            if relation.related_symbol in source_set:
                related.add(relation.source_symbol)
        return related


def stored_relation_from_scout(item: SymbolRelationship) -> StoredSymbolRelation:
    text = f"{item.relation_type} {item.evidence_title} {item.metadata.get('summary', '')}".lower()
    multiplier = 2.0 if any(term in text for term in ("2x", "2 x", "two times", "200%", "daily leveraged")) else 1.0
    relation_type = item.relation_type.replace("_candidate", "")
    if multiplier == 2.0 and "leveraged" in relation_type:
        relation_type = "leveraged_underlying"
    return StoredSymbolRelation(
        source_symbol=item.source_symbol,
        related_symbol=item.related_symbol,
        relation_type=relation_type,
        multiplier=multiplier,
        confidence=item.confidence,
        evidence_url=item.evidence_url,
        evidence_title=item.evidence_title,
        source=str(item.metadata.get("source") or "strategy_news_scout"),
        observed_at=item.created_at,
        metadata={**dict(item.metadata or {}), "query": item.query},
    )


def static_symbol_relations() -> list[StoredSymbolRelation]:
    relations: list[StoredSymbolRelation] = []
    for exposure in LEVERAGED_EXPOSURES.values():
        relations.append(
            StoredSymbolRelation(
                source_symbol=exposure.symbol,
                related_symbol=exposure.underlying,
                relation_type="leveraged_underlying",
                multiplier=exposure.multiplier,
                confidence=0.95,
                evidence_url="",
                evidence_title=f"Static configured exposure: {exposure.english_label}",
                source="static_leveraged_exposure",
                metadata={"direction": exposure.direction},
            )
        )
    return relations


def relation_from_row(row: sqlite3.Row) -> StoredSymbolRelation:
    try:
        observed_at = datetime.fromisoformat(str(row["observed_at"]))
    except ValueError:
        observed_at = datetime.now(timezone.utc)
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return StoredSymbolRelation(
        source_symbol=str(row["source_symbol"]),
        related_symbol=str(row["related_symbol"]),
        relation_type=str(row["relation_type"]),
        multiplier=float(row["multiplier"] or 1.0),
        confidence=float(row["confidence"] or 0.0),
        evidence_url=str(row["evidence_url"] or ""),
        evidence_title=str(row["evidence_title"] or ""),
        source=str(row["source"] or ""),
        observed_at=observed_at,
        metadata=metadata,
    )
