from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import NewsItem, OptionCandidate, OptionContract, Quote, StrategyScore


class StrategySkill(ABC):
    name: str

    @abstractmethod
    def evaluate(
        self,
        symbol: str,
        quote: Quote | None,
        news: list[NewsItem],
        fundamentals: dict[str, Any] | None = None,
        features: dict[str, Any] | None = None,
    ) -> StrategyScore:
        raise NotImplementedError

    @abstractmethod
    def rank_options(
        self,
        symbol: str,
        quote: Quote | None,
        contracts: list[OptionContract],
        news: list[NewsItem],
    ) -> list[OptionCandidate]:
        raise NotImplementedError
