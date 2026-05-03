from __future__ import annotations


def parse_success_rate(parsed: int, total: int) -> float:
    return 0.0 if total <= 0 else round(parsed / total, 6)


def verification_pass_rate(verified: int, total: int) -> float:
    return 0.0 if total <= 0 else round(verified / total, 6)
