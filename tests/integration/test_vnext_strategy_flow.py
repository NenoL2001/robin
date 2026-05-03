from __future__ import annotations

from datetime import date
from pathlib import Path

from robin.core.config import load_vnext_config
from robin.facts.entity_resolution.security_master import SecurityMaster
from robin.facts.pipeline import build_evidence_packets
from robin.features.daily_compute import compute_factor_values_daily, write_factor_values
from robin.ingest.lake import DataLake
from robin.ingest.run import run_ingest_from_registry_path
from robin.market.loaders.ohlcv import load_ohlcv_csv
from robin.strategy.risk.policy import risk_check
from robin.strategy.selector.champion_challenger import select_decision
from robin.core.types import DecisionAction


FIXTURES = Path("tests/fixtures/vnext")


def test_evidence_packet_factor_snapshot_to_strategy_decision(tmp_path: Path) -> None:
    config = load_vnext_config(root=tmp_path)
    lake = DataLake(config)
    run_ingest_from_registry_path(config, FIXTURES / "source_registry.yaml")
    packets = build_evidence_packets(lake.read_raw_documents(), SecurityMaster.from_csv(FIXTURES / "security_master.csv"))
    values = compute_factor_values_daily(load_ohlcv_csv(FIXTURES / "ohlcv.csv"), date(2026, 5, 1))

    lake.write_evidence_packets(packets)
    write_factor_values(values, config.lake_root / config.lake.gold_path / "factor_values")
    decision = risk_check(select_decision("fixture", "SNDK", packets, values), max_notional=0.0)

    assert decision.action == DecisionAction.BLOCK
    assert decision.evidence_packet_ids
    assert decision.factor_value_ids
