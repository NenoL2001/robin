from __future__ import annotations

from robin.contracts.factor import FactorDefinition
from robin.core.ids import stable_hash, stable_id


def default_factor_definitions(config_hash: str = "") -> list[FactorDefinition]:
    config_hash = config_hash or stable_hash({"registry": "default_vnext_factors", "version": 1})
    specs = [
        ("return_1d", "close / close_lag_1 - 1", ["close"], 1),
        ("return_5d", "close / close_lag_5 - 1", ["close"], 5),
        ("volume_zscore_20d", "zscore(volume, 20)", ["volume"], 20),
        ("close_location_value", "(close-low)/(high-low)", ["high", "low", "close"], 1),
    ]
    definitions: list[FactorDefinition] = []
    for name, formula, inputs, lookback in specs:
        definitions.append(
            FactorDefinition(
                id=stable_id("factor", {"name": name, "formula": formula, "config_hash": config_hash}),
                name=name,
                owner="vnext.factor_factory",
                status="active",
                formula=formula,
                inputs=inputs,
                lookback_days=lookback,
                neutralization=["industry", "market_cap"],
                config_hash=config_hash,
            )
        )
    return definitions
