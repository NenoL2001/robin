from __future__ import annotations

from itertools import combinations


def minimal_cpcv_splits(n_groups: int, test_groups: int = 1) -> list[tuple[list[int], list[int]]]:
    groups = list(range(n_groups))
    result: list[tuple[list[int], list[int]]] = []
    for test in combinations(groups, test_groups):
        test_set = list(test)
        train = [group for group in groups if group not in test_set]
        result.append((train, test_set))
    return result
