from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from ..backtest import BacktestStore
from ..config import BotConfig
from ..memory import MemoryStore, memory_path
from .observability import run_profile_suite
from ..research import ResearchEngine
from ..runtime import RuntimeStore, runtime_path
from ..storage import load_holdings


class OrchestratorAgent:
    """Operator-like scheduler that coordinates specialized local workers through jobs."""

    def __init__(self, config: BotConfig, runtime: RuntimeStore | None = None):
        self.config = config
        self.runtime = runtime or RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))

    def schedule_once(self) -> dict[str, int]:
        if not self.config.orchestration.enabled:
            return {}
        now = datetime.now(ZoneInfo(self.config.timezone))
        scheduled: dict[str, int] = {}
        scheduled["news_scan"] = self._enqueue_interval_job("news_scan", self.config.monitor.deep_scan_seconds, priority=20)
        if self.config.agent_harness.enabled:
            scheduled["operator_agent"] = self._enqueue_agent_interval_job(
                "operator_agent",
                max(300, self.config.orchestration.tick_seconds * 5),
                "检查 worker、job 和运行状态。",
                priority=18,
            )
        if due_today(now, self.config.monitor.report_time):
            scheduled["daily_report"] = self._enqueue_daily_job("daily_report", now, priority=10)
            if self.config.agent_harness.enabled:
                scheduled["report_agent"] = self._enqueue_agent_daily_job("report_agent", now, "汇总日报中的 Agent 运行状态。", priority=12)
                scheduled["risk_agent"] = self._enqueue_agent_daily_job("risk_agent", now, "复盘真实持仓和告警风险。", priority=13)
        if due_today(now, self.config.orchestration.strategy_review_time):
            scheduled["strategy_review"] = self._enqueue_daily_job("strategy_review", now, priority=30)
            if self.config.agent_harness.enabled:
                scheduled["strategy_agent"] = self._enqueue_agent_daily_job("strategy_agent", now, "复盘策略、feature 和模拟验证。", priority=31)
                scheduled["research_agent"] = self._enqueue_agent_daily_job("research_agent", now, "复盘新闻源质量和研究数据缺口。", priority=22)
        if due_today(now, self.config.orchestration.code_iteration_time):
            if self.config.agent_harness.enabled:
                scheduled["maintenance_agent"] = self._enqueue_agent_daily_job("maintenance_agent", now, "运行受限代码迭代 review、profile 和 PR 草稿。", priority=16)
                scheduled["verification_agent"] = self._enqueue_agent_daily_job("verification_agent", now, "验证测试、profile 和安全边界。", priority=17)
            else:
                payload = {"iterations": self.config.orchestration.profile_iterations, "dry_run": True}
                job_id = self.runtime.enqueue_job(
                    "profile_suite",
                    payload,
                    priority=15,
                    idempotency_key=f"profile_suite:{now.date().isoformat()}",
                )
                scheduled["profile_suite"] = 1 if job_id else 0
                job_id = self.runtime.enqueue_job(
                    "code_iteration_review",
                    payload,
                    priority=12,
                    idempotency_key=f"code_iteration_review:{now.date().isoformat()}",
                )
                scheduled["code_iteration_review"] = 1 if job_id else 0
        self.runtime.record_log("INFO", "orchestrator", "orchestrator", "schedule tick", scheduled)
        return scheduled

    def _enqueue_interval_job(self, job_type: str, interval_seconds: int, *, priority: int = 0) -> int:
        now = datetime.now(ZoneInfo(self.config.timezone))
        bucket = int(now.timestamp() // max(1, interval_seconds))
        job_id = self.runtime.enqueue_job(
            job_type,
            {"scheduled_at": now.isoformat(), "bucket": bucket},
            priority=priority,
            idempotency_key=f"{job_type}:{bucket}",
        )
        return 1 if job_id else 0

    def _enqueue_daily_job(self, job_type: str, now: datetime, *, priority: int = 0) -> int:
        job_id = self.runtime.enqueue_job(
            job_type,
            {"scheduled_for": now.date().isoformat()},
            priority=priority,
            idempotency_key=f"{job_type}:{now.date().isoformat()}",
        )
        return 1 if job_id else 0

    def _enqueue_agent_interval_job(self, agent_name: str, interval_seconds: int, objective: str, *, priority: int = 0) -> int:
        now = datetime.now(ZoneInfo(self.config.timezone))
        bucket = int(now.timestamp() // max(1, interval_seconds))
        job_id = self.runtime.enqueue_job(
            "agent_run",
            {"agent_name": agent_name, "objective": objective, "dry_run": True, "bucket": bucket},
            priority=priority,
            idempotency_key=f"agent_run:{agent_name}:{bucket}",
        )
        return 1 if job_id else 0

    def _enqueue_agent_daily_job(self, agent_name: str, now: datetime, objective: str, *, priority: int = 0) -> int:
        job_id = self.runtime.enqueue_job(
            "agent_run",
            {"agent_name": agent_name, "objective": objective, "dry_run": True, "scheduled_for": now.date().isoformat()},
            priority=priority,
            idempotency_key=f"agent_run:{agent_name}:{now.date().isoformat()}",
        )
        return 1 if job_id else 0


def due_today(now: datetime, clock: str) -> bool:
    try:
        hour, minute = [int(part) for part in clock.split(":", 1)]
    except ValueError:
        hour, minute = 16, 30
    return (now.hour, now.minute) >= (hour, minute)


def process_strategy_jobs(config: BotConfig, worker_id: str) -> bool:
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    job = runtime.claim_job(worker_id, ["strategy_review"])
    if not job:
        return False
    try:
        report = generate_strategy_review(config)
        runtime.complete_job(job.id, {"summary": report[:1000]})
        return True
    except Exception as exc:
        runtime.fail_job(job.id, str(exc), retry=False)
        return False


def generate_strategy_review(config: BotConfig) -> str:
    engine = ResearchEngine(config)
    holdings = load_holdings(config.holdings_path)
    symbols = sorted({holding.normalized_symbol() for holding in holdings if holding.asset_type in {"equity", "etf"}})
    plan = engine.generate_strategy_plan(symbols, holdings=holdings, dry_run=False)
    roundtable = engine.generate_strategy_roundtable(dry_run=False)
    summary = engine.strategy_skill_summary(symbols)
    backtests = engine.backtest_summary()
    content = (
        "策略 Agent 每日复盘\n\n"
        "## 策略计划\n"
        f"{plan.get('summary') or '暂无策略计划。'}\n\n"
        "## Agent 圆桌\n"
        f"{roundtable or '暂无圆桌纪要。'}\n\n"
        "## Skill 状态\n"
        f"{summary or '暂无 Skill 状态。'}\n\n"
        "## 最近回测\n"
        f"{backtests or '暂无回测。'}\n\n"
        "## 结论\n"
        "新策略默认只能进入 candidate；active 需要人工确认、足够回测和纸面交易验证。"
    )
    memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
    memory.add("strategy_lesson", content[:2000], strategy="strategy_agent", importance=0.75, confidence=0.7, source="strategy_agent")
    return content


def process_maintenance_jobs(config: BotConfig, worker_id: str) -> bool:
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    job = runtime.claim_job(worker_id, ["profile_suite", "code_iteration_review"])
    if not job:
        return False
    try:
        if job.type == "profile_suite":
            result = run_profile_suite(config, iterations=int(job.payload.get("iterations", 2)), dry_run=bool(job.payload.get("dry_run", True)))
            runtime.complete_job(job.id, {"summary": result["summary"]})
        else:
            path = run_code_iteration_review(config, iterations=int(job.payload.get("iterations", 2)), dry_run=bool(job.payload.get("dry_run", False)))
            runtime.complete_job(job.id, {"path": str(path)})
        return True
    except Exception as exc:
        runtime.fail_job(job.id, str(exc), retry=False)
        return False


def run_code_iteration_review(config: BotConfig, *, iterations: int = 2, dry_run: bool = False) -> Path:
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    started_at = datetime.now().isoformat(timespec="seconds")
    skill_text = load_code_iteration_skill(config.root)
    change_record = collect_local_change_record(config.root)
    profile = run_profile_suite(config, iterations=max(1, iterations), dry_run=True)
    py_files = [str(path) for path in sorted((config.root / "portfolio_bot").rglob("*.py"))]
    py_compile = run_command([sys.executable, "-m", "py_compile", *py_files], cwd=config.root)
    tests = run_command(["pytest", "-q"], cwd=config.root)
    strategy_dry_run = run_command([sys.executable, "-m", "portfolio_bot", "--config", str(config.config_path or config.root / "config.yaml"), "strategy-plan-now", "--dry-run"], cwd=config.root)
    activation = promote_candidate_strategies(config, py_compile, tests, strategy_dry_run, dry_run=dry_run)
    report_dir = config.data_dir / "code_iterations"
    report_path = report_dir / f"{datetime.now().date().isoformat()}.md"
    pr_dir = config.data_dir / "pr_drafts"
    pr_path = pr_dir / f"{datetime.now().date().isoformat()}-code-iteration.md"
    pr_body = render_pr_draft(started_at, profile["summary"], py_compile, tests, change_record, skill_text, activation)
    body = render_code_iteration_report(started_at, profile["summary"], py_compile, tests, strategy_dry_run, activation, change_record, skill_text, pr_path)
    if dry_run:
        runtime.record_log("INFO", "maintenance", "code_iteration", "code iteration dry-run complete", {"path": str(report_path), "pr_draft": str(pr_path)})
        return report_path
    report_dir.mkdir(parents=True, exist_ok=True)
    pr_dir.mkdir(parents=True, exist_ok=True)
    pr_path.write_text(pr_body, encoding="utf-8")
    report_path.write_text(body, encoding="utf-8")
    append_jsonl(config.root / "system_skills" / "code_iteration" / "review_memory.jsonl", {"created_at": started_at, "kind": "code_iteration_review", "report_path": str(report_path), "pr_draft_path": str(pr_path), "tests": tests.get("returncode"), "py_compile": py_compile.get("returncode"), "strategy_dry_run": strategy_dry_run.get("returncode"), "strategy_activation": activation})
    MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled).add(
        "daily_review",
        body[:2500],
        strategy="code_iteration_agent",
        importance=0.7,
        confidence=0.75,
        source="maintenance",
        metadata={"path": str(report_path), "pr_draft": str(pr_path)},
    )
    runtime.record_log("INFO", "maintenance", "code_iteration", "code iteration review written", {"path": str(report_path), "pr_draft": str(pr_path)})
    return report_path


def promote_candidate_strategies(
    config: BotConfig,
    py_compile: dict[str, object],
    tests: dict[str, object],
    strategy_dry_run: dict[str, object],
    *,
    dry_run: bool,
) -> dict[str, object]:
    policy = load_code_iteration_policy(config.root)
    gates = dict(policy.get("strategy_activation_gates", {}) or {})
    result: dict[str, object] = {"dry_run": dry_run, "enabled": bool(policy.get("auto_strategy_activation", False)), "promoted": [], "blocked": []}
    if not result["enabled"]:
        return result
    candidates = candidate_strategy_files(config.strategy_root)
    if not candidates:
        return result
    base_checks = []
    if gates.get("require_py_compile", True) and py_compile.get("returncode") != 0:
        base_checks.append("py_compile_failed")
    if gates.get("require_pytest", True) and tests.get("returncode") != 0:
        base_checks.append("pytest_failed")
    if gates.get("require_strategy_dry_run", True) and strategy_dry_run.get("returncode") != 0:
        base_checks.append("strategy_dry_run_failed")
    min_trades = int(gates.get("min_backtest_trades", config.strategy_risk.min_backtest_trades))
    max_drawdown = float(gates.get("max_backtest_drawdown", config.strategy_risk.max_backtest_drawdown))
    store = BacktestStore(config.data_dir / config.backtest.sqlite_path)
    for path, raw in candidates:
        name = str(raw.get("name", path.parent.name))
        blocked = list(base_checks)
        recent = store.recent(strategy_name=name, limit=1)
        backtest = recent[0] if recent else None
        if not backtest:
            blocked.append("missing_backtest")
        else:
            if backtest.trade_count < min_trades:
                blocked.append("weak_backtest_trades")
            if backtest.max_drawdown < max_drawdown:
                blocked.append("backtest_drawdown_failed")
        if blocked:
            result["blocked"].append({"strategy": name, "path": str(path), "checks": blocked})
            if not dry_run:
                append_jsonl(path.parent / "review_memory.jsonl", {"created_at": datetime.now().isoformat(timespec="seconds"), "kind": "strategy_activation_blocked", "strategy_name": name, "checks": blocked})
            continue
        if not dry_run:
            raw["status"] = "active"
            raw["activated_at"] = datetime.now().isoformat(timespec="seconds")
            path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
            append_jsonl(path.parent / "review_memory.jsonl", {"created_at": datetime.now().isoformat(timespec="seconds"), "kind": "strategy_activation", "strategy_name": name, "status": "active", "backtest_id": backtest.backtest_id})
        result["promoted"].append({"strategy": name, "path": str(path), "backtest_id": backtest.backtest_id, "dry_run": dry_run})
    return result


def load_code_iteration_policy(root: Path) -> dict[str, object]:
    path = root / "system_skills" / "code_iteration" / "policy.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}
    return raw if isinstance(raw, dict) else {}


def candidate_strategy_files(strategy_root: Path) -> list[tuple[Path, dict[str, object]]]:
    rows: list[tuple[Path, dict[str, object]]] = []
    for path in sorted(strategy_root.glob("*/strategy.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict) and str(raw.get("status", "candidate")) == "candidate":
            rows.append((path, raw))
    return rows


def run_command(cmd: list[str], *, cwd: Path) -> dict[str, object]:
    try:
        completed = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=180)
        return {"cmd": cmd, "returncode": completed.returncode, "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]}
    except Exception as exc:
        return {"cmd": cmd, "returncode": -1, "stdout": "", "stderr": str(exc)}


def load_code_iteration_skill(root: Path) -> str:
    path = root / "system_skills" / "code_iteration" / "SKILL.md"
    try:
        return path.read_text(encoding="utf-8")[:4000]
    except OSError:
        return "Code iteration skill missing; maintenance agent must stay proposal-only."


def collect_local_change_record(root: Path) -> dict[str, object]:
    git_status = run_command(["git", "status", "--short"], cwd=root)
    if git_status["returncode"] == 0:
        files = [line.strip() for line in str(git_status["stdout"]).splitlines() if line.strip()]
        return {"mode": "git", "files": files[:200], "raw": str(git_status["stdout"])[:4000]}
    cutoff = time.time() - 24 * 60 * 60
    suffixes = {".py", ".yaml", ".yml", ".md", ".toml", ".jsonl"}
    recent = []
    for path in root.rglob("*"):
        if ".portfolio_bot" in path.parts or "__pycache__" in path.parts or path.is_dir() or path.suffix not in suffixes:
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                recent.append(str(path.relative_to(root)))
        except OSError:
            continue
    return {"mode": "mtime_fallback", "files": sorted(recent)[:200], "raw": "not a git repository"}


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def render_code_iteration_report(
    started_at: str,
    profile_summary: list[dict],
    py_compile: dict[str, object],
    tests: dict[str, object],
    strategy_dry_run: dict[str, object],
    activation: dict[str, object],
    change_record: dict[str, object],
    skill_text: str,
    pr_path: Path,
) -> str:
    profile_lines = "\n".join(
        f"- {row['operation']}: avg={row['avg_ms']}ms max={row['max_ms']}ms failed={row['failed']}"
        for row in profile_summary
    )
    changed_files = "\n".join(f"- {file}" for file in list(change_record.get("files", []))[:80])
    return (
        f"# 代码迭代 Agent 复盘\n\n"
        f"- started_at: {started_at}\n"
        f"- py_compile: returncode={py_compile['returncode']}\n"
        f"- pytest: returncode={tests['returncode']}\n"
        f"- strategy_dry_run: returncode={strategy_dry_run['returncode']}\n\n"
        "## Skill 约束\n"
        f"使用 `system_skills/code_iteration`。策略候选可在固定验证门槛通过后自动升 active；真实交易边界不变。\n\n"
        "## 本地改动记录\n"
        f"- mode: {change_record.get('mode')}\n"
        f"{changed_files or '- 暂无最近文件改动记录。'}\n\n"
        "## PR 草稿\n"
        f"{pr_path}\n\n"
        "## Profile 摘要\n"
        f"{profile_lines or '暂无 profile 数据。'}\n\n"
        "## 测试输出\n"
        "```text\n"
        f"{tests.get('stdout', '')}{tests.get('stderr', '')}\n"
        "```\n\n"
        "## 策略自动激活\n"
        "```json\n"
        f"{json.dumps(activation, ensure_ascii=False, indent=2, default=str)}\n"
        "```\n\n"
        "## 迭代建议\n"
        "- 优先处理失败 profile 或 429/rate limit。\n"
        "- 新策略先以 candidate 记录；只有通过 py_compile、pytest、策略 dry-run 和回测门槛才升 active。\n"
        "- 真实代码改动仍应保持小范围、可回滚，并保留 agent review 记录。\n"
    )


def render_pr_draft(started_at: str, profile_summary: list[dict], py_compile: dict[str, object], tests: dict[str, object], change_record: dict[str, object], skill_text: str, activation: dict[str, object]) -> str:
    failed_profiles = [row for row in profile_summary if row.get("failed")]
    slow_profiles = sorted(profile_summary, key=lambda row: float(row.get("avg_ms", 0)), reverse=True)[:5]
    profile_lines = "\n".join(f"- {row['operation']}: avg={row['avg_ms']}ms max={row['max_ms']}ms failed={row['failed']}" for row in slow_profiles)
    failed_lines = "\n".join(f"- {row['operation']}: {row.get('last_error', '')}" for row in failed_profiles)
    changed_files = "\n".join(f"- {file}" for file in list(change_record.get("files", []))[:80])
    tests_ok = py_compile.get("returncode") == 0 and tests.get("returncode") == 0
    return (
        "# PR Draft: Portfolio Bot Daily Code Iteration\n\n"
        "## Summary\n"
        "- Daily maintenance output generated by `system_skills/code_iteration`.\n"
        "- Scope should stay narrow: one correctness, observability, rate-limit, or hotpath improvement.\n"
        "- Strategy candidates may auto-promote only after fixed gates pass; no real trading changes.\n\n"
        "## Evidence\n"
        f"- started_at: {started_at}\n"
        f"- py_compile_returncode: {py_compile.get('returncode')}\n"
        f"- pytest_returncode: {tests.get('returncode')}\n"
        f"- tests_ok: {tests_ok}\n\n"
        "## Profile Hot Paths\n"
        f"{profile_lines or '- 暂无 profile 数据。'}\n\n"
        "## Failed/Noisy Signals\n"
        f"{failed_lines or '- 暂无失败 profile。'}\n\n"
        "## Local Change Record\n"
        f"- mode: {change_record.get('mode')}\n"
        f"{changed_files or '- 暂无最近文件改动记录。'}\n\n"
        "## Proposed Change\n"
        "- 优先把重复新闻/行情读取收敛到 DataHub cache 与 FeatureEngine，减少 Finnhub 429 和重复策略计算。\n"
        "- 保持策略、新闻、feature、report worker 通过 runtime jobs 解耦。\n\n"
        "## Strategy Activation\n"
        "```json\n"
        f"{json.dumps(activation, ensure_ascii=False, indent=2, default=str)}\n"
        "```\n\n"
        "## Agent Review\n"
        "- bounded_scope: yes\n"
        "- profile_evidence_included: yes\n"
        f"- tests_passed: {tests_ok}\n"
        "- no_real_trading_change: yes\n"
        "- dry_run_semantics_preserved: review_required\n"
        "- rollback: revert the narrow module patch and restart launchd service.\n\n"
        "## Skill Excerpt\n"
        "```text\n"
        f"{skill_text[:1200]}\n"
        "```\n"
    )
