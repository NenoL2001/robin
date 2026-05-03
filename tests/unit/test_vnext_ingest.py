from __future__ import annotations

from pathlib import Path

from robin.contracts.raw_document import RawDocument
from robin.core.config import load_vnext_config
from robin.core.types import SourceTier
from robin.ingest.dedup.url_hash import document_dedupe_key
from robin.ingest.lake import DataLake
from robin.ingest.sources.registry import load_source_registry


FIXTURES = Path("tests/fixtures/vnext")


def test_url_hash_dedup_is_stable() -> None:
    first = document_dedupe_key("https://example.com/a?utm_source=x", "Title", "Body")
    second = document_dedupe_key("https://example.com/a", "Title", "Body")

    assert first == second


def test_source_registry_config_validation() -> None:
    registry = load_source_registry(FIXTURES / "source_registry.yaml")

    assert len(registry.enabled_sources()) == 1
    assert registry.by_id("sandisk_ir_fixture") is not None


def test_bronze_writer_dedupes_within_append_batch(tmp_path: Path) -> None:
    lake = DataLake(load_vnext_config(root=tmp_path))
    document = RawDocument(source_id="fixture", source_tier=SourceTier.P1, url="https://example.com/a", title="SNDK", body="SNDK revenue")
    duplicate = RawDocument(source_id="fixture", source_tier=SourceTier.P1, url="https://example.com/a", title="SNDK", body="SNDK revenue")

    paths = lake.write_raw_documents([document, duplicate])

    assert len(paths) == 1
    assert len(lake.read_raw_documents()) == 1
