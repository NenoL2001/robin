from __future__ import annotations

from pathlib import Path

from robin.core.config import load_vnext_config
from robin.facts.entity_resolution.security_master import SecurityMaster
from robin.facts.pipeline import build_evidence_packets
from robin.ingest.lake import DataLake
from robin.ingest.run import run_ingest_from_registry_path


FIXTURES = Path("tests/fixtures/vnext")


def test_raw_document_to_evidence_packet_pipeline(tmp_path: Path) -> None:
    config = load_vnext_config(root=tmp_path)
    lake = DataLake(config)

    run_ingest_from_registry_path(config, FIXTURES / "source_registry.yaml")
    packets = build_evidence_packets(lake.read_raw_documents(), SecurityMaster.from_csv(FIXTURES / "security_master.csv"))
    paths = lake.write_evidence_packets(packets)

    assert len(paths) == 4
    assert len(lake.read_evidence_packets()) == 4
    assert any(packet.source_tier == "P0" for packet in packets)
