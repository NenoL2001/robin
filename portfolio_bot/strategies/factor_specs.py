from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..config import BotConfig
from ..memory import MemoryStore, memory_path
from .factor_attribution import FactorAttributionStore


FACTOR_SPEC_DIR = "_factor_specs"
FACTOR_SPEC_FILE = "factors.yaml"


@dataclass(slots=True)
class FactorSpec:
    name: str
    version: str = "1.0.0"
    status: str = "candidate"
    direction: str = "positive"
    weight: float = 0.0
    min_observations_for_orders: int = 20
    evidence_required: bool = True
    description: str = ""
    query_terms: list[str] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FactorIterationResult:
    dry_run: bool
    specs: list[FactorSpec]
    added: list[str]
    updated: list[str]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "specs": [item.to_dict() for item in self.specs],
            "added": self.added,
            "updated": self.updated,
            "summary": self.summary,
        }


DEFAULT_FACTOR_SPECS = [
    FactorSpec(
        "earnings_surprise",
        weight=14.0,
        description="Official earnings beat or above-guidance financial results.",
        query_terms=["earnings beat", "above guidance", "EPS", "revenue"],
    ),
    FactorSpec(
        "guidance_revision",
        weight=12.0,
        description="Forward revenue/EPS/margin guide raised or materially above consensus/prior guide.",
        query_terms=["guidance", "outlook", "Q4 guide", "raises guidance"],
    ),
    FactorSpec(
        "datacenter_mix_shift",
        weight=10.0,
        description="Revenue mix shift toward data center or AI infrastructure customers.",
        query_terms=["datacenter", "data center", "AI infrastructure"],
    ),
    FactorSpec(
        "contracted_revenue_visibility",
        weight=10.0,
        description="Multiyear commitments, financial guarantees, NBM contracts, or prepayments.",
        query_terms=["NBM", "multiyear", "financial guarantee", "prepayment"],
    ),
    FactorSpec(
        "product_roadmap_acceleration",
        weight=8.0,
        description="Sampling, shipping, ramp, or roadmap acceleration for a strategic product.",
        query_terms=["samples", "shipping", "ramp", "roadmap"],
    ),
    FactorSpec(
        "hbf_ai_inference_moat",
        weight=10.0,
        description="High Bandwidth Flash, BiCS8, Stargate, or AI inference memory roadmap evidence.",
        query_terms=["High Bandwidth Flash", "HBF", "BiCS8", "Stargate", "AI inference"],
    ),
    FactorSpec(
        "official_source_strength",
        weight=4.0,
        description="Evidence comes from official IR, company PDF, SEC filing, or primary source.",
        query_terms=["investor relations", "10-Q", "press release", "fact sheet"],
    ),
    FactorSpec(
        "sell_the_news_volatility",
        direction="negative",
        weight=-6.0,
        description="Post-earnings profit-taking, parabolic move, high short/options activity, or liquidity risk.",
        query_terms=["sell-the-news", "profit-taking", "parabolic", "volatility"],
    ),
    FactorSpec(
        "intraday_followthrough",
        weight=0.45,
        evidence_required=False,
        description="Deterministic local intraday behavior score from quote return, gap, and move shape.",
        query_terms=["intraday", "follow-through", "price behavior"],
    ),
    FactorSpec(
        "large_move_reversal_risk",
        direction="negative",
        weight=-1.0,
        evidence_required=False,
        description="Large daily move control that penalizes overextension even when news is strong.",
        query_terms=["overextension", "reversal risk", "large move"],
    ),
    FactorSpec(
        "position_crowding_pressure",
        direction="negative",
        weight=-1.0,
        evidence_required=False,
        description="Existing position size plus large daily move pressure for paper sizing discipline.",
        query_terms=["position sizing", "crowding", "exposure"],
    ),
    FactorSpec(
        "volume_confirmation",
        weight=1.0,
        evidence_required=False,
        description="Quote-level volume availability/confirmation until historical volume normalization is added.",
        query_terms=["volume", "confirmation"],
    ),
    FactorSpec(
        "bar_momentum_5d",
        weight=0.35,
        evidence_required=False,
        description="Local 5-day bar return contribution, capped to avoid chasing single-day spikes.",
        query_terms=["5d return", "trend"],
    ),
    FactorSpec(
        "relative_volume_confirmation",
        weight=1.5,
        evidence_required=False,
        description="Historical bar relative volume confirmation versus recent average.",
        query_terms=["relative volume", "volume breakout"],
    ),
    FactorSpec(
        "close_location_quality",
        weight=2.0,
        evidence_required=False,
        description="Close location value within the daily range as deterministic demand/supply clue.",
        query_terms=["close location", "range position"],
    ),
    FactorSpec(
        "underlying_relation_strength",
        weight=8.0,
        description="Inferred relationship from product/ETF/search evidence connecting a symbol to its economic underlying.",
        query_terms=["underlying", "2x long", "fund holdings", "ETF"],
    ),
]


class FactorSpecStore:
    def __init__(self, strategy_root: Path):
        self.strategy_root = strategy_root
        self.path = strategy_root / FACTOR_SPEC_DIR / FACTOR_SPEC_FILE

    def load(self) -> list[FactorSpec]:
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or []
        except OSError:
            return []
        if not isinstance(raw, list):
            return []
        specs: list[FactorSpec] = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            specs.append(
                FactorSpec(
                    name=str(item.get("name", "")),
                    version=str(item.get("version", "1.0.0")),
                    status=str(item.get("status", "candidate")),
                    direction=str(item.get("direction", "positive")),
                    weight=float(item.get("weight", 0.0)),
                    min_observations_for_orders=int(item.get("min_observations_for_orders", 20)),
                    evidence_required=bool(item.get("evidence_required", True)),
                    description=str(item.get("description", "")),
                    query_terms=[str(value) for value in item.get("query_terms", []) or []],
                    updated_at=str(item.get("updated_at", "")),
                )
            )
        return specs

    def save(self, specs: list[FactorSpec]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump([item.to_dict() for item in specs], sort_keys=False, allow_unicode=True), encoding="utf-8")

    def weights(self) -> dict[str, float]:
        specs = self.load()
        if not specs:
            specs = DEFAULT_FACTOR_SPECS
        return {item.name: float(item.weight) for item in specs}


def iterate_factor_specs(config: BotConfig, *, dry_run: bool = False) -> FactorIterationResult:
    store = FactorSpecStore(config.strategy_root)
    existing = {item.name: item for item in store.load()}
    added: list[str] = []
    updated: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    if config.strategy_lab.allow_new_factor_candidates:
        for default in DEFAULT_FACTOR_SPECS:
            if default.name not in existing:
                spec = FactorSpec(**default.to_dict())
                spec.min_observations_for_orders = config.strategy_lab.min_factor_observations_for_orders
                spec.updated_at = now
                existing[spec.name] = spec
                added.append(spec.name)
    for spec in existing.values():
        if spec.status == "active" and spec.min_observations_for_orders < config.strategy_lab.min_factor_observations_for_orders:
            spec.min_observations_for_orders = config.strategy_lab.min_factor_observations_for_orders
            spec.updated_at = now
            updated.append(spec.name)
    attribution_updates = apply_attribution_feedback(config, existing, now)
    updated.extend(name for name in attribution_updates if name not in updated)
    specs = sorted(existing.values(), key=lambda item: item.name)
    summary = render_factor_iteration_summary(specs, added, updated, dry_run=dry_run)
    if not dry_run:
        store.save(specs)
        memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
        for name in added:
            spec = existing[name]
            memory.add(
                "factor_spec",
                f"factor candidate {spec.name}: weight={spec.weight:g}, direction={spec.direction}",
                strategy="strategy_lab",
                importance=0.72,
                confidence=0.7,
                source="factor_iteration",
                metadata=spec.to_dict(),
            )
        memory.add(
            "factor_weight_update",
            summary,
            strategy="strategy_lab",
            importance=0.68,
            confidence=0.7,
            source="factor_iteration",
            metadata={"added": added, "updated": updated, "spec_count": len(specs)},
        )
    return FactorIterationResult(dry_run=dry_run, specs=specs, added=added, updated=updated, summary=summary)


def apply_attribution_feedback(config: BotConfig, existing: dict[str, FactorSpec], now: str) -> list[str]:
    if not config.strategy_lab.daily_factor_iteration_enabled:
        return []
    try:
        summaries = FactorAttributionStore.from_config(config).summary(
            horizon="1d",
            min_observations=max(1, int(config.strategy_lab.min_factor_observations_for_orders)),
        )
    except Exception:
        return []
    updated: list[str] = []
    for summary in summaries:
        spec = existing.get(summary.factor_name)
        if not spec:
            continue
        original = float(spec.weight or 0.0)
        if abs(original) <= 0:
            continue
        if summary.directional_score < -0.5:
            spec.weight = shrink_weight(original)
        elif summary.directional_score > 0.75:
            spec.weight = expand_weight(original)
        if spec.weight != original:
            spec.updated_at = now
            updated.append(spec.name)
    return updated


def shrink_weight(value: float) -> float:
    sign = -1.0 if value < 0 else 1.0
    return round(sign * max(0.1, min(20.0, abs(value) * 0.9)), 4)


def expand_weight(value: float) -> float:
    sign = -1.0 if value < 0 else 1.0
    return round(sign * max(0.1, min(20.0, abs(value) * 1.05)), 4)


def render_factor_iteration_summary(specs: list[FactorSpec], added: list[str], updated: list[str], *, dry_run: bool) -> str:
    lines = [
        "因子迭代",
        f"- dry_run={dry_run}; specs={len(specs)}; added={len(added)}; updated={len(updated)}",
    ]
    if added:
        lines.append("- 新增候选因子: " + ", ".join(added))
    for spec in specs[:12]:
        lines.append(f"- {spec.name}: status={spec.status}, weight={spec.weight:g}, direction={spec.direction}, min_obs={spec.min_observations_for_orders}")
    return "\n".join(lines)
