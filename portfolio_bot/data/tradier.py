from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from ..models import OptionContract


class TradierClient:
    def __init__(self, access_token: str, base_url: str = "https://api.tradier.com/v1", timeout: int = 10):
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.access_token)

    def expirations(self, symbol: str) -> list[datetime]:
        if not self.configured:
            return []
        data = self._get("/markets/options/expirations", {"symbol": symbol.upper(), "includeAllRoots": "true"})
        dates = data.get("expirations", {}).get("date", [])
        if isinstance(dates, str):
            dates = [dates]
        result: list[datetime] = []
        for value in dates:
            try:
                result.append(datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc))
            except ValueError:
                continue
        return result

    def option_chain(self, symbol: str, expiration: datetime) -> list[OptionContract]:
        if not self.configured:
            return []
        data = self._get(
            "/markets/options/chains",
            {"symbol": symbol.upper(), "expiration": expiration.date().isoformat(), "greeks": "true"},
        )
        options = data.get("options", {}).get("option", [])
        if isinstance(options, dict):
            options = [options]
        contracts: list[OptionContract] = []
        for row in options:
            greeks = row.get("greeks") or {}
            option_type = str(row.get("option_type", "")).lower()
            if option_type not in {"call", "put"}:
                continue
            contracts.append(
                OptionContract(
                    underlying=symbol.upper(),
                    symbol=str(row.get("symbol", "")),
                    expiration=expiration,
                    strike=float(row.get("strike")),
                    option_type=option_type,  # type: ignore[arg-type]
                    bid=_float(row.get("bid")),
                    ask=_float(row.get("ask")),
                    last=_float(row.get("last")),
                    mark=_float(row.get("mark")),
                    delta=_float(greeks.get("delta")),
                    implied_volatility=_float(greeks.get("mid_iv") or greeks.get("smv_vol")),
                    open_interest=_int(row.get("open_interest")),
                    volume=_int(row.get("volume")),
                )
            )
        return contracts

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        response = requests.get(self.base_url + path, params=params, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response.json()


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
