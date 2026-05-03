from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from robin.core.clock import utc_now
from robin.core.ids import stable_hash


def append_lineage_event(path: Path, *, artifact_id: str, inputs: list[str], code_hash: str, config_hash: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    event = {
        "artifact_id": artifact_id,
        "inputs": inputs,
        "code_hash": code_hash,
        "config_hash": config_hash,
        "run_id": stable_hash({"artifact": artifact_id, "inputs": inputs, "created_at": utc_now().isoformat()})[:24],
        "created_at": utc_now().isoformat(),
        "metadata": metadata or {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return event
