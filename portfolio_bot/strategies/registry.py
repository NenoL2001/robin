from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import yaml

from ..config import ResearchConfig
from ..models import Holding, StrategyInfo
from .base import StrategySkill


def load_strategies(strategy_root: Path, research: ResearchConfig, holdings: list[Holding] | None = None) -> list[StrategySkill]:
    strategy_root.mkdir(parents=True, exist_ok=True)
    ensure_default_strategy_files(strategy_root)
    strategies: list[StrategySkill] = []
    for info in load_strategy_infos(strategy_root):
        if info.status != "active":
            continue
        strategy = instantiate_strategy(info.calculation, research, holdings or [])
        if strategy is not None:
            strategies.append(strategy)
    return strategies


def instantiate_strategy(calculation: dict[str, Any], research: ResearchConfig, holdings: list[Holding]) -> StrategySkill | None:
    module_name = str(calculation.get("module", "")).strip()
    class_name = str(calculation.get("class", "")).strip()
    if not module_name or not class_name:
        return None
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    try:
        instance = cls(research, holdings=holdings)
    except TypeError:
        try:
            instance = cls(research)
        except TypeError:
            instance = cls()
    if not isinstance(instance, StrategySkill):
        raise TypeError(f"{module_name}.{class_name} is not a StrategySkill")
    return instance


def load_strategy_infos(strategy_root: Path) -> list[StrategyInfo]:
    ensure_default_strategy_files(strategy_root)
    infos: list[StrategyInfo] = []
    for strategy_file in sorted(strategy_root.glob("*/strategy.yaml")):
        raw = yaml.safe_load(strategy_file.read_text(encoding="utf-8")) or {}
        infos.append(
            StrategyInfo(
                name=str(raw.get("name", strategy_file.parent.name)),
                version=str(raw.get("version", "1.0.0")),
                status=str(raw.get("status", "candidate")),
                description=str(raw.get("description", "")),
                path=str(strategy_file.parent),
                data_sources=[str(item) for item in raw.get("data_sources", []) or []],
                metric_ops=[str(item) for item in raw.get("metric_ops", []) or []],
                calculation=dict(raw.get("calculation", {}) or {}),
            )
        )
    return infos


def strategy_info_map(strategy_root: Path) -> dict[str, StrategyInfo]:
    return {info.name: info for info in load_strategy_infos(strategy_root)}


def ensure_default_strategy_files(strategy_root: Path) -> None:
    path = strategy_root / "semiconductor_reversal"
    path.mkdir(parents=True, exist_ok=True)
    strategy_file = path / "strategy.yaml"
    memory_file = path / "memory.jsonl"
    if not strategy_file.exists():
        strategy_file.write_text(
            """name: semiconductor_reversal
version: 1.0.0
status: active
description: Finds semiconductor chain companies near earnings explosion or valuation reversal phases.
data_sources:
  - quotes
  - company_news
  - feature_bundle
calculation:
  module: portfolio_bot.strategies.semiconductor_reversal
  class: SemiconductorReversalStrategy
metric_ops:
  - quote
  - exposure
  - news
  - sentiment
  - chain_exposure
universe_examples:
  - INTC
  - AXTI
  - AEHR
  - POET
option_profile:
  min_days: 180
  max_days: 548
  max_premium: 1500
score_fields:
  - bull_case
  - bear_case
  - catalysts
  - valuation_gap
  - option_quality
  - risk_flags
  - confidence
""",
            encoding="utf-8",
        )
    rules_file = path / "rules.py"
    if not rules_file.exists():
        rules_file.write_text(
            '"""Deterministic rules live in portfolio_bot.strategies.semiconductor_reversal for v1."""\n',
            encoding="utf-8",
        )
    prompt_file = path / "prompt.md"
    if not prompt_file.exists():
        prompt_file.write_text(
            """# semiconductor_reversal Prompt

用中文评估半导体链路里的业绩爆发、估值反转、长期 call 风险收益。
输出研究候选，不给真实交易指令。
""",
            encoding="utf-8",
        )
    backtest_file = path / "backtest.yaml"
    if not backtest_file.exists():
        backtest_file.write_text(
            """min_trades: 8
max_drawdown_limit: -0.35
paper_trading_min_days: 14
slippage_bps: 10
""",
            encoding="utf-8",
        )
    review_file = path / "review_memory.jsonl"
    if not review_file.exists():
        review_file.write_text("", encoding="utf-8")
    if not memory_file.exists():
        memory_file.write_text("", encoding="utf-8")
