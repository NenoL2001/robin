from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True, slots=True)
class WalkForwardSplit:
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def walk_forward_splits(start: date, end: date, train_days: int, test_days: int) -> list[WalkForwardSplit]:
    splits: list[WalkForwardSplit] = []
    train_start = start
    while True:
        train_end = train_start + timedelta(days=train_days - 1)
        test_start = train_end + timedelta(days=1)
        test_end = test_start + timedelta(days=test_days - 1)
        if test_end > end:
            break
        splits.append(WalkForwardSplit(train_start, train_end, test_start, test_end))
        train_start = test_start
    return splits
