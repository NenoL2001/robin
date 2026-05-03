from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import polars as pl

from robin.contracts.base import ContractModel
from robin.contracts.canonical_document import CanonicalDocument
from robin.contracts.evidence_packet import EvidencePacket
from robin.contracts.factor import FactorValueDaily
from robin.contracts.raw_document import RawDocument
from robin.core.clock import partition_date
from robin.core.config import VNextConfig
from robin.ingest.dedup.url_hash import document_dedupe_key

JSON_PARQUET_FIELDS = {
    "attributes",
    "cost_model",
    "gross_metrics",
    "input_ids",
    "lineage",
    "metadata",
    "net_metrics",
    "raw_payload",
    "risk_checks",
}


def parquet_ready_row(row: dict[str, Any]) -> dict[str, Any]:
    """Encode nested contract fields so Polars can write empty dict/list fields."""

    ready = dict(row)
    for key in JSON_PARQUET_FIELDS & ready.keys():
        value = ready[key]
        if isinstance(value, dict | list):
            ready[key] = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return ready


def restore_json_fields(row: dict[str, Any]) -> dict[str, Any]:
    restored = dict(row)
    for key in JSON_PARQUET_FIELDS & restored.keys():
        value = restored[key]
        if isinstance(value, str) and value[:1] in {"{", "["}:
            restored[key] = json.loads(value)
    return restored


class DataLake:
    """Small local lake abstraction for partitioned parquet/json artifacts."""

    def __init__(self, config: VNextConfig):
        self.config = config
        self.root = config.lake_root
        self.root.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        """Create local catalog and metadata files without assuming paid services."""

        for directory in (
            self.root / self.config.lake.bronze_path,
            self.root / self.config.lake.silver_path,
            self.root / self.config.lake.gold_path,
            self.root / self.config.lake.artifact_path,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        catalog = self.root / self.config.lake.catalog_path
        try:
            import duckdb

            with duckdb.connect(str(catalog)) as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS artifacts (artifact_id VARCHAR, kind VARCHAR, path VARCHAR, created_at TIMESTAMP)"
                )
        except Exception as exc:  # pragma: no cover - exercised when duckdb is absent or unavailable.
            (self.root / "catalog.UNSPECIFIED_DUCKDB_UNAVAILABLE.txt").write_text(str(exc), encoding="utf-8")
        metadata = self.root / self.config.lake.metadata_path
        with sqlite3.connect(metadata) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )

    def bronze_raw_path(self, document: RawDocument) -> Path:
        date_part = partition_date(document.published_time or document.ingested_time)
        directory = self.root / self.config.lake.bronze_path / "raw_documents" / f"source={document.source_id}" / f"date={date_part}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{document.id}.parquet"

    def write_raw_documents(self, documents: Iterable[RawDocument]) -> list[Path]:
        paths: list[Path] = []
        seen: set[str] = set()
        for document in documents:
            dedupe_key = document_dedupe_key(document.url, document.title, document.body)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            path = self.bronze_raw_path(document)
            if path.exists():
                paths.append(path)
                continue
            pl.DataFrame([parquet_ready_row(document.to_storage_dict())]).write_parquet(path)
            paths.append(path)
        return paths

    def read_raw_documents(self) -> list[RawDocument]:
        base = self.root / self.config.lake.bronze_path / "raw_documents"
        rows: list[dict[str, Any]] = []
        for path in sorted(base.glob("source=*/date=*/*.parquet")):
            rows.extend(pl.read_parquet(path).to_dicts())
        return [RawDocument.model_validate(restore_json_fields(row)) for row in rows]

    def silver_canonical_path(self, document: CanonicalDocument) -> Path:
        date_part = partition_date(document.published_time or document.ingested_time)
        directory = self.root / self.config.lake.silver_path / "canonical_documents" / f"date={date_part}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{document.id}.parquet"

    def write_canonical_documents(self, documents: Iterable[CanonicalDocument]) -> list[Path]:
        paths: list[Path] = []
        for document in documents:
            path = self.silver_canonical_path(document)
            pl.DataFrame([parquet_ready_row(document.to_storage_dict())]).write_parquet(path)
            paths.append(path)
        return paths

    def gold_evidence_path(self, evidence_id: str, date_value: str) -> Path:
        directory = self.root / self.config.lake.gold_path / "evidence_packets" / f"date={date_value}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{evidence_id}.json"

    def write_evidence_packets(self, packets: Iterable[EvidencePacket]) -> list[Path]:
        paths: list[Path] = []
        for packet in packets:
            date_value = packet.created_at.date().isoformat()
            paths.append(self.write_json_artifact(self.gold_evidence_path(packet.id, date_value), packet))
        return paths

    def read_evidence_packets(self) -> list[EvidencePacket]:
        base = self.root / self.config.lake.gold_path / "evidence_packets"
        packets: list[EvidencePacket] = []
        for path in sorted(base.glob("date=*/*.json")):
            packets.append(EvidencePacket.model_validate(json.loads(path.read_text(encoding="utf-8"))))
        return packets

    def read_factor_values(self) -> list[FactorValueDaily]:
        base = self.root / self.config.lake.gold_path / "factor_values"
        rows: list[dict[str, Any]] = []
        for path in sorted(base.glob("date=*/*.parquet")):
            rows.extend(pl.read_parquet(path).to_dicts())
        return [FactorValueDaily.model_validate(restore_json_fields(row)) for row in rows]

    def write_json_artifact(self, path: Path, payload: ContractModel | dict[str, Any]) -> Path:
        data = payload.to_storage_dict() if isinstance(payload, ContractModel) else payload
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
        return path
