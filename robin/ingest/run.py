from __future__ import annotations

from pathlib import Path

from robin.core.config import VNextConfig
from robin.ingest.fetchers.fixture import FixtureFetcher
from robin.ingest.lake import DataLake
from robin.ingest.sources.registry import SourceConfig, SourceRegistry, load_source_registry


def fetcher_for(source: SourceConfig) -> FixtureFetcher:
    if source.source_type != "fixture":
        return FixtureFetcher()
    return FixtureFetcher()


def run_ingest(config: VNextConfig, registry: SourceRegistry) -> dict[str, object]:
    lake = DataLake(config)
    written = []
    failures = []
    for source in registry.enabled_sources():
        docs, source_failures = fetcher_for(source).fetch(source)
        failures.extend(source_failures)
        written.extend(lake.write_raw_documents(docs))
    return {
        "written": [str(path) for path in written],
        "written_count": len(written),
        "failures": [failure.__dict__ for failure in failures],
    }


def run_ingest_from_registry_path(config: VNextConfig, registry_path: Path) -> dict[str, object]:
    return run_ingest(config, load_source_registry(registry_path))
