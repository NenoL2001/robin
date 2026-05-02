from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..backtest import BacktestStore, run_equity_backtest, run_long_call_backtest
from ..config import BotConfig
from ..data_hub import DataHub
from ..data.x_api import XApiClient
from ..memory import MemoryStore, memory_path
from ..models import MarketEvent, NewsItem
from ..monitor import LLM_ANALYSIS_BACKOFF, LLM_ANALYSIS_BACKOFF_KEY, PortfolioMonitor, holding_alert_symbols, select_high_impact_news
from ..notifications import build_notifier
from .observability import profile_operation, run_health_check, setup_logging
from ..openai_client import OpenAIService
from .orchestrator import OrchestratorAgent, process_maintenance_jobs, process_strategy_jobs
from .harness import run_agent
from ..paper import PaperBroker
from ..research import ResearchEngine
from ..runtime import RuntimeStore, event_idempotency_key, runtime_path
from ..storage import load_analyst_config, load_holdings


def run_worker(config: BotConfig, role: str, *, once: bool = False, dry_run: bool = False) -> None:
    worker_id = f"{role}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    logger = setup_logging(config, role, worker_id)
    runtime.record_log("INFO", role, worker_id, "worker started", {"once": once, "dry_run": dry_run})
    last_profile_at = 0.0
    try:
        while True:
            runtime.heartbeat(worker_id, role, os.getpid())
            sleep_seconds = 5
            try:
                should_profile = once or (time.time() - last_profile_at >= max(1, config.logging.profile_sample_seconds))
                if should_profile:
                    sleep_seconds = profile_operation(
                        runtime,
                        f"worker.{role}.loop",
                        role,
                        worker_id,
                        lambda: run_worker_once(config, role, worker_id, dry_run=dry_run),
                        metadata={"once": once, "dry_run": dry_run},
                    )
                    last_profile_at = time.time()
                else:
                    sleep_seconds = run_worker_once(config, role, worker_id, dry_run=dry_run)
                if should_profile:
                    logger.info("worker loop complete", extra={"role": role, "worker_id": worker_id, "operation": f"worker.{role}.loop"})
            except Exception as exc:
                logger.exception("worker loop failed", extra={"role": role, "worker_id": worker_id, "operation": f"worker.{role}.loop"})
                runtime.record_log("ERROR", role, worker_id, "worker loop failed", {"error": str(exc)})
                if once:
                    raise
            if once:
                return
            time.sleep(sleep_seconds)
    finally:
        runtime.heartbeat(worker_id, role, os.getpid(), status="stopped" if once else "exiting")
        runtime.record_log("INFO", role, worker_id, "worker stopped", {"once": once})


def run_worker_once(config: BotConfig, role: str, worker_id: str, *, dry_run: bool = False) -> int:
    if role == "orchestrator":
        OrchestratorAgent(config).schedule_once()
        return config.orchestration.tick_seconds
    if role == "realtime":
        PortfolioMonitor(config, dry_run=dry_run).scan_once(send_alerts=not dry_run)
        return config.monitor.realtime_poll_seconds
    if role == "news":
        process_news_jobs(config, worker_id, dry_run=dry_run)
        return config.monitor.deep_scan_seconds
    if role == "ai":
        process_ai_jobs(config, worker_id, dry_run=dry_run)
        return 5
    if role == "report":
        process_report_jobs(config, worker_id, dry_run=dry_run)
        return 30
    if role == "agent":
        process_agent_jobs(config, worker_id, dry_run=dry_run)
        return 15
    if role == "strategy":
        process_strategy_jobs(config, worker_id)
        return 30
    if role == "paper":
        process_paper_jobs(config, worker_id)
        return 5
    if role == "backtest":
        process_backtest_jobs(config, worker_id)
        return 10
    if role == "health":
        run_health_check(config, worker_id, dry_run=dry_run)
        return config.health.check_seconds
    if role == "maintenance":
        process_maintenance_jobs(config, worker_id)
        return 30
    raise ValueError(f"unknown worker role: {role}")


def process_news_jobs(config: BotConfig, worker_id: str, *, dry_run: bool = False) -> bool:
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    job = runtime.claim_job(worker_id, ["news_scan"])
    if not job:
        asyncio.run(news_scan_once(config, worker_id, dry_run=dry_run))
        return False
    try:
        result = asyncio.run(news_scan_once(config, worker_id, dry_run=dry_run))
        runtime.complete_job(job.id, {"result": result})
        return True
    except Exception as exc:
        runtime.fail_job(job.id, str(exc), retry=True, delay_seconds=120)
        return False


def process_report_jobs(config: BotConfig, worker_id: str, *, dry_run: bool = False) -> bool:
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    job = runtime.claim_job(worker_id, ["daily_report"])
    monitor = PortfolioMonitor(config, dry_run=dry_run)
    if not job:
        monitor.maybe_send_daily_report()
        return False
    try:
        report = monitor.deep_scan(send_report=True)
        runtime.complete_job(job.id, {"chars": len(report)})
        return True
    except Exception as exc:
        runtime.fail_job(job.id, str(exc), retry=True, delay_seconds=300)
        return False


def process_agent_jobs(config: BotConfig, worker_id: str, *, dry_run: bool = False) -> bool:
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    job = runtime.claim_job(worker_id, ["agent_run"])
    if not job:
        return False
    try:
        agent_name = str(job.payload.get("agent_name", "operator_agent"))
        objective = str(job.payload.get("objective", ""))
        result = run_agent(config, agent_name, objective, dry_run=bool(job.payload.get("dry_run", True)) or dry_run)
        if result.status == "done":
            runtime.complete_job(job.id, {"run_id": result.run_id, "status": result.status})
        else:
            runtime.fail_job(job.id, result.reflection.get("summary", result.status), retry=False)
        return True
    except Exception as exc:
        runtime.fail_job(job.id, str(exc), retry=True, delay_seconds=120)
        return False


async def news_scan_once(config: BotConfig, worker_id: str, *, dry_run: bool = False) -> str:
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    runtime.heartbeat(worker_id, "news", os.getpid())
    holdings = load_holdings(config.holdings_path)
    alert_symbols = holding_alert_symbols(holdings)
    symbols = sorted(alert_symbols | set(config.research.default_universe))
    news = await DataHub(config, runtime=runtime).collect_news_async(symbols, days=3, commit=not dry_run, fresh_only=not dry_run)
    high_impact = select_high_impact_news(news, max_items=5, symbols=alert_symbols)
    monitor = PortfolioMonitor(config, dry_run=dry_run)
    events: list[MarketEvent] = []
    for item in high_impact:
        for symbol in (item.symbols or [])[:3]:
            events.append(
                MarketEvent(
                    symbol=symbol,
                    event_type="high_impact_news",
                    severity="high",
                    message=f"High-impact lead for {symbol}: {item.title}",
                    news=item,
                )
            )
    events = monitor._apply_cooldown(events, commit=not dry_run)
    if dry_run:
        for event in events:
            print(f"[dry-run:high-impact] {event.message}")
    else:
        for event in events:
            monitor.handle_event(event, send_major_email=False)
        monitor.send_major_event_email_batch(events)
    return f"news scan complete; fresh_news={len(news)} high_impact={len(high_impact)} alerts={len(events)}"


async def collect_news_async(config: BotConfig, symbols: list[str], *, commit: bool = True) -> list[NewsItem]:
    items: list[NewsItem] = []
    if config.finnhub_api_key:
        items.extend(await fetch_finnhub_company_news_async(config, symbols))
    analyst_config = load_analyst_config(config.analysts_path)
    x_posts = await asyncio.to_thread(XApiClient(config.x_bearer_token).recent_semiconductor_posts, analyst_config)
    items.extend(x_posts)
    if not commit:
        return items
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    fresh_keys = runtime.filter_fresh_news_keys({item.dedupe_key() for item in items}, commit=True)
    fresh = [item for item in items if item.dedupe_key() in fresh_keys]
    memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
    for item in fresh:
        memory.add(
            "news_lead",
            f"{item.source}: {item.title} {item.summary}".strip(),
            symbol=item.symbols[0].upper() if item.symbols else "",
            importance=0.7 if item.kind == "x_post" else 0.6,
            confidence=0.35 if item.kind == "x_post" else 0.6,
            source=item.source,
            metadata={"url": item.url, "symbols": ",".join(sorted(set(item.symbols))), "kind": item.kind},
        )
    return fresh


async def fetch_finnhub_company_news_async(config: BotConfig, symbols: list[str]) -> list[NewsItem]:
    try:
        import httpx  # type: ignore
    except Exception:
        engine = ResearchEngine(config)
        return await asyncio.to_thread(engine.collect_news, symbols, 3, False)
    start = (date.today() - timedelta(days=3)).isoformat()
    end = date.today().isoformat()
    semaphore = asyncio.Semaphore(max(1, config.rate_limits.finnhub_concurrency))
    async with httpx.AsyncClient(base_url="https://finnhub.io/api/v1", timeout=config.rate_limits.news_timeout_seconds) as client:
        tasks = [fetch_one_finnhub_symbol(client, semaphore, config.finnhub_api_key, symbol, start, end) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    items: list[NewsItem] = []
    for result in results:
        if isinstance(result, list):
            items.extend(result)
    return items


async def fetch_one_finnhub_symbol(client, semaphore: asyncio.Semaphore, api_key: str, symbol: str, start: str, end: str) -> list[NewsItem]:
    async with semaphore:
        response = await client.get("/company-news", params={"symbol": symbol.upper(), "from": start, "to": end, "token": api_key})
        response.raise_for_status()
        rows = response.json()
    if not isinstance(rows, list):
        return []
    items: list[NewsItem] = []
    for row in rows:
        ts = row.get("datetime")
        published = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        items.append(
            NewsItem(
                title=str(row.get("headline", "")),
                url=str(row.get("url", "")),
                source=str(row.get("source", "Finnhub")),
                published_at=published,
                symbols=[symbol.upper()],
                summary=str(row.get("summary", "")),
                kind="company_news",
                raw=row,
            )
        )
    return items


def process_ai_jobs(config: BotConfig, worker_id: str, *, dry_run: bool = False) -> None:
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    job = runtime.claim_job(worker_id, ["major_event_analysis"])
    if not job:
        return
    key = str(job.payload.get("idempotency_key", job.idempotency_key))
    event = event_from_payload(job.payload.get("event", {}))
    memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
    try:
        if dry_run:
            analysis = f"dry-run analysis for {event.symbol}: {event.message}"
        else:
            analysis = OpenAIService(config).analyze_event(event, [event.news] if event.news else [], str(job.payload.get("memory_context", "")))
        memory.add(
            "event_analysis",
            analysis,
            symbol=event.symbol,
            importance=0.9,
            confidence=0.65,
            source=config.llm.provider if not dry_run else "dry_run",
            metadata={"event_type": event.event_type, "idempotency_key": key, "llm_provider": config.llm.provider},
        )
        build_notifier(config, dry_run=dry_run).send(f"Portfolio deep analysis: {event.symbol}", analysis)
        runtime.update_market_event_ai_status(key, "done")
        runtime.complete_job(job.id, {"analysis": analysis[:500]})
    except Exception as exc:
        if is_llm_backoff_error(exc):
            runtime.check_and_touch_cooldown(LLM_ANALYSIS_BACKOFF_KEY, LLM_ANALYSIS_BACKOFF, commit=True)
        memory.add(
            "event_analysis_error",
            f"{event.symbol} deep analysis failed: {exc}",
            symbol=event.symbol,
            importance=0.4,
            confidence=0.7,
            source=config.llm.provider,
            metadata={"event_type": event.event_type, "idempotency_key": key, "llm_provider": config.llm.provider},
        )
        runtime.update_market_event_ai_status(key, "failed")
        runtime.fail_job(job.id, str(exc), retry=False)


def is_llm_backoff_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "rate_limit" in text or "insufficient_quota" in text or "exceeded your current quota" in text


def is_openai_backoff_error(exc: Exception) -> bool:
    return is_llm_backoff_error(exc)


def process_paper_jobs(config: BotConfig, worker_id: str) -> None:
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    job = runtime.claim_job(worker_id, ["paper_buy", "paper_sell", "paper_snapshot"])
    if not job:
        return
    broker = PaperBroker(config.data_dir / config.paper.sqlite_path, config.paper.starting_cash, memory=MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled))
    try:
        if job.type == "paper_buy":
            result = broker.buy(**job.payload)
            runtime.complete_job(job.id, {"order_id": result.order_id})
        elif job.type == "paper_sell":
            result = broker.sell(**job.payload)
            runtime.complete_job(job.id, {"order_id": result.order_id})
        else:
            snapshot = broker.snapshot()
            runtime.complete_job(job.id, {"equity": snapshot["equity"]})
    except Exception as exc:
        runtime.fail_job(job.id, str(exc), retry=False)


def process_backtest_jobs(config: BotConfig, worker_id: str) -> None:
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    job = runtime.claim_job(worker_id, ["backtest"])
    if not job:
        return
    try:
        prices = [float(value) for value in job.payload.get("prices", [])]
        if job.payload.get("asset_type") == "option":
            result = run_long_call_backtest(
                prices,
                strike=float(job.payload["strike"]),
                premium=float(job.payload["premium"]),
                contracts=int(job.payload.get("contracts", 1)),
                strategy_name=str(job.payload.get("strategy_name", "semiconductor_reversal")),
                strategy_version=str(job.payload.get("strategy_version", "1.0.0")),
            )
        else:
            result = run_equity_backtest(
                prices,
                strategy_name=str(job.payload.get("strategy_name", "semiconductor_reversal")),
                strategy_version=str(job.payload.get("strategy_version", "1.0.0")),
                slippage_bps=config.backtest.default_slippage_bps,
                commission=config.backtest.default_commission,
            )
        BacktestStore(config.data_dir / config.backtest.sqlite_path, memory=MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)).save(result)
        runtime.complete_job(job.id, {"backtest_id": result.backtest_id, "total_return": result.total_return})
    except Exception as exc:
        runtime.fail_job(job.id, str(exc), retry=False)


def event_from_payload(payload: dict[str, Any]) -> MarketEvent:
    news_payload = payload.get("news")
    news = None
    if isinstance(news_payload, dict):
        news = NewsItem(
            title=str(news_payload.get("title", "")),
            url=str(news_payload.get("url", "")),
            source=str(news_payload.get("source", "")),
            symbols=list(news_payload.get("symbols", [])),
            summary=str(news_payload.get("summary", "")),
            kind=str(news_payload.get("kind", "news")),
        )
    return MarketEvent(
        symbol=str(payload.get("symbol", "")),
        event_type=str(payload.get("event_type", "")),
        severity=str(payload.get("severity", "high")),  # type: ignore[arg-type]
        message=str(payload.get("message", "")),
        news=news,
    )
