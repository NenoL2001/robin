from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from typing import Any, Callable


FactorFormulaFn = Callable[[dict[str, Any]], float]


BUILTIN_FACTOR_NAMES = {
    "earnings_surprise",
    "guidance_revision",
    "datacenter_mix_shift",
    "contracted_revenue_visibility",
    "product_roadmap_acceleration",
    "hbf_ai_inference_moat",
    "official_source_strength",
    "sell_the_news_volatility",
    "intraday_followthrough",
    "large_move_reversal_risk",
    "position_crowding_pressure",
    "volume_confirmation",
    "bar_momentum_5d",
    "relative_volume_confirmation",
    "close_location_quality",
    "underlying_relation_strength",
    "news_quality_score",
    "evidence_freshness_decay",
    "source_diversity_confirmation",
    "analyst_revision_breadth",
    "relationship_event_pass_through",
    "post_event_drift_followthrough",
    "liquidity_break_risk",
}


@dataclass(frozen=True, slots=True)
class FactorFormula:
    ref: str
    version: str
    description: str
    compute: FactorFormulaFn

    @property
    def code_hash(self) -> str:
        try:
            source = inspect.getsource(self.compute)
        except (OSError, TypeError):
            source = repr(self.compute)
        payload = f"{self.ref}:{self.version}:{source}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]


class FactorFormulaRegistry:
    """Registry for pure local factor formulas.

    The current strategy still computes factors through the existing mining code;
    this registry gives each formula a stable, auditable ref/hash before future
    code-iteration can swap implementations under tests.
    """

    def __init__(self, formulas: dict[str, FactorFormula] | None = None):
        self._formulas = formulas or default_formulas()

    @classmethod
    def default(cls) -> "FactorFormulaRegistry":
        return cls()

    def get(self, ref: str) -> FactorFormula | None:
        return self._formulas.get(ref)

    def exists(self, ref: str) -> bool:
        return ref in self._formulas

    def hash_for_ref(self, ref: str) -> str:
        formula = self.get(ref)
        return formula.code_hash if formula else ""

    def validate_hash(self, ref: str, expected_hash: str) -> bool:
        if not expected_hash:
            return True
        return self.hash_for_ref(ref) == expected_hash

    def refs(self) -> list[str]:
        return sorted(self._formulas)


def default_formula_ref(name: str) -> str:
    clean = normalize_factor_name(name)
    return f"builtin.{clean}" if clean in BUILTIN_FACTOR_NAMES else "candidate.unimplemented"


def normalize_factor_name(name: str) -> str:
    return str(name or "").strip().lower().replace(" ", "_").replace("-", "_")


def builtin_factor_placeholder(snapshot: dict[str, Any]) -> float:
    """Pure placeholder for built-in formulas already implemented in factors.py."""

    value = snapshot.get("value", 0.0)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def default_formulas() -> dict[str, FactorFormula]:
    formulas = {
        f"builtin.{name}": FactorFormula(
            ref=f"builtin.{name}",
            version="1.0.0",
            description=f"Built-in local factor formula for {name}.",
            compute=builtin_factor_placeholder,
        )
        for name in BUILTIN_FACTOR_NAMES
    }
    return formulas
