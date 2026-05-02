from __future__ import annotations

import base64
import datetime as dt
from typing import Any

import requests


class RobinhoodCryptoClient:
    """Read-only Robinhood Crypto API client skeleton.

    Configure only API credentials created in Robinhood Crypto settings. This
    client intentionally exposes read methods only.
    """

    def __init__(self, api_key: str = "", base64_private_key: str = "", timeout: int = 10):
        self.api_key = api_key
        self.base64_private_key = base64_private_key
        self.timeout = timeout
        self.base_url = "https://trading.robinhood.com"
        self._signing_key = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base64_private_key)

    def accounts(self) -> Any:
        return self._request("GET", "/api/v2/crypto/trading/accounts/")

    def holdings(self, account_number: str) -> Any:
        return self._request("GET", f"/api/v2/crypto/trading/holdings/?account_number={account_number}")

    def best_bid_ask(self, *symbols: str) -> Any:
        query = "&".join(f"symbol={symbol}" for symbol in symbols)
        suffix = f"?{query}" if query else ""
        return self._request("GET", f"/api/v2/crypto/marketdata/best_bid_ask/{suffix}")

    def _request(self, method: str, path: str, body: str = "") -> Any:
        if not self.configured:
            return None
        headers = self._headers(method, path, body)
        response = requests.request(method, self.base_url + path, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _headers(self, method: str, path: str, body: str) -> dict[str, str]:
        from nacl.signing import SigningKey

        if self._signing_key is None:
            self._signing_key = SigningKey(base64.b64decode(self.base64_private_key))
        timestamp = int(dt.datetime.now(tz=dt.timezone.utc).timestamp())
        message = f"{self.api_key}{timestamp}{path}{method}{body}"
        signed = self._signing_key.sign(message.encode("utf-8"))
        return {
            "x-api-key": self.api_key,
            "x-signature": base64.b64encode(signed.signature).decode("utf-8"),
            "x-timestamp": str(timestamp),
        }
