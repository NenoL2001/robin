from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from ...backtest import default_demo_prices, format_backtest_result, run_equity_backtest
from ...config import BotConfig
from ...data_hub import DataHub
from ...features import FeatureEngine
from ...memory import MemoryStore, format_memory, memory_path
from ...paper import PaperBroker
from ...research import ResearchEngine
from ...runtime import RuntimeStore, runtime_path
from ...storage import load_holdings
from ..orchestrator import generate_strategy_review, run_code_iteration_review
from ..source_config import SourceConfigManager


ToolFunc = Callable[[dict[str, Any]], dict[str, Any]]


class ToolRegistry:
    def __init__(self, config: BotConfig, runtime: RuntimeStore, memory: MemoryStore, *, dry_run: bool):
        self.config = config
        self.runtime = runtime
        self.memory = memory
        self.dry_run = dry_run
        self.tools: dict[str, ToolFunc] = {
            "runtime_status": self.runtime_status,
            "workers_status": self.workers_status,
            "memory_search": self.memory_search,
            "memory_add": self.memory_add,
            "data_news": self.data_news,
            "feature_compute": self.feature_compute,
            "strategy_news_scout": self.strategy_news_scout,
            "factor_iterate": self.factor_iterate,
            "strategy_review": self.strategy_review,
            "agent_status_summary": self.agent_status_summary,
            "profile_suite": self.profile_suite,
            "test_py_compile": self.test_py_compile,
            "test_pytest": self.test_pytest,
            "code_iteration_review": self.code_iteration_review,
            "paper_snapshot": self.paper_snapshot,
            "backtest_demo": self.backtest_demo,
            "source_config_review": self.source_config_review,
            "agent_doctor": self.agent_doctor,
            "code_patch": self.code_patch,
        }

    def execute(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if tool_name not in self.tools:
            raise ValueError(f"unknown agent tool: {tool_name}")
        return self.tools[tool_name](payload)

    def runtime_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.status()

    def workers_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"workers": self.runtime.worker_statuses(stale_after_seconds=max(60, self.config.workers.heartbeat_seconds * 3))}

    def memory_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows = self.memory.search(str(payload.get("query", "")), symbol=str(payload.get("symbol", "")), limit=int(payload.get("limit", 8)))
        return {"count": len(rows), "items": [format_memory(row) for row in rows]}

    def memory_add(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.dry_run:
            return {"dry_run": True, "memory_id": None}
        rowid = self.memory.add(
            str(payload.get("kind", "agent_note")),
            str(payload.get("content", "")),
            symbol=str(payload.get("symbol", "")),
            strategy=str(payload.get("strategy", "")),
            importance=float(payload.get("importance", 0.5)),
            confidence=float(payload.get("confidence", 0.5)),
            source=str(payload.get("source", "agent_tool")),
            metadata=dict(payload.get("metadata", {}) or {}),
            evidence_links=list(payload.get("evidence_links", []) or []),
            expires_at=payload.get("expires_at"),
            related_run_id=payload.get("related_run_id"),
        )
        return {"memory_id": rowid}

    def data_news(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbols = [str(symbol).upper() for symbol in payload.get("symbols", []) if symbol]
        if not symbols:
            symbols = sorted(holding_symbols(load_holdings(self.config.holdings_path)) | set(self.config.research.default_universe))
        news = DataHub(self.config, runtime=self.runtime).collect_news(symbols, days=int(payload.get("days", 3)), commit=False)
        return {
            "symbols": symbols[:50],
            "count": len(news),
            "items": [
                {"title": item.title, "source": item.source, "url": item.url, "symbols": item.symbols, "kind": item.kind}
                for item in news[:10]
            ],
        }

    def feature_compute(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbols = [str(symbol).upper() for symbol in payload.get("symbols", []) if symbol]
        if not symbols:
            symbols = sorted(holding_symbols(load_holdings(self.config.holdings_path)) | set(self.config.research.default_universe))[:12]
        hub = DataHub(self.config, runtime=self.runtime)
        quotes = hub.quotes(symbols, commit=False)
        news = hub.collect_news(symbols, commit=False)
        features = FeatureEngine(self.runtime).compute_many(symbols, quotes, news, holdings=load_holdings(self.config.holdings_path), commit=False)
        return {"features": features}

    def strategy_news_scout(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbols = [str(symbol).upper() for symbol in payload.get("symbols", []) if symbol]
        if not symbols:
            symbols = sorted(holding_symbols(load_holdings(self.config.holdings_path)) | set(self.config.research.default_universe))
        result = ResearchEngine(self.config).scout_strategy_news(
            symbols,
            strategy_name=str(payload.get("strategy", "semiconductor_reversal")),
            dry_run=self.dry_run or bool(payload.get("dry_run", False)),
            deep=True,
        )
        return result.to_dict()

    def factor_iterate(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = ResearchEngine(self.config).iterate_strategy_factors(dry_run=self.dry_run or bool(payload.get("dry_run", False)))
        return result.to_dict()

    def strategy_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.dry_run or payload.get("dry_run", False):
            engine = ResearchEngine(self.config)
            holdings = load_holdings(self.config.holdings_path)
            symbols = sorted({holding.normalized_symbol() for holding in holdings if holding.asset_type in {"equity", "etf"}})
            content = (
                "策略 Agent 每日复盘\n\n"
                "## Skill 状态\n"
                f"{engine.strategy_skill_summary(symbols) or '暂无 Skill 状态。'}\n\n"
                "## 最近回测\n"
                f"{engine.backtest_summary() or '暂无回测。'}\n\n"
                "## 结论\n"
                "dry-run：只生成复盘，不写真实策略状态。"
            )
        else:
            content = generate_strategy_review(self.config)
        return {"summary": content[:4000]}

    def agent_status_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        runs = self.runtime.recent_agent_runs(limit=int(payload.get("limit", 8)))
        failed = [row for row in runs if row.get("status") != "done"]
        lines = ["Agent 运行状态"]
        lines.append(f"- 最近 Agent runs: {len(runs)}")
        lines.append(f"- 最近失败/未完成: {len(failed)}")
        for row in runs[:6]:
            lines.append(f"- {row.get('agent_name')}: {row.get('status')} - {row.get('objective')}")
        return {"summary": "\n".join(lines), "runs": runs[:8]}

    def profile_suite(self, payload: dict[str, Any]) -> dict[str, Any]:
        from ..observability import run_profile_suite

        result = run_profile_suite(self.config, iterations=int(payload.get("iterations", 1)), dry_run=bool(payload.get("dry_run", True)))
        return {"summary": result.get("summary", [])[:12], "iterations": result.get("iterations")}

    def test_py_compile(self, payload: dict[str, Any]) -> dict[str, Any]:
        py_files = [str(path) for path in sorted((self.config.root / "portfolio_bot").rglob("*.py"))]
        return safe_run([sys.executable, "-m", "py_compile", *py_files], cwd=self.config.root)

    def test_pytest(self, payload: dict[str, Any]) -> dict[str, Any]:
        return safe_run(["pytest", "-q"], cwd=self.config.root, timeout=int(payload.get("timeout", 180)))

    def code_iteration_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        dry_run = self.dry_run or bool(payload.get("dry_run", False))
        path = run_code_iteration_review(self.config, iterations=int(payload.get("iterations", 1)), dry_run=dry_run)
        return {"path": str(path), "dry_run": dry_run, "written": not dry_run}

    def paper_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.dry_run:
            broker = PaperBroker(self.config.data_dir / self.config.paper.sqlite_path, self.config.paper.starting_cash)
        else:
            broker = PaperBroker(self.config.data_dir / self.config.paper.sqlite_path, self.config.paper.starting_cash, memory=self.memory)
        return broker.snapshot()

    def backtest_demo(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = run_equity_backtest(default_demo_prices(), strategy_name=str(payload.get("strategy_name", "agent_demo")), strategy_version="1.0.0")
        return {"summary": format_backtest_result(result), "result": asdict(result)}

    def source_config_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        dry_run = self.dry_run or bool(payload.get("dry_run", False))
        result = SourceConfigManager(self.config).ensure_default_sources(dry_run=dry_run)
        return asdict(result)

    def agent_doctor(self, payload: dict[str, Any]) -> dict[str, Any]:
        guardrails = {"no_real_trading": True, "no_secret_output": True, "dry_run": self.dry_run}
        return {
            "runtime": self.runtime.status(),
            "guardrails": guardrails,
            "agent_harness_enabled": self.config.agent_harness.enabled,
            "engine": self.config.agent_harness.engine,
            "auto_patch_enabled": self.config.agent_harness.auto_patch_enabled,
        }

    def code_patch(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": "not_applied", "reason": "generic harness records code_patch intent; actual patching is restricted to code_iteration workflow"}


def holding_symbols(holdings) -> set[str]:
    symbols: set[str] = set()
    for holding in holdings:
        if holding.asset_type == "option":
            underlying = str(holding.metadata.get("underlying", "")).strip().upper()
            if underlying:
                symbols.add(underlying)
        elif holding.asset_type in {"equity", "etf"}:
            symbols.add(holding.normalized_symbol())
    return symbols


def safe_run(cmd: list[str], *, cwd: Path, timeout: int = 180) -> dict[str, Any]:
    try:
        completed = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=timeout)
        return {"returncode": completed.returncode, "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]}
    except Exception as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc)}
