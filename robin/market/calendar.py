from __future__ import annotations

from datetime import date


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5
