from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from ..config import BotConfig
from ..memory import MemoryStore, memory_path
from .factor_attribution import FactorAttributionStore, FactorAttributionSummary
from .factor_formulas import FactorFormulaRegistry, default_formula_ref, normalize_factor_name


FACTOR_SPEC_DIR = "_factor_specs"
FACTOR_SPEC_FILE = "factors.yaml"
FACTOR_MUTATION_FILE = "mutations.jsonl"
FACTOR_STATUSES = {"candidate", "active", "quarantined", "retired"}
SCORING_STATUSES = {"active", "candidate"}


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
    formula_ref: str = ""
    formula_version: str = "1.0.0"
    formula_hash: str = ""
    source: str = "default"
    created_at: str = ""
    retired_at: str = ""
    reason: str = ""
    last_evaluated_at: str = ""
    negative_streak: int = 0
    positive_streak: int = 0

    def __post_init__(self) -> None:
        self.name = normalize_factor_name(self.name)
        self.status = self.status if self.status in FACTOR_STATUSES else "candidate"
        self.direction = self.direction if self.direction in {"positive", "negative"} else "positive"
        self.created_at = self.created_at or self.updated_at
        self.formula_ref = self.formula_ref or default_formula_ref(self.name)
        if not self.formula_hash:
            self.formula_hash = FactorFormulaRegistry.default().hash_for_ref(self.formula_ref)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def ensure_metadata(self, now: str) -> bool:
        changed = False
        if not self.created_at:
            self.created_at = self.updated_at or now
            changed = True
        if not self.updated_at:
            self.updated_at = now
            changed = True
        if not self.formula_ref:
            self.formula_ref = default_formula_ref(self.name)
            changed = True
        expected_hash = FactorFormulaRegistry.default().hash_for_ref(self.formula_ref)
        if expected_hash and not self.formula_hash:
            self.formula_hash = expected_hash
            changed = True
        return changed


@dataclass(slots=True)
class FactorMutation:
    action: str
    factor_name: str
    created_at: str
    reason: str = ""
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FactorIterationResult:
    dry_run: bool
    specs: list[FactorSpec]
    added: list[str]
    updated: list[str]
    promoted: list[str]
    quarantined: list[str]
    retired: list[str]
    formula_proposals: list[dict[str, Any]]
    mutations: list[FactorMutation]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "specs": [item.to_dict() for item in self.specs],
            "added": self.added,
            "updated": self.updated,
            "promoted": self.promoted,
            "quarantined": self.quarantined,
            "retired": self.retired,
            "formula_proposals": self.formula_proposals,
            "mutations": [item.to_dict() for item in self.mutations],
            "summary": self.summary,
        }


DEFAULT_FACTOR_SPECS = [
    FactorSpec("earnings_surprise", weight=14.0, description="Official earnings beat or above-guidance financial results.", query_terms=["earnings beat", "above guidance", "EPS", "revenue"]),
    FactorSpec("guidance_revision", weight=12.0, description="Forward revenue/EPS/margin guide raised or materially above consensus/prior guide.", query_terms=["guidance", "outlook", "Q4 guide", "raises guidance"]),
    FactorSpec("datacenter_mix_shift", weight=10.0, description="Revenue mix shift toward data center or AI infrastructure customers.", query_terms=["datacenter", "data center", "AI infrastructure"]),
    FactorSpec("contracted_revenue_visibility", weight=10.0, description="Multiyear commitments, financial guarantees, NBM contracts, or prepayments.", query_terms=["NBM", "multiyear", "financial guarantee", "prepayment"]),
    FactorSpec("product_roadmap_acceleration", weight=8.0, description="Sampling, shipping, ramp, or roadmap acceleration for a strategic product.", query_terms=["samples", "shipping", "ramp", "roadmap"]),
    FactorSpec("hbf_ai_inference_moat", weight=10.0, description="High Bandwidth Flash, BiCS8, Stargate, or AI inference memory roadmap evidence.", query_terms=["High Bandwidth Flash", "HBF", "BiCS8", "Stargate", "AI inference"]),
    FactorSpec("official_source_strength", weight=4.0, description="Evidence comes from official IR, company PDF, SEC filing, or primary source.", query_terms=["investor relations", "10-Q", "press release", "fact sheet"]),
    FactorSpec("sell_the_news_volatility", direction="negative", weight=-6.0, description="Post-earnings profit-taking, parabolic move, high short/options activity, or liquidity risk.", query_terms=["sell-the-news", "profit-taking", "parabolic", "volatility"]),
    FactorSpec("intraday_followthrough", weight=0.45, evidence_required=False, description="Deterministic local intraday behavior score from quote return, gap, and move shape.", query_terms=["intraday", "follow-through", "price behavior"]),
    FactorSpec("large_move_reversal_risk", direction="negative", weight=-1.0, evidence_required=False, description="Large daily move control that penalizes overextension even when news is strong.", query_terms=["overextension", "reversal risk", "large move"]),
    FactorSpec("position_crowding_pressure", direction="negative", weight=-1.0, evidence_required=False, description="Existing position size plus large daily move pressure for paper sizing discipline.", query_terms=["position sizing", "crowding", "exposure"]),
    FactorSpec("volume_confirmation", weight=1.0, evidence_required=False, description="Quote-level volume availability/confirmation until historical volume normalization is added.", query_terms=["volume", "confirmation"]),
    FactorSpec("bar_momentum_5d", weight=0.35, evidence_required=False, description="Local 5-day bar return contribution, capped to avoid chasing single-day spikes.", query_terms=["5d return", "trend"]),
    FactorSpec("relative_volume_confirmation", weight=1.5, evidence_required=False, description="Historical bar relative volume confirmation versus recent average.", query_terms=["relative volume", "volume breakout"]),
    FactorSpec("close_location_quality", weight=2.0, evidence_required=False, description="Close location value within the daily range as deterministic demand/supply clue.", query_terms=["close location", "range position"]),
    FactorSpec("underlying_relation_strength", weight=8.0, description="Inferred relationship from product/ETF/search evidence connecting a symbol to its economic underlying.", query_terms=["underlying", "2x long", "fund holdings", "ETF"]),
    FactorSpec("news_quality_score", weight=5.0, evidence_required=False, description="Deterministic source-tier, symbol-match, event, citation, and freshness quality score for evidence.", query_terms=["official source", "evidence quality", "fresh citation"]),
    FactorSpec("evidence_freshness_decay", direction="negative", weight=-2.0, evidence_required=False, description="Penalizes stale evidence so old headlines do not keep driving current signals.", query_terms=["freshness", "stale evidence", "event decay"]),
    FactorSpec("source_diversity_confirmation", weight=3.0, evidence_required=False, description="Rewards independent confirmation across official, transcript, finance, and industry sources.", query_terms=["source diversity", "independent confirmation"]),
    FactorSpec("analyst_revision_breadth", weight=4.0, description="Estimate, rating, or price-target revision breadth after confirmed events.", query_terms=["upgrade", "price target", "estimate revision", "rating"]),
    FactorSpec("relationship_event_pass_through", weight=4.5, description="Event pass-through from economic underlying to leveraged ETF/single-stock product exposure.", query_terms=["underlying event", "2x exposure", "read-through"]),
    FactorSpec("post_event_drift_followthrough", weight=0.4, evidence_required=False, description="Local price follow-through after verified events; helps distinguish sustained repricing from one-day spikes.", query_terms=["post-event drift", "follow-through"]),
    FactorSpec("liquidity_break_risk", direction="negative", weight=-4.0, evidence_required=False, description="Penalizes high-volatility moves with weak close location, poor volume, or likely liquidity air pockets.", query_terms=["liquidity risk", "reversal", "close location"]),
]


class FactorSpecStore:
    def __init__(self, strategy_root: Path):
        self.strategy_root = strategy_root
        self.path = strategy_root / FACTOR_SPEC_DIR / FACTOR_SPEC_FILE
        self.mutation_path = strategy_root / FACTOR_SPEC_DIR / FACTOR_MUTATION_FILE

    def list(self, status: str | None = None) -> list[FactorSpec]:
        specs = self.load()
        if status:
            return [spec for spec in specs if spec.status == status]
        return specs

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
            specs.append(factor_spec_from_mapping(item))
        return sorted(specs, key=lambda item: item.name)

    def get(self, name: str) -> FactorSpec | None:
        normalized = normalize_factor_name(name)
        return next((spec for spec in self.load() if spec.name == normalized), None)

    def save(self, specs: list[FactorSpec]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(specs, key=lambda item: item.name)
        payload = yaml.safe_dump([item.to_dict() for item in ordered], sort_keys=False, allow_unicode=True)
        tmp = self.path.with_name(f"{self.path.name}.tmp")
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_suffix(self.path.suffix + ".bak"))
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.path)

    def weights(self, include_statuses: set[str] | tuple[str, ...] | None = None) -> dict[str, float]:
        specs = self.load()
        if not specs:
            specs = clone_default_specs(datetime.now(timezone.utc).isoformat())
        allowed = set(include_statuses or SCORING_STATUSES)
        return {item.name: float(item.weight) for item in specs if item.status in allowed}

    def statuses(self) -> dict[str, str]:
        return {item.name: item.status for item in (self.load() or DEFAULT_FACTOR_SPECS)}

    def formula_hashes(self) -> dict[str, str]:
        return {item.name: item.formula_hash for item in (self.load() or DEFAULT_FACTOR_SPECS)}

    def upsert(self, spec: FactorSpec, *, reason: str = "manual_upsert") -> FactorMutation:
        specs = {item.name: item for item in self.load()}
        before = specs.get(spec.name)
        now = datetime.now(timezone.utc).isoformat()
        spec.updated_at = now
        spec.created_at = spec.created_at or now
        specs[spec.name] = spec
        self.save(list(specs.values()))
        action = "updated" if before else "added"
        return self._mutation(action, spec.name, reason, before=before, after=spec)

    def update(self, name: str, patch: dict[str, Any], *, reason: str = "manual_update") -> FactorMutation:
        specs = {item.name: item for item in self.load()}
        normalized = normalize_factor_name(name)
        if normalized not in specs:
            raise KeyError(f"factor not found: {name}")
        spec = specs[normalized]
        before = FactorSpec(**spec.to_dict())
        for key, value in patch.items():
            if hasattr(spec, key):
                setattr(spec, key, value)
        spec.__post_init__()
        spec.updated_at = datetime.now(timezone.utc).isoformat()
        specs[normalized] = spec
        self.save(list(specs.values()))
        return self._mutation("updated", normalized, reason, before=before, after=spec)

    def set_status(self, name: str, status: str, *, reason: str = "manual_status") -> FactorMutation:
        if status not in FACTOR_STATUSES:
            raise ValueError(f"unsupported factor status: {status}")
        specs = {item.name: item for item in self.load()}
        normalized = normalize_factor_name(name)
        if normalized not in specs:
            raise KeyError(f"factor not found: {name}")
        spec = specs[normalized]
        before = FactorSpec(**spec.to_dict())
        spec.status = status
        spec.reason = reason
        spec.updated_at = datetime.now(timezone.utc).isoformat()
        if status == "retired":
            spec.retired_at = spec.updated_at
        specs[normalized] = spec
        self.save(list(specs.values()))
        action = "retired" if status == "retired" else status
        return self._mutation(action, normalized, reason, before=before, after=spec)

    def retire(self, name: str, *, reason: str = "manual_retire") -> FactorMutation:
        return self.set_status(name, "retired", reason=reason)

    def hard_delete(self, name: str, *, confirm: str) -> FactorMutation:
        normalized = normalize_factor_name(name)
        if normalize_factor_name(confirm) != normalized:
            raise ValueError("hard delete requires --confirm NAME")
        specs = {item.name: item for item in self.load()}
        before = specs.pop(normalized, None)
        if not before:
            raise KeyError(f"factor not found: {name}")
        self.save(list(specs.values()))
        return self._mutation("hard_deleted", normalized, "manual_hard_delete", before=before, after=None)

    def append_mutations(self, mutations: list[FactorMutation]) -> None:
        if not mutations:
            return
        self.mutation_path.parent.mkdir(parents=True, exist_ok=True)
        with self.mutation_path.open("a", encoding="utf-8") as fh:
            for mutation in mutations:
                fh.write(json.dumps(mutation.to_dict(), ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def _mutation(action: str, name: str, reason: str, *, before: FactorSpec | None, after: FactorSpec | None, metadata: dict[str, Any] | None = None) -> FactorMutation:
        return FactorMutation(
            action=action,
            factor_name=normalize_factor_name(name),
            created_at=datetime.now(timezone.utc).isoformat(),
            reason=reason,
            before=before.to_dict() if before else None,
            after=after.to_dict() if after else None,
            metadata=metadata or {},
        )


class FactorLifecyclePolicy:
    def __init__(self, config: BotConfig, *, registry: FactorFormulaRegistry | None = None):
        self.config = config
        self.registry = registry or FactorFormulaRegistry.default()
        self.min_observations = max(1, int(config.strategy_lab.min_factor_observations_for_orders))
        self.promote_score = float(getattr(config.strategy_lab, "factor_promote_directional_score", 0.75))
        self.demote_score = float(getattr(config.strategy_lab, "factor_demote_directional_score", -0.5))
        self.quarantine_observations = int(getattr(config.strategy_lab, "factor_quarantine_observations", 40))
        self.retire_days = int(getattr(config.strategy_lab, "factor_retire_after_quarantine_days", 30))

    def run(
        self,
        existing: dict[str, FactorSpec],
        *,
        candidate_proposals: list[Any] | None,
        attribution_summary: list[FactorAttributionSummary],
        now: str,
    ) -> tuple[list[FactorSpec], list[FactorMutation], list[dict[str, Any]]]:
        mutations: list[FactorMutation] = []
        self._ensure_defaults(existing, mutations, now)
        self._ensure_schema(existing, mutations, now)
        self._apply_candidate_proposals(existing, candidate_proposals or [], mutations, now)
        self._apply_attribution(existing, attribution_summary, mutations, now)
        self._retire_stale_quarantined(existing, mutations, now)
        proposals = self._formula_proposals(existing)
        return sorted(existing.values(), key=lambda item: item.name), mutations, proposals

    def _ensure_defaults(self, existing: dict[str, FactorSpec], mutations: list[FactorMutation], now: str) -> None:
        if not self.config.strategy_lab.allow_new_factor_candidates:
            return
        for default in DEFAULT_FACTOR_SPECS:
            if default.name in existing:
                continue
            spec = FactorSpec(**default.to_dict())
            spec.min_observations_for_orders = self.min_observations
            spec.created_at = now
            spec.updated_at = now
            spec.source = "default_registry"
            existing[spec.name] = spec
            mutations.append(FactorSpecStore._mutation("added", spec.name, "missing_default_factor", before=None, after=spec))

    def _ensure_schema(self, existing: dict[str, FactorSpec], mutations: list[FactorMutation], now: str) -> None:
        for spec in existing.values():
            before = FactorSpec(**spec.to_dict())
            changed = spec.ensure_metadata(now)
            if spec.status == "active" and spec.min_observations_for_orders < self.min_observations:
                spec.min_observations_for_orders = self.min_observations
                changed = True
            if changed:
                spec.updated_at = now
                mutations.append(FactorSpecStore._mutation("updated", spec.name, "schema_migration", before=before, after=spec))

    def _apply_candidate_proposals(self, existing: dict[str, FactorSpec], proposals: list[Any], mutations: list[FactorMutation], now: str) -> None:
        if not self.config.strategy_lab.allow_new_factor_candidates:
            return
        for proposal in proposals:
            name = normalize_factor_name(getattr(proposal, "name", ""))
            if not name:
                continue
            if name not in existing:
                spec = FactorSpec(
                    name=name,
                    status="candidate",
                    direction=str(getattr(proposal, "direction", "positive")),
                    weight=float(getattr(proposal, "weight", 0.0) or 0.0),
                    min_observations_for_orders=int(getattr(proposal, "min_observations_for_orders", self.min_observations) or self.min_observations),
                    evidence_required=bool(getattr(proposal, "evidence_event_types", ()) or (float(getattr(proposal, "weight", 0.0) or 0.0) > 0)),
                    description=str(getattr(proposal, "reason", "")),
                    query_terms=[str(value) for value in getattr(proposal, "evidence_event_types", ()) or []],
                    source="news_strategy",
                    created_at=now,
                    updated_at=now,
                    reason="news/event candidate proposal",
                )
                existing[name] = spec
                mutations.append(FactorSpecStore._mutation("added", name, "news_event_factor_proposal", before=None, after=spec))
                continue
            spec = existing[name]
            before = FactorSpec(**spec.to_dict())
            changed = False
            for term in getattr(proposal, "evidence_event_types", ()) or []:
                if str(term) not in spec.query_terms:
                    spec.query_terms.append(str(term))
                    changed = True
            if not spec.description and getattr(proposal, "reason", ""):
                spec.description = str(getattr(proposal, "reason", ""))
                changed = True
            if changed:
                spec.updated_at = now
                mutations.append(FactorSpecStore._mutation("updated", name, "news_event_factor_refresh", before=before, after=spec))

    def _apply_attribution(self, existing: dict[str, FactorSpec], summaries: list[FactorAttributionSummary], mutations: list[FactorMutation], now: str) -> None:
        if not self.config.strategy_lab.daily_factor_iteration_enabled:
            return
        for summary in summaries:
            spec = existing.get(summary.factor_name)
            if not spec or spec.status == "retired":
                continue
            before = FactorSpec(**spec.to_dict())
            changed_actions: list[str] = []
            spec.last_evaluated_at = now
            if summary.directional_score < self.demote_score:
                spec.negative_streak += 1
                spec.positive_streak = 0
                if abs(spec.weight) > 0:
                    spec.weight = shrink_weight(spec.weight)
                    changed_actions.append("weight_changed")
                if spec.status in {"active", "candidate"} and (spec.negative_streak >= 2 or summary.observation_count >= self.quarantine_observations):
                    spec.status = "quarantined"
                    spec.reason = f"negative directional_score={summary.directional_score:g}"
                    changed_actions.append("quarantined")
                elif spec.status == "quarantined" and (spec.negative_streak >= 2 or summary.observation_count >= self.quarantine_observations):
                    spec.status = "retired"
                    spec.retired_at = now
                    spec.reason = f"retired after quarantine directional_score={summary.directional_score:g}"
                    changed_actions.append("retired")
            elif summary.directional_score > self.promote_score:
                spec.positive_streak += 1
                spec.negative_streak = 0
                if abs(spec.weight) > 0:
                    spec.weight = expand_weight(spec.weight)
                    changed_actions.append("weight_changed")
                if spec.status == "candidate" and summary.observation_count >= max(self.min_observations, spec.min_observations_for_orders) and self._formula_valid(spec):
                    spec.status = "active"
                    spec.reason = f"promoted directional_score={summary.directional_score:g}"
                    changed_actions.append("promoted")
                elif spec.status == "quarantined":
                    spec.status = "candidate"
                    spec.reason = f"restored from quarantine directional_score={summary.directional_score:g}"
                    changed_actions.append("updated")
            else:
                spec.negative_streak = 0
                spec.positive_streak = 0
                changed_actions.append("updated")
            if changed_actions:
                spec.updated_at = now
                action = status_action(changed_actions)
                mutations.append(FactorSpecStore._mutation(action, spec.name, "factor_attribution_feedback", before=before, after=spec, metadata=summary.to_dict()))

    def _retire_stale_quarantined(self, existing: dict[str, FactorSpec], mutations: list[FactorMutation], now: str) -> None:
        now_dt = parse_dt(now) or datetime.now(timezone.utc)
        for spec in existing.values():
            if spec.status != "quarantined" or not spec.last_evaluated_at:
                continue
            evaluated = parse_dt(spec.last_evaluated_at)
            if evaluated and now_dt - evaluated >= timedelta(days=self.retire_days):
                before = FactorSpec(**spec.to_dict())
                spec.status = "retired"
                spec.retired_at = now
                spec.updated_at = now
                spec.reason = f"retired after {self.retire_days} days in quarantine"
                mutations.append(FactorSpecStore._mutation("retired", spec.name, "stale_quarantined_factor", before=before, after=spec))

    def _formula_valid(self, spec: FactorSpec) -> bool:
        return self.registry.exists(spec.formula_ref) and self.registry.validate_hash(spec.formula_ref, spec.formula_hash)

    def _formula_proposals(self, existing: dict[str, FactorSpec]) -> list[dict[str, Any]]:
        if not getattr(self.config.strategy_lab, "allow_formula_candidates", True):
            return []
        proposals = []
        for spec in existing.values():
            if spec.status == "retired":
                continue
            if not self._formula_valid(spec):
                proposals.append(
                    {
                        "factor_name": spec.name,
                        "formula_ref": spec.formula_ref,
                        "reason": "missing formula registry entry or hash mismatch",
                        "required_gate": "code_iteration: py_compile + pytest + strategy-plan-now --dry-run + replay fixture",
                    }
                )
        return proposals


def iterate_factor_specs(config: BotConfig, *, dry_run: bool = False, candidate_proposals: list[Any] | None = None) -> FactorIterationResult:
    store = FactorSpecStore(config.strategy_root)
    existing = {item.name: item for item in store.load()}
    now = datetime.now(timezone.utc).isoformat()
    attribution = attribution_summaries(config)
    specs, mutations, formula_proposals = FactorLifecyclePolicy(config).run(
        existing,
        candidate_proposals=candidate_proposals,
        attribution_summary=attribution,
        now=now,
    )
    added = mutation_names(mutations, "added")
    promoted = mutation_names(mutations, "promoted")
    quarantined = mutation_names(mutations, "quarantined")
    retired = mutation_names(mutations, "retired")
    updated = sorted({mutation.factor_name for mutation in mutations if mutation.action in {"updated", "weight_changed", "formula_changed"} or mutation.action not in {"added", "promoted", "quarantined", "retired", "hard_deleted"}})
    summary = render_factor_iteration_summary(specs, added, updated, promoted, quarantined, retired, formula_proposals, dry_run=dry_run)
    if not dry_run:
        store.save(specs)
        record_factor_mutations(config, store, mutations, summary)
    return FactorIterationResult(
        dry_run=dry_run,
        specs=specs,
        added=added,
        updated=updated,
        promoted=promoted,
        quarantined=quarantined,
        retired=retired,
        formula_proposals=formula_proposals,
        mutations=mutations,
        summary=summary,
    )


def factor_spec_from_mapping(item: dict[str, Any]) -> FactorSpec:
    return FactorSpec(
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
        formula_ref=str(item.get("formula_ref", "")),
        formula_version=str(item.get("formula_version", "1.0.0")),
        formula_hash=str(item.get("formula_hash", "")),
        source=str(item.get("source", "legacy_yaml")),
        created_at=str(item.get("created_at", "")),
        retired_at=str(item.get("retired_at", "")),
        reason=str(item.get("reason", "")),
        last_evaluated_at=str(item.get("last_evaluated_at", "")),
        negative_streak=int(item.get("negative_streak", 0) or 0),
        positive_streak=int(item.get("positive_streak", 0) or 0),
    )


def clone_default_specs(now: str) -> list[FactorSpec]:
    specs = []
    for default in DEFAULT_FACTOR_SPECS:
        spec = FactorSpec(**default.to_dict())
        spec.created_at = spec.created_at or now
        spec.updated_at = spec.updated_at or now
        spec.source = "default_registry"
        specs.append(spec)
    return specs


def attribution_summaries(config: BotConfig) -> list[FactorAttributionSummary]:
    try:
        return FactorAttributionStore.from_config(config).summary(horizon="1d", min_observations=1)
    except Exception:
        return []


def record_factor_mutations(config: BotConfig, store: FactorSpecStore, mutations: list[FactorMutation], summary: str) -> None:
    store.append_mutations(mutations)
    memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
    for mutation in mutations:
        if mutation.action == "added":
            memory.add(
                "factor_spec",
                f"factor candidate {mutation.factor_name}",
                strategy="strategy_lab",
                importance=0.72,
                confidence=0.7,
                source="factor_iteration",
                metadata=mutation.to_dict(),
            )
        memory.add(
            "factor_mutation",
            f"factor {mutation.action}: {mutation.factor_name}; {mutation.reason}",
            strategy="strategy_lab",
            importance=0.7,
            confidence=0.72,
            source="factor_lifecycle",
            metadata=mutation.to_dict(),
        )
    memory.add(
        "factor_weight_update",
        summary,
        strategy="strategy_lab",
        importance=0.68,
        confidence=0.7,
        source="factor_iteration",
        metadata={"mutations": [item.to_dict() for item in mutations]},
    )


def apply_attribution_feedback(config: BotConfig, existing: dict[str, FactorSpec], now: str) -> list[str]:
    summaries = attribution_summaries(config)
    _, mutations, _ = FactorLifecyclePolicy(config).run(existing, candidate_proposals=[], attribution_summary=summaries, now=now)
    return [mutation.factor_name for mutation in mutations]


def shrink_weight(value: float) -> float:
    sign = -1.0 if value < 0 else 1.0
    return round(sign * max(0.1, min(20.0, abs(value) * 0.9)), 4)


def expand_weight(value: float) -> float:
    sign = -1.0 if value < 0 else 1.0
    return round(sign * max(0.1, min(20.0, abs(value) * 1.05)), 4)


def mutation_names(mutations: list[FactorMutation], action: str) -> list[str]:
    return sorted({mutation.factor_name for mutation in mutations if mutation.action == action})


def status_action(actions: list[str]) -> str:
    for action in ("retired", "quarantined", "promoted", "weight_changed", "updated"):
        if action in actions:
            return action
    return actions[0] if actions else "updated"


def parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def render_factor_iteration_summary(
    specs: list[FactorSpec],
    added: list[str],
    updated: list[str],
    promoted: list[str],
    quarantined: list[str],
    retired: list[str],
    formula_proposals: list[dict[str, Any]],
    *,
    dry_run: bool,
) -> str:
    lines = [
        "因子迭代",
        (
            f"- dry_run={dry_run}; specs={len(specs)}; added={len(added)}; updated={len(updated)}; "
            f"promoted={len(promoted)}; quarantined={len(quarantined)}; retired={len(retired)}; formula_proposals={len(formula_proposals)}"
        ),
    ]
    if added:
        lines.append("- 新增候选因子: " + ", ".join(added))
    if promoted:
        lines.append("- 升级为 active: " + ", ".join(promoted))
    if quarantined:
        lines.append("- 隔离因子: " + ", ".join(quarantined))
    if retired:
        lines.append("- 退休因子: " + ", ".join(retired))
    for item in formula_proposals[:8]:
        lines.append(f"- 公式候选/修复: {item.get('factor_name')} -> {item.get('formula_ref')} ({item.get('reason')})")
    for spec in specs[:12]:
        lines.append(f"- {spec.name}: status={spec.status}, weight={spec.weight:g}, direction={spec.direction}, min_obs={spec.min_observations_for_orders}")
    return "\n".join(lines)
