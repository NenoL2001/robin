from __future__ import annotations

import json
from pathlib import Path

from ..config import BotConfig
from .openai_client import OpenAIService
from ..storage import load_holdings, save_holdings


def import_screenshot(config: BotConfig, image_path: Path, output_path: Path | None = None, confirm: bool = False) -> Path:
    service = OpenAIService(config)
    holdings = service.import_holdings_from_screenshot(image_path)
    output = output_path or config.data_dir / "holdings_import.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            [
                {
                    "symbol": h.symbol,
                    "name": h.name,
                    "asset_type": h.asset_type,
                    "quantity": h.quantity,
                    "market_value": h.market_value,
                    "avg_cost": h.avg_cost,
                    "metadata": h.metadata,
                }
                for h in holdings
            ],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if confirm:
        existing = {h.normalized_symbol(): h for h in load_holdings(config.holdings_path)}
        for holding in holdings:
            existing[holding.normalized_symbol()] = holding
        save_holdings(config.holdings_path, existing.values())
    return output
