from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LeveragedExposure:
    symbol: str
    underlying: str
    multiplier: float
    direction: str = "long"

    @property
    def chinese_label(self) -> str:
        side = "做多" if self.direction == "long" else "做空"
        return f"{self.multiplier:g}x {side} {self.underlying}"

    @property
    def english_label(self) -> str:
        side = "long" if self.direction == "long" else "short"
        return f"{self.multiplier:g}x {side} {self.underlying}"


LEVERAGED_EXPOSURES: dict[str, LeveragedExposure] = {
    "SNXX": LeveragedExposure("SNXX", "SNDK", 2.0, "long"),
}


def leveraged_exposure(symbol: str) -> LeveragedExposure | None:
    return LEVERAGED_EXPOSURES.get(str(symbol or "").strip().upper())


def expand_leveraged_symbols(symbols: set[str] | list[str] | tuple[str, ...]) -> set[str]:
    expanded = {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
    for symbol in list(expanded):
        exposure = leveraged_exposure(symbol)
        if exposure:
            expanded.add(exposure.underlying)
    return expanded
