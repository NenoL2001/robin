from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...memory import MemoryRecord, MemoryStore
from ...runtime import RuntimeStore
from ...strategies.registry import load_strategy_infos
from .schemas import MemoryBundle


class MemorySynthesizer:
    def __init__(self, root: Path, runtime: RuntimeStore, memory: MemoryStore):
        self.root = root
        self.runtime = runtime
        self.memory = memory

    def synthesize(
        self,
        query: str,
        *,
        symbols: list[str] | None = None,
        strategy: str = "",
        run_id: int | None = None,
        limit: int = 8,
    ) -> MemoryBundle:
        symbols = [symbol.upper() for symbol in (symbols or []) if symbol]
        factual = self._records(["real_position_snapshot", "market_event", "bar_snapshot", "news_lead", "daily_news_summary", "web_evidence", "ranked_evidence", "strategy_evidence", "event_observation", "factor_spec", "factor_observation", "factor_weight_update", "factor_attribution", "symbol_relationship", "signal", "strategy_signal", "risk_gate_verdict", "paper_order_proposal", "paper_order_job"], query, symbols, limit)
        episodic = self._agent_runs(limit=limit)
        procedural = self._procedural(strategy=strategy, limit=limit)
        reflective = self._records(["daily_review", "strategy_lesson", "strategy_roundtable", "source_config_change", "agent_reflection", "missed_evidence_lesson", "missed_signal_review", "report_verification", "research_gap"], query, symbols, limit)
        warnings = stale_warnings(factual + reflective)
        bundle = MemoryBundle(query=query, factual=factual, episodic=episodic, procedural=procedural, reflective=reflective, stale_warnings=warnings)
        if run_id is not None:
            for layer, rows in (("factual", factual), ("episodic", episodic), ("procedural", procedural), ("reflective", reflective)):
                for row in rows[:limit]:
                    self.runtime.add_agent_memory_link(run_id, row.get("id"), layer, row.get("content", "")[:500], float(row.get("confidence", 0.5) or 0.5), row)
        return bundle

    def _records(self, kinds: list[str], query: str, symbols: list[str], limit: int) -> list[dict[str, Any]]:
        rows: list[MemoryRecord] = []
        seen: set[int] = set()
        for symbol in symbols or [""]:
            for record in self.memory.search(query, symbol=symbol, limit=limit):
                if record.kind in kinds and record.id not in seen:
                    rows.append(record)
                    seen.add(record.id)
        for kind in kinds:
            for record in self.memory.recent(kind=kind, limit=limit):
                if record.id not in seen:
                    rows.append(record)
                    seen.add(record.id)
        rows = sorted(rows, key=lambda item: (item.importance, item.created_at), reverse=True)[:limit]
        return [record_to_memory_row(row) for row in rows]

    def _agent_runs(self, limit: int) -> list[dict[str, Any]]:
        rows = []
        for row in self.runtime.recent_agent_runs(limit=limit):
            rows.append(
                {
                    "id": row.get("id"),
                    "content": f"{row.get('agent_name')} {row.get('status')}: {row.get('objective')}",
                    "confidence": 0.7 if row.get("status") == "done" else 0.45,
                    "source": "agent_runs",
                    "created_at": row.get("created_at", ""),
                    "metadata": {
                        "agent_name": row.get("agent_name"),
                        "role": row.get("role"),
                        "status": row.get("status"),
                        "created_at": row.get("created_at"),
                        "updated_at": row.get("updated_at"),
                    },
                }
            )
        return rows

    def _procedural(self, strategy: str, limit: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        skill_path = self.root / "system_skills" / "code_iteration" / "SKILL.md"
        if skill_path.exists():
            rows.append({"id": None, "content": skill_path.read_text(encoding="utf-8")[:1800], "confidence": 0.9, "source": str(skill_path), "metadata": {"kind": "system_skill"}})
        for info in load_strategy_infos(self.root / "strategy_skills")[:limit]:
            if strategy and info.name != strategy:
                continue
            rows.append({"id": None, "content": f"strategy {info.name} v{info.version} status={info.status}: {info.description}", "confidence": 0.85, "source": info.path, "metadata": asdict(info)})
        return rows[:limit]


def record_to_memory_row(record: MemoryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "kind": record.kind,
        "symbol": record.symbol,
        "strategy": record.strategy,
        "content": record.content,
        "importance": record.importance,
        "confidence": record.confidence,
        "source": record.source,
        "metadata": record.metadata or {},
        "created_at": record.created_at,
    }


def stale_warnings(rows: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    now = datetime.now(timezone.utc)
    for row in rows:
        meta = row.get("metadata") or {}
        expires_at = meta.get("expires_at")
        if expires_at:
            try:
                expiry = datetime.fromisoformat(str(expires_at))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry < now:
                    warnings.append(f"memory expired: {row.get('kind', row.get('source'))} {row.get('content', '')[:80]}")
            except ValueError:
                warnings.append(f"memory has invalid expires_at: {expires_at}")
        if float(row.get("confidence", 0.5) or 0.5) < 0.4:
            warnings.append(f"low-confidence memory: {row.get('content', '')[:80]}")
    return warnings[:12]
