from __future__ import annotations

from datetime import date
from pathlib import Path

from robin.core.config import load_vnext_config
from robin.core.ids import stable_hash
from robin.facts.entity_resolution.security_master import SecurityMaster
from robin.facts.pipeline import build_evidence_packets
from robin.features.daily_compute import compute_factor_values_daily
from robin.ingest.lake import DataLake
from robin.ingest.run import run_ingest_from_registry_path
from robin.market.loaders.ohlcv import load_ohlcv_csv


FIXTURES = Path("tests/fixtures/vnext")


def _replay_hash(root: Path) -> str:
    config = load_vnext_config(root=root)
    lake = DataLake(config)
    run_ingest_from_registry_path(config, FIXTURES / "source_registry.yaml")
    packets = build_evidence_packets(lake.read_raw_documents(), SecurityMaster.from_csv(FIXTURES / "security_master.csv"))
    values = compute_factor_values_daily(load_ohlcv_csv(FIXTURES / "ohlcv.csv"), date(2026, 5, 1))
    return stable_hash({"packets": [(packet.id, packet.verification_status.value, packet.summary) for packet in packets], "factors": [(value.id, value.snapshot_hash) for value in values]})


def test_replay_fixture_is_deterministic(tmp_path: Path) -> None:
    assert _replay_hash(tmp_path / "a") == _replay_hash(tmp_path / "b")


def test_late_news_keeps_three_clocks(tmp_path: Path) -> None:
    config = load_vnext_config(root=tmp_path)
    lake = DataLake(config)
    run_ingest_from_registry_path(config, FIXTURES / "source_registry.yaml")
    document = lake.read_raw_documents()[0]

    assert document.event_time is not None
    assert document.published_time is not None
    assert document.ingested_time is not None
    assert document.event_time <= document.published_time <= document.ingested_time
