from __future__ import annotations

import json
import logging
import os
import time
import tracemalloc
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..backtest import default_demo_prices, run_equity_backtest
from ..config import BotConfig
from ..memory import MemoryStore, memory_path
from ..notifications import build_notifier
from ..paper import PaperBroker
from ..runtime import RuntimeStore, runtime_path
from ..storage import load_holdings


@dataclass(slots=True)
class ProfileMeasurement:
    operation: str
    role: str
    worker_id: str
    ok: bool
    duration_ms: float
    cpu_ms: float
    peak_kb: float
    metadata: dict[str, Any]
    error: str


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "pid": os.getpid(),
        }
        for key in ("role", "worker_id", "operation", "status"):
            value = getattr(record, key, "")
            if value:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(config: BotConfig, role: str, worker_id: str = "") -> logging.Logger:
    logger = logging.getLogger(f"portfolio_bot.{role}.{os.getpid()}")
    logger.setLevel(level_number(config.logging.level))
    logger.propagate = False
    if logger.handlers:
        return logger
    if not config.logging.enabled:
        logger.addHandler(logging.NullHandler())
        return logger
    path = observability_log_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JsonLineFormatter())
    handler.setLevel(level_number(config.logging.level))
    logger.addHandler(handler)
    logger.info("logger initialized", extra={"role": role, "worker_id": worker_id})
    return logger


def observability_log_path(config: BotConfig) -> Path:
    path = Path(config.logging.jsonl_path)
    if path.is_absolute():
        return path
    return config.data_dir / path


def level_number(value: str) -> int:
    return int(getattr(logging, value.upper(), logging.INFO))


def profile_operation(
    runtime: RuntimeStore,
    operation: str,
    role: str,
    worker_id: str,
    func: Callable[[], Any],
    *,
    track_memory: bool = False,
    metadata: dict[str, Any] | None = None,
) -> Any:
    result, measurement = measure_operation(
        runtime,
        operation,
        role,
        worker_id,
        func,
        track_memory=track_memory,
        metadata=metadata,
        raise_errors=True,
    )
    return result


def measure_operation(
    runtime: RuntimeStore,
    operation: str,
    role: str,
    worker_id: str,
    func: Callable[[], Any],
    *,
    track_memory: bool = False,
    metadata: dict[str, Any] | None = None,
    raise_errors: bool = False,
) -> tuple[Any, ProfileMeasurement]:
    start_wall = time.perf_counter()
    start_cpu = time.process_time()
    started_trace = False
    result: Any = None
    error = ""
    exc: BaseException | None = None
    if track_memory and not tracemalloc.is_tracing():
        tracemalloc.start()
        started_trace = True
    try:
        result = func()
        ok = True
    except BaseException as caught:
        ok = False
        error = str(caught)
        exc = caught
    finally:
        peak_kb = 0.0
        if track_memory and tracemalloc.is_tracing():
            _, peak = tracemalloc.get_traced_memory()
            peak_kb = peak / 1024.0
        if started_trace:
            tracemalloc.stop()
        measurement = ProfileMeasurement(
            operation=operation,
            role=role,
            worker_id=worker_id,
            ok=ok,
            duration_ms=(time.perf_counter() - start_wall) * 1000.0,
            cpu_ms=(time.process_time() - start_cpu) * 1000.0,
            peak_kb=peak_kb,
            metadata=metadata or {},
            error=error,
        )
        runtime.record_profile(
            operation,
            role,
            worker_id,
            ok=measurement.ok,
            duration_ms=measurement.duration_ms,
            cpu_ms=measurement.cpu_ms,
            peak_kb=measurement.peak_kb,
            metadata=measurement.metadata,
            error=measurement.error,
        )
    if exc and raise_errors:
        raise exc
    return result, measurement


def run_profile_suite(config: BotConfig, *, iterations: int = 3, dry_run: bool = True) -> dict[str, Any]:
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    worker_id = f"profile-suite:{os.getpid()}"
    logger = setup_logging(config, "profile", worker_id)
    rows: list[dict[str, Any]] = []

    def record(operation: str, func: Callable[[], Any]) -> None:
        result, measurement = measure_operation(
            runtime,
            operation,
            "profile",
            worker_id,
            func,
            track_memory=True,
            raise_errors=False,
        )
        row = asdict(measurement)
        row["result_count"] = result_size(result)
        rows.append(row)
        level = logging.INFO if measurement.ok else logging.ERROR
        logger.log(level, "profile step complete", extra={"role": "profile", "worker_id": worker_id, "operation": operation})

    for index in range(max(1, iterations)):
        logger.info("profile iteration start", extra={"role": "profile", "worker_id": worker_id, "operation": f"iteration.{index + 1}"})
        record("scan_once", lambda: _profile_scan_once(config, dry_run=dry_run))
        record("deep_scan", lambda: _profile_deep_scan(config, dry_run=dry_run))
        record("report_now", lambda: _profile_report(config, dry_run=dry_run))
        record("paper_snapshot", lambda: _paper_broker(config).snapshot())
        record("paper_review", lambda: _paper_broker(config).review())
        record("backtest_equity", lambda: run_equity_backtest(default_demo_prices(), strategy_name="profile_suite", strategy_version="1.0.0"))
        record("memory_search", lambda: MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled).search("POET long call", limit=5))
        record("runtime_status", lambda: runtime.status())
        record("workers_status", lambda: runtime.worker_statuses(stale_after_seconds=max(60, config.workers.heartbeat_seconds * 3)))
    summary = runtime.profile_summary(limit=max(100, iterations * 20))
    return {"iterations": max(1, iterations), "dry_run": dry_run, "results": rows, "summary": summary}


def run_health_check(config: BotConfig, worker_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    stale_after = worker_stale_after_seconds(config)
    workers = runtime.worker_statuses(stale_after_seconds=stale_after)
    expected = expected_worker_counts(config)
    running_by_role: dict[str, int] = {role: 0 for role in expected}
    stale_by_role: dict[str, int] = {role: 0 for role in expected}
    live_by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in expected}
    latest_by_role: dict[str, dict[str, Any]] = {}
    dead_pids: list[dict[str, Any]] = []
    dead_worker_ids: list[str] = []
    for row in workers:
        role = str(row.get("role", ""))
        status = str(row.get("status", ""))
        pid = int(row.get("pid") or 0)
        current = latest_by_role.get(role)
        if current is None or str(row.get("last_seen_at", "")) > str(current.get("last_seen_at", "")):
            latest_by_role[role] = row
        alive = pid_alive(pid)
        if status == "stale":
            stale_by_role[role] = stale_by_role.get(role, 0) + 1
            if alive:
                live_by_role.setdefault(role, []).append(row)
            continue
        if status == "running" and alive:
            live_by_role.setdefault(role, []).append(row)
        elif status == "running":
            dead_worker_ids.append(str(row.get("worker_id", "")))
    runtime.mark_workers_status(dead_worker_ids, "stale")
    for role, live_rows in live_by_role.items():
        running_by_role[role] = len(live_rows)
    for role, count in expected.items():
        if count <= 0 or running_by_role.get(role, 0) >= count:
            continue
        latest = latest_by_role.get(role)
        if latest and str(latest.get("status", "")) == "running" and not pid_alive(int(latest.get("pid") or 0)):
            dead_pids.append({"role": role, "worker_id": latest.get("worker_id"), "pid": latest.get("pid")})

    missing = [
        {"role": role, "expected": count, "running": running_by_role.get(role, 0)}
        for role, count in expected.items()
        if count > 0 and running_by_role.get(role, 0) < count
    ]
    status_payload = runtime.status()
    stale_jobs = runtime.stale_running_jobs(config.health.stale_job_seconds)
    failed_jobs = int(status_payload["jobs"].get("failed", 0))
    quotes = runtime.quote_snapshot_status(limit=50)
    stale_quotes = stale_quote_symbols(quotes, config.health.quote_stale_seconds)

    issues = []
    if missing:
        issues.append(f"missing_workers={len(missing)}")
    if dead_pids:
        issues.append(f"dead_pids={len(dead_pids)}")
    if stale_jobs:
        issues.append(f"stale_jobs={len(stale_jobs)}")
    if failed_jobs > config.health.max_failed_jobs:
        issues.append(f"failed_jobs={failed_jobs}")
    level = "ok"
    if missing or dead_pids:
        level = "unhealthy"
    elif stale_jobs or failed_jobs > config.health.max_failed_jobs:
        level = "degraded"
    summary = "系统健康：正常" if level == "ok" else f"系统健康：{level}; " + ", ".join(issues)
    details = {
        "expected_workers": expected,
        "running_by_role": running_by_role,
        "stale_by_role": stale_by_role,
        "stale_after_seconds": stale_after,
        "current_workers": {
            role: [
                {"worker_id": row.get("worker_id"), "pid": row.get("pid"), "status": row.get("status"), "last_seen_at": row.get("last_seen_at")}
                for row in live_by_role.get(role, [])[: count or 1]
            ]
            for role, count in expected.items()
        },
        "missing": missing,
        "dead_pids": dead_pids,
        "job_counts": status_payload["jobs"],
        "stale_jobs": [{"id": job.id, "type": job.type, "locked_by": job.locked_by, "locked_at": job.locked_at} for job in stale_jobs],
        "quote_snapshots": len(quotes),
        "stale_quotes": stale_quotes,
        "profile_runs": status_payload.get("profile_runs", 0),
        "health_checks": status_payload.get("health_checks", 0),
    }
    runtime.record_health(level, summary, details)
    runtime.record_log(level.upper() if level != "ok" else "INFO", "health", worker_id, summary, details)
    if level != "ok" and not dry_run and config.health.alerts_enabled:
        key = f"health_alert:{level}:{','.join(sorted(issues))}"
        if runtime.check_and_touch_cooldown(key, cooldown_minutes(config.health.alert_cooldown_minutes)):
            build_notifier(config, dry_run=dry_run).send("Portfolio bot health", health_message(summary, details))
    return {"status": level, "summary": summary, "details": details}


def health_message(summary: str, details: dict[str, Any]) -> str:
    missing = details.get("missing") or []
    stale_jobs = details.get("stale_jobs") or []
    lines = [summary]
    if missing:
        lines.append(f"缺失 worker: {missing[:5]}")
    if stale_jobs:
        lines.append(f"疑似卡住 job: {stale_jobs[:5]}")
    lines.append(f"job 状态: {details.get('job_counts', {})}")
    return "\n".join(lines)


def stale_quote_symbols(rows: list[dict[str, Any]], stale_seconds: int) -> list[str]:
    if stale_seconds <= 0:
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - stale_seconds
    stale = []
    for row in rows:
        try:
            updated = datetime.fromisoformat(str(row["updated_at"])).timestamp()
        except (KeyError, ValueError):
            continue
        if updated < cutoff:
            stale.append(str(row["symbol"]))
    return stale


def cooldown_minutes(minutes: int):
    from datetime import timedelta

    return timedelta(minutes=max(1, minutes))


def worker_stale_after_seconds(config: BotConfig) -> int:
    role_periods = [
        config.workers.heartbeat_seconds * 3,
        config.monitor.realtime_poll_seconds * 3,
        config.monitor.deep_scan_seconds + config.workers.heartbeat_seconds * 2,
        config.health.check_seconds * 3,
    ]
    return max(60, *(int(value) for value in role_periods))


def expected_worker_counts(config: BotConfig) -> dict[str, int]:
    return {
        "orchestrator": max(0, config.workers.orchestrator_processes),
        "realtime": max(0, config.workers.realtime_processes),
        "news": max(0, config.workers.news_processes),
        "ai": max(0, config.workers.ai_processes),
        "report": max(0, config.workers.report_processes),
        "agent": max(0, config.workers.agent_processes),
        "strategy": max(0, config.workers.strategy_processes),
        "paper": max(0, config.workers.paper_processes),
        "backtest": max(0, config.workers.backtest_processes),
        "health": max(0, config.workers.health_processes),
        "maintenance": max(0, config.workers.maintenance_processes),
    }


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def result_size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set, dict, str)):
        return len(value)
    return 1


def _profile_scan_once(config: BotConfig, *, dry_run: bool) -> list[Any]:
    from ..monitor import PortfolioMonitor

    return PortfolioMonitor(config, dry_run=True if dry_run else False).scan_once(send_alerts=False)


def _profile_deep_scan(config: BotConfig, *, dry_run: bool) -> str:
    from ..monitor import PortfolioMonitor

    return PortfolioMonitor(config, dry_run=True if dry_run else False).deep_scan(send_report=False)


def _profile_report(config: BotConfig, *, dry_run: bool) -> str:
    from ..research import ResearchEngine

    return ResearchEngine(config).generate_daily_report(load_holdings(config.holdings_path), dry_run=True if dry_run else False)


def _paper_broker(config: BotConfig) -> PaperBroker:
    return PaperBroker(
        config.data_dir / config.paper.sqlite_path,
        config.paper.starting_cash,
        memory=MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled),
    )
