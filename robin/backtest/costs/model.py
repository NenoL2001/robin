from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CostModel:
    fixed_bps: float = 2.0
    spread_bps: float = 5.0
    impact_bps_per_turnover: float = 10.0

    def cost_fraction(self, turnover: float) -> float:
        bps = self.fixed_bps + self.spread_bps + abs(turnover) * self.impact_bps_per_turnover
        return bps / 10000.0
