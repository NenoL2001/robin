from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .backtest import BacktestStore, default_demo_prices, format_backtest_result, load_prices_csv, run_equity_backtest, run_long_call_backtest
from .config import load_config
from .data_hub import DataHub
from .features import FeatureEngine
from .launchd import install_launchd
from .market.web_search import WebSearchService
from .memory import MemoryStore, OpenSourceMemoryBridge, format_memory, memory_path
from .monitor import PortfolioMonitor
from .notifications import EmailNotifier, IMessageNotifier, build_notifier
from .observability import observability_log_path, run_health_check, run_profile_suite, worker_stale_after_seconds
from .orchestrator import OrchestratorAgent, generate_strategy_review, run_code_iteration_review
from .paper import PaperBroker, position_to_dict
from .research import ResearchEngine
from .runtime import RuntimeStore, runtime_path
from .screenshot_import import import_screenshot
from .storage import load_holdings
from .supervisor import Supervisor
from .workers import run_worker
from .agents.harness import run_agent
from .agents.harness.guardrails import AgentGuardrails
from .agents.harness.memory import MemorySynthesizer


def main(argv: list[str] | None = None) -> None:
    argv = _normalize_global_config_arg(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="portfolio-bot")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run")
    run_p.add_argument("--dry-run", action="store_true")

    scan_p = sub.add_parser("scan-once")
    scan_p.add_argument("--dry-run", action="store_true")

    deep_p = sub.add_parser("deep-scan")
    deep_p.add_argument("--dry-run", action="store_true")

    report_p = sub.add_parser("report-now")
    report_p.add_argument("--dry-run", action="store_true")

    research_now_p = sub.add_parser("research-now")
    research_now_p.add_argument("symbols", nargs="*")
    research_now_p.add_argument("--days", type=int, default=3)
    research_now_p.add_argument("--force-refresh", action="store_true")
    research_now_p.add_argument("--send-email", action="store_true")
    research_now_p.add_argument("--format", choices=["readable", "legacy"], default="readable")
    research_now_p.add_argument("--dry-run", action="store_true")

    web_search_p = sub.add_parser("web-search")
    web_search_p.add_argument("query")
    web_search_p.add_argument("--symbols", nargs="*", default=[])
    web_search_p.add_argument("--dry-run", action="store_true")

    news_digest_p = sub.add_parser("news-digest-now")
    news_digest_p.add_argument("--symbols", nargs="*", default=[])
    news_digest_p.add_argument("--days", type=int, default=3)
    news_digest_p.add_argument("--dry-run", action="store_true")

    strategy_plan_p = sub.add_parser("strategy-plan-now")
    strategy_plan_p.add_argument("--symbols", nargs="*", default=[])
    strategy_plan_p.add_argument("--dry-run", action="store_true")

    strategy_scout_p = sub.add_parser("strategy-news-scout-now")
    strategy_scout_p.add_argument("--strategy", default="semiconductor_reversal")
    strategy_scout_p.add_argument("--symbols", nargs="*", default=[])
    strategy_scout_p.add_argument("--dry-run", action="store_true")

    deep_research_p = sub.add_parser("deep-research-now")
    deep_research_p.add_argument("symbol")
    deep_research_p.add_argument("--strategy", default="semiconductor_reversal")
    deep_research_p.add_argument("--dry-run", action="store_true")

    factor_iter_p = sub.add_parser("factor-iterate-now")
    factor_iter_p.add_argument("--dry-run", action="store_true")

    factor_validate_p = sub.add_parser("factor-validate-now")
    factor_validate_p.add_argument("--symbols", nargs="*", default=[])
    factor_validate_p.add_argument("--dry-run", action="store_true")

    bars_refresh_p = sub.add_parser("bars-refresh-now")
    bars_refresh_p.add_argument("--symbols", nargs="*", default=[])
    bars_refresh_p.add_argument("--dry-run", action="store_true")

    relation_discover_p = sub.add_parser("relation-discover-now")
    relation_discover_p.add_argument("--symbols", nargs="*", default=[])
    relation_discover_p.add_argument("--dry-run", action="store_true")

    evidence_rank_p = sub.add_parser("evidence-rank-now")
    evidence_rank_p.add_argument("--symbols", nargs="*", default=[])
    evidence_rank_p.add_argument("--days", type=int, default=5)
    evidence_rank_p.add_argument("--dry-run", action="store_true")

    report_verify_p = sub.add_parser("report-verify-now")
    report_verify_p.add_argument("--latest", action="store_true")
    report_verify_p.add_argument("--dry-run", action="store_true")

    factor_attr_p = sub.add_parser("factor-attribution-now")
    factor_attr_p.add_argument("--horizon", choices=["1d", "3d", "5d"], default="1d")
    factor_attr_p.add_argument("--dry-run", action="store_true")

    strategy_iter_p = sub.add_parser("strategy-iterate-now")
    strategy_iter_p.add_argument("--symbols", nargs="*", default=[])
    strategy_iter_p.add_argument("--dry-run", action="store_true")

    paper_execute_p = sub.add_parser("paper-execute-proposals-now")
    paper_execute_p.add_argument("--dry-run", action="store_true")

    strategy_roundtable_p = sub.add_parser("strategy-roundtable-now")
    strategy_roundtable_p.add_argument("--dry-run", action="store_true")

    import_p = sub.add_parser("import-screenshot")
    import_p.add_argument("image")
    import_p.add_argument("--out")
    import_p.add_argument("--confirm", action="store_true")

    msg_p = sub.add_parser("test-imessage")
    msg_p.add_argument("--message", default="portfolio bot test")

    email_p = sub.add_parser("test-email")
    email_p.add_argument("--subject", default="portfolio bot test")
    email_p.add_argument("--body", default="portfolio bot email test")

    sub.add_parser("validate-config")

    launchd_p = sub.add_parser("install-launchd")
    launchd_p.add_argument("--label", default="com.noah.portfolio-bot")

    memory_add_p = sub.add_parser("memory-add")
    memory_add_p.add_argument("content")
    memory_add_p.add_argument("--symbol", default="")
    memory_add_p.add_argument("--kind", default="manual")
    memory_add_p.add_argument("--importance", type=float, default=0.7)

    memory_search_p = sub.add_parser("memory-search")
    memory_search_p.add_argument("query")
    memory_search_p.add_argument("--symbol", default="")
    memory_search_p.add_argument("--limit", type=int, default=8)

    memory_recent_p = sub.add_parser("memory-recent")
    memory_recent_p.add_argument("--symbol", default="")
    memory_recent_p.add_argument("--kind", default="")
    memory_recent_p.add_argument("--limit", type=int, default=8)

    sub.add_parser("memory-status")

    paper_buy_p = sub.add_parser("paper-buy")
    add_paper_order_args(paper_buy_p)

    paper_sell_p = sub.add_parser("paper-sell")
    add_paper_order_args(paper_sell_p)

    paper_positions_p = sub.add_parser("paper-positions")
    paper_positions_p.add_argument("--json", action="store_true")

    sub.add_parser("paper-review")

    paper_curve_p = sub.add_parser("paper-equity-curve")
    paper_curve_p.add_argument("--limit", type=int, default=30)

    backtest_p = sub.add_parser("backtest")
    backtest_p.add_argument("--strategy-name", default="semiconductor_reversal")
    backtest_p.add_argument("--strategy-version", default="1.0.0")
    backtest_p.add_argument("--asset-type", choices=["equity", "option"], default="equity")
    backtest_p.add_argument("--prices-csv")
    backtest_p.add_argument("--prices", default="")
    backtest_p.add_argument("--strike", type=float)
    backtest_p.add_argument("--premium", type=float)
    backtest_p.add_argument("--contracts", type=int, default=1)

    backtest_report_p = sub.add_parser("backtest-report")
    backtest_report_p.add_argument("--strategy-name", default="")
    backtest_report_p.add_argument("--limit", type=int, default=8)

    worker_p = sub.add_parser("worker")
    worker_p.add_argument("role", choices=["orchestrator", "realtime", "news", "ai", "report", "agent", "strategy", "paper", "backtest", "health", "maintenance"])
    worker_p.add_argument("--once", action="store_true")
    worker_p.add_argument("--dry-run", action="store_true")

    jobs_p = sub.add_parser("jobs")
    jobs_p.add_argument("--status", default="")
    jobs_p.add_argument("--limit", type=int, default=50)

    profile_p = sub.add_parser("profile-suite")
    profile_p.add_argument("--iterations", type=int, default=3)
    profile_p.add_argument("--live", action="store_true")
    profile_p.add_argument("--json", action="store_true")

    profile_report_p = sub.add_parser("profile-report")
    profile_report_p.add_argument("--limit", type=int, default=200)
    profile_report_p.add_argument("--json", action="store_true")

    health_p = sub.add_parser("health-check")
    health_p.add_argument("--dry-run", action="store_true")
    health_p.add_argument("--json", action="store_true")
    health_p.add_argument("--compact", action="store_true")

    health_status_p = sub.add_parser("health-status")
    health_status_p.add_argument("--limit", type=int, default=10)

    runtime_logs_p = sub.add_parser("runtime-logs")
    runtime_logs_p.add_argument("--level", default="")
    runtime_logs_p.add_argument("--role", default="")
    runtime_logs_p.add_argument("--limit", type=int, default=50)

    features_p = sub.add_parser("features-compute")
    features_p.add_argument("symbols", nargs="+")
    features_p.add_argument("--backend", choices=["sync", "thread", "async", "process"], default="")
    features_p.add_argument("--dry-run", action="store_true")

    data_news_p = sub.add_parser("data-news")
    data_news_p.add_argument("symbols", nargs="+")
    data_news_p.add_argument("--days", type=int, default=3)
    data_news_p.add_argument("--force-refresh", action="store_true")
    data_news_p.add_argument("--dry-run", action="store_true")

    metrics_p = sub.add_parser("metrics")
    metrics_sub = metrics_p.add_subparsers(dest="metrics_command", required=True)
    metrics_snapshot_p = metrics_sub.add_parser("snapshot")
    metrics_snapshot_p.add_argument("symbol")
    metrics_snapshot_p.add_argument("--name", default="feature_bundle")

    sub.add_parser("orchestrate-once")
    sub.add_parser("strategy-review-now")

    code_iter_p = sub.add_parser("code-iteration-review")
    code_iter_p.add_argument("--iterations", type=int, default=2)
    code_iter_p.add_argument("--dry-run", action="store_true")

    agent_run_p = sub.add_parser("agent-run")
    agent_run_p.add_argument("name")
    agent_run_p.add_argument("--objective", default="")
    agent_run_p.add_argument("--auto-patch", action="store_true")
    agent_run_p.add_argument("--apply", action="store_true")
    agent_run_p.add_argument("--dry-run", action="store_true")

    agent_status_p = sub.add_parser("agent-status")
    agent_status_p.add_argument("--limit", type=int, default=10)

    agent_trace_p = sub.add_parser("agent-trace")
    agent_trace_p.add_argument("run_id", type=int)

    agent_memory_p = sub.add_parser("agent-memory-synthesize")
    agent_memory_p.add_argument("query")
    agent_memory_p.add_argument("--symbol", action="append", default=[])
    agent_memory_p.add_argument("--strategy", default="")
    agent_memory_p.add_argument("--limit", type=int, default=8)

    sub.add_parser("agent-doctor")

    agent_review_p = sub.add_parser("agent-review")
    agent_review_p.add_argument("run_id", type=int)

    sub.add_parser("workers-status")
    sub.add_parser("runtime-status")

    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.command == "run":
        if config.workers.enabled:
            Supervisor(config, Path(args.config), dry_run=args.dry_run).run_forever()
        else:
            PortfolioMonitor(config, dry_run=args.dry_run).run_forever()
    elif args.command == "scan-once":
        events = PortfolioMonitor(config, dry_run=args.dry_run).scan_once(send_alerts=not args.dry_run)
        print(json.dumps([event.message for event in events], indent=2))
    elif args.command == "deep-scan":
        print(PortfolioMonitor(config, dry_run=args.dry_run).deep_scan(send_report=False))
    elif args.command == "report-now":
        holdings = load_holdings(config.holdings_path)
        report = ResearchEngine(config).generate_daily_report(holdings, dry_run=args.dry_run)
        print(report)
    elif args.command == "research-now":
        holdings = load_holdings(config.holdings_path)
        brief = ResearchEngine(config).generate_research_brief(
            args.symbols,
            holdings=holdings,
            days=args.days,
            dry_run=args.dry_run,
            force_refresh=args.force_refresh,
        )
        print(brief)
        if args.send_email:
            build_notifier(config, dry_run=args.dry_run).send("Portfolio active research brief", brief)
    elif args.command == "web-search":
        results = WebSearchService(config).search(args.query, symbols=args.symbols, commit=not args.dry_run)
        print(json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2, default=str))
    elif args.command == "news-digest-now":
        holdings = load_holdings(config.holdings_path)
        symbols = args.symbols or sorted(research_symbols_for_cli(holdings) | set(config.research.default_universe))
        digest = ResearchEngine(config).generate_daily_digest(symbols, days=args.days, dry_run=args.dry_run, include_web=True)
        print(digest.summary)
    elif args.command == "strategy-plan-now":
        holdings = load_holdings(config.holdings_path)
        plan = ResearchEngine(config).generate_strategy_plan(args.symbols or None, holdings=holdings, dry_run=args.dry_run)
        print(plan["summary"])
        if plan.get("paper_order_proposals"):
            print(json.dumps(plan["paper_order_proposals"], ensure_ascii=False, indent=2, default=str))
        if plan.get("paper_order_jobs"):
            print(json.dumps(plan["paper_order_jobs"], ensure_ascii=False, indent=2, default=str))
    elif args.command == "strategy-news-scout-now":
        holdings = load_holdings(config.holdings_path)
        symbols = args.symbols or sorted(research_symbols_for_cli(holdings) | set(config.research.default_universe))
        result = ResearchEngine(config).scout_strategy_news(symbols, strategy_name=args.strategy, dry_run=args.dry_run, deep=True)
        print(result.summary())
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    elif args.command == "deep-research-now":
        result = ResearchEngine(config).scout_strategy_news([args.symbol], strategy_name=args.strategy, dry_run=args.dry_run, deep=True)
        print(result.summary())
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    elif args.command == "factor-iterate-now":
        result = ResearchEngine(config).iterate_strategy_factors(dry_run=args.dry_run)
        print(result.summary)
    elif args.command == "factor-validate-now":
        holdings = load_holdings(config.holdings_path)
        symbols = args.symbols or sorted(research_symbols_for_cli(holdings) | set(config.research.default_universe))
        result = ResearchEngine(config).validate_strategy_factor_flow(symbols, holdings=holdings, dry_run=args.dry_run)
        print(result["validation_summary"])
        print(json.dumps(result["validation"], ensure_ascii=False, indent=2, default=str))
    elif args.command == "bars-refresh-now":
        holdings = load_holdings(config.holdings_path)
        symbols = args.symbols or sorted(research_symbols_for_cli(holdings) | set(config.research.default_universe))
        result = ResearchEngine(config).refresh_bars(symbols, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif args.command == "relation-discover-now":
        holdings = load_holdings(config.holdings_path)
        symbols = args.symbols or sorted(research_symbols_for_cli(holdings) | set(config.research.default_universe))
        result = ResearchEngine(config).discover_relations(symbols, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif args.command == "evidence-rank-now":
        holdings = load_holdings(config.holdings_path)
        symbols = args.symbols or sorted(research_symbols_for_cli(holdings) | set(config.research.default_universe))
        engine = ResearchEngine(config)
        digest = engine.generate_daily_digest(symbols, days=args.days, dry_run=args.dry_run, include_web=not args.dry_run)
        ranked = engine.evidence_ranker.rank_news(digest.items, symbols, commit=not args.dry_run)
        print(json.dumps([item.to_dict() for item in ranked], ensure_ascii=False, indent=2, default=str))
    elif args.command == "report-verify-now":
        engine = ResearchEngine(config)
        holdings = load_holdings(config.holdings_path)
        memory = memory_store(config)
        recent = memory.recent(kind="daily_review", limit=1)
        if not recent:
            print("no latest daily_review memory found")
        else:
            symbols = [part.strip().upper() for part in (recent[0].symbol or "").split(",") if part.strip()]
            relationships = engine.relation_graph.relationships_for(symbols, min_confidence=config.relation_graph.min_confidence) if engine.relation_graph else []
            result = engine.report_verifier.verify(recent[0].content, holdings, relationships, query_log=[], commit=not args.dry_run)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    elif args.command == "factor-attribution-now":
        engine = ResearchEngine(config)
        symbols = engine.factor_attribution.symbols_with_open_attribution(horizon=args.horizon)
        quotes = engine.data_hub.quotes(symbols, commit=not args.dry_run) if symbols else {}
        updated = 0 if args.dry_run else engine.factor_attribution.update_forward_returns(quotes, horizon=args.horizon, remember=True)
        summary = engine.factor_attribution.summary(horizon=args.horizon, min_observations=1)
        print(json.dumps({"horizon": args.horizon, "symbols": symbols, "updated": updated, "summary": [item.to_dict() for item in summary]}, ensure_ascii=False, indent=2, default=str))
    elif args.command == "strategy-iterate-now":
        engine = ResearchEngine(config)
        factor_result = engine.iterate_strategy_factors(dry_run=args.dry_run)
        holdings = load_holdings(config.holdings_path)
        plan = engine.generate_strategy_plan(args.symbols or None, holdings=holdings, dry_run=args.dry_run)
        print(factor_result.summary)
        print()
        print(plan["summary"])
    elif args.command == "paper-execute-proposals-now":
        if args.dry_run:
            print("dry-run: paper proposals are enqueued by strategy-plan-now when non-dry-run and risk gate passes.")
        else:
            holdings = load_holdings(config.holdings_path)
            plan = ResearchEngine(config).generate_strategy_plan(None, holdings=holdings, dry_run=False)
            print(json.dumps(plan.get("paper_order_jobs", []), ensure_ascii=False, indent=2, default=str))
    elif args.command == "strategy-roundtable-now":
        print(ResearchEngine(config).generate_strategy_roundtable(dry_run=args.dry_run))
    elif args.command == "import-screenshot":
        output = import_screenshot(config, Path(args.image), Path(args.out) if args.out else None, confirm=args.confirm)
        print(f"wrote import result to {output}")
    elif args.command == "test-imessage":
        result = IMessageNotifier(config.imessage_recipient, config=config).send("portfolio bot test", args.message)
        print(result)
    elif args.command == "test-email":
        result = EmailNotifier(config).send(args.subject, args.body)
        print(result)
    elif args.command == "validate-config":
        validate_config(config)
    elif args.command == "install-launchd":
        target = install_launchd(Path(args.config), Path.cwd(), label=args.label)
        print(f"installed {target}")
    elif args.command == "memory-add":
        memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
        rowid = memory.add(args.kind, args.content, symbol=args.symbol, importance=args.importance, source="manual")
        print(f"memory_id={rowid}")
    elif args.command == "memory-search":
        memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
        for record in memory.search(args.query, symbol=args.symbol, limit=args.limit):
            print(format_memory(record))
    elif args.command == "memory-recent":
        memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
        for record in memory.recent(symbol=args.symbol, kind=args.kind, limit=args.limit):
            print(format_memory(record))
    elif args.command == "memory-status":
        memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
        bridge = OpenSourceMemoryBridge(config.memory.open_source_enabled, config.memory.open_source_backend)
        print(f"sqlite_memory: {'enabled' if config.memory.enabled else 'disabled'} count={memory.count()} path={memory.path}")
        print(f"open_source_memory: {bridge.status()}")
    elif args.command == "paper-buy":
        broker = paper_broker(config)
        order = broker.buy(**paper_order_kwargs(args))
        print(json.dumps(asdict(order), ensure_ascii=False, indent=2))
    elif args.command == "paper-sell":
        broker = paper_broker(config)
        order = broker.sell(**paper_order_kwargs(args))
        print(json.dumps(asdict(order), ensure_ascii=False, indent=2))
    elif args.command == "paper-positions":
        broker = paper_broker(config)
        snapshot = broker.snapshot()
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        else:
            print(f"现金: ${snapshot['cash']:.2f}")
            print(f"持仓市值: ${snapshot['market_value']:.2f}")
            print(f"模拟净值: ${snapshot['equity']:.2f}")
            for position in broker.positions():
                row = position_to_dict(position)
                print(
                    f"- {position.symbol}: {position.quantity:g} {position.asset_type}, "
                    f"成本 ${row['cost_basis']:.2f}, 市值 ${row['market_value']:.2f}, 策略 {position.strategy_name}"
                )
    elif args.command == "paper-review":
        print(paper_broker(config).review())
    elif args.command == "paper-equity-curve":
        rows = paper_broker(config).equity_curve(limit=args.limit)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif args.command == "backtest":
        prices = parse_prices(args)
        source = price_source(args)
        if args.asset_type == "option":
            if args.strike is None or args.premium is None:
                raise SystemExit("option backtest requires --strike and --premium")
            result = run_long_call_backtest(
                prices,
                strike=args.strike,
                premium=args.premium,
                contracts=args.contracts,
                strategy_name=args.strategy_name,
                strategy_version=args.strategy_version,
            )
        else:
            result = run_equity_backtest(
                prices,
                strategy_name=args.strategy_name,
                strategy_version=args.strategy_version,
                slippage_bps=config.backtest.default_slippage_bps,
                commission=config.backtest.default_commission,
            )
        result.metadata["price_source"] = source
        backtest_store(config).save(result)
        print(format_backtest_result(result))
    elif args.command == "backtest-report":
        for result in backtest_store(config).recent(strategy_name=args.strategy_name, limit=args.limit):
            print(format_backtest_result(result))
    elif args.command == "worker":
        run_worker(config, args.role, once=args.once, dry_run=args.dry_run)
    elif args.command == "jobs":
        for job in runtime_store(config).jobs(status=args.status, limit=args.limit):
            print(json.dumps(asdict(job), ensure_ascii=False, indent=2))
    elif args.command == "profile-suite":
        result = run_profile_suite(config, iterations=args.iterations, dry_run=not args.live)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print("Profile suite 完成。摘要:")
            print_profile_summary(result["summary"])
    elif args.command == "profile-report":
        summary = runtime_store(config).profile_summary(limit=args.limit)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print_profile_summary(summary)
    elif args.command == "health-check":
        result = run_health_check(config, "cli-health-check", dry_run=args.dry_run)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(result["summary"])
            details = result["details"]
            if args.compact:
                print(f"workers: {compact_worker_summary(details)}")
            else:
                print(f"workers: {details['running_by_role']}")
            print(f"jobs: {details['job_counts']}")
            if details["missing"]:
                print(f"missing: {details['missing']}")
    elif args.command == "health-status":
        for row in runtime_store(config).recent_health(limit=args.limit):
            print(json.dumps(row, ensure_ascii=False, indent=2))
    elif args.command == "runtime-logs":
        for row in runtime_store(config).recent_logs(level=args.level, role=args.role, limit=args.limit):
            print(json.dumps(row, ensure_ascii=False, indent=2))
    elif args.command == "features-compute":
        symbols = [symbol.upper() for symbol in args.symbols]
        engine = ResearchEngine(config)
        hub = engine.data_hub
        quotes = hub.quotes(symbols, commit=not args.dry_run)
        if engine.bar_store:
            engine.bar_store.refresh_from_quotes(quotes, commit=not args.dry_run)
        news = hub.collect_news(symbols, commit=not args.dry_run)
        backend = args.backend or config.metrics.backend
        features = FeatureEngine(runtime_store(config), backend=backend, max_workers=config.metrics.max_workers, bar_store=engine.bar_store).compute_many(
            symbols,
            quotes,
            news,
            holdings=load_holdings(config.holdings_path),
            commit=not args.dry_run,
            backend=backend,
        )
        print(json.dumps(features, ensure_ascii=False, indent=2, default=str))
    elif args.command == "data-news":
        news = DataHub(config).collect_news(args.symbols, days=args.days, commit=not args.dry_run, force_refresh=args.force_refresh)
        for item in news:
            print(json.dumps({"title": item.title, "source": item.source, "symbols": item.symbols, "url": item.url}, ensure_ascii=False))
    elif args.command == "metrics":
        if args.metrics_command == "snapshot":
            row = runtime_store(config).latest_metric_snapshot(args.symbol, args.name)
            print(json.dumps(row, ensure_ascii=False, indent=2, default=str))
    elif args.command == "orchestrate-once":
        print(json.dumps(OrchestratorAgent(config).schedule_once(), ensure_ascii=False, indent=2))
    elif args.command == "strategy-review-now":
        print(generate_strategy_review(config))
    elif args.command == "code-iteration-review":
        print(run_code_iteration_review(config, iterations=args.iterations, dry_run=args.dry_run))
    elif args.command == "agent-run":
        objective = args.objective
        if args.auto_patch:
            objective = objective or "运行自动补丁候选 workflow，必须受 guardrail、测试和回滚约束。"
        result = run_agent(config, args.name, objective, dry_run=args.dry_run or not args.apply)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str))
    elif args.command == "agent-status":
        rows = [compact_agent_run(row) for row in runtime_store(config).recent_agent_runs(limit=args.limit)]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif args.command == "agent-trace":
        print(json.dumps(runtime_store(config).agent_trace(args.run_id), ensure_ascii=False, indent=2))
    elif args.command == "agent-memory-synthesize":
        runtime = runtime_store(config)
        memory = memory_store(config)
        bundle = MemorySynthesizer(config.root, runtime, memory).synthesize(
            args.query,
            symbols=args.symbol,
            strategy=args.strategy,
            limit=args.limit,
        )
        print(json.dumps(asdict(bundle), ensure_ascii=False, indent=2, default=str))
    elif args.command == "agent-doctor":
        guardrail = AgentGuardrails(config.root)
        result = {
            "agent_harness_enabled": config.agent_harness.enabled,
            "runtime": runtime_store(config).status(),
            "policy": {
                "auto_source_edits": guardrail.policy.get("auto_source_edits"),
                "blocked_paths": guardrail.policy.get("blocked_paths", []),
                "blocked_changes": guardrail.policy.get("blocked_changes", []),
            },
            "engine": config.agent_harness.engine,
            "auto_patch_enabled": config.agent_harness.auto_patch_enabled,
            "auto_restart_after_patch": config.agent_harness.auto_restart_after_patch,
            "guardrails": {
                "no_real_trading": True,
                "no_secret_output": True,
                "code_patch_policy_bound": True,
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "agent-review":
        trace = runtime_store(config).agent_trace(args.run_id)
        print(render_agent_review(trace))
    elif args.command == "workers-status":
        stale_after = worker_stale_after_seconds(config)
        print(json.dumps(runtime_store(config).worker_statuses(stale_after_seconds=stale_after), ensure_ascii=False, indent=2))
    elif args.command == "runtime-status":
        print(json.dumps(runtime_store(config).status(), ensure_ascii=False, indent=2))


def _normalize_global_config_arg(argv: list[str]) -> list[str]:
    """Allow --config before or after the subcommand."""
    tokens = list(argv)
    for index, token in enumerate(tokens):
        if token == "--config" and index + 1 < len(tokens):
            value = tokens[index + 1]
            del tokens[index : index + 2]
            return ["--config", value] + tokens
        if token.startswith("--config="):
            value = token.split("=", 1)[1]
            del tokens[index]
            return ["--config", value] + tokens
    return tokens


def add_paper_order_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbol")
    parser.add_argument("--asset-type", choices=["equity", "etf", "option", "crypto"], default="equity")
    parser.add_argument("--quantity", type=float, required=True)
    parser.add_argument("--price", type=float, required=True)
    parser.add_argument("--strategy-name", required=True)
    parser.add_argument("--strategy-version", default="1.0.0")
    parser.add_argument("--signal-id", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--memory-context", default="")
    parser.add_argument("--multiplier", type=float)
    parser.add_argument("--commission", type=float, default=0.0)


def paper_order_kwargs(args: argparse.Namespace) -> dict:
    return {
        "symbol": args.symbol,
        "asset_type": args.asset_type,
        "quantity": args.quantity,
        "price": args.price,
        "strategy_name": args.strategy_name,
        "strategy_version": args.strategy_version,
        "signal_id": args.signal_id,
        "reason": args.reason,
        "memory_context": args.memory_context,
        "multiplier": args.multiplier,
        "commission": args.commission,
    }


def memory_store(config) -> MemoryStore:
    return MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)


def paper_broker(config) -> PaperBroker:
    return PaperBroker(config.data_dir / config.paper.sqlite_path, config.paper.starting_cash, memory=memory_store(config))


def backtest_store(config) -> BacktestStore:
    return BacktestStore(config.data_dir / config.backtest.sqlite_path, memory=memory_store(config))


def runtime_store(config) -> RuntimeStore:
    return RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))


def research_symbols_for_cli(holdings) -> set[str]:
    symbols: set[str] = set()
    for holding in holdings:
        if holding.asset_type == "option":
            underlying = str(holding.metadata.get("underlying", "")).strip().upper()
            if underlying:
                symbols.add(underlying)
        elif holding.asset_type in {"equity", "etf"}:
            symbols.add(holding.normalized_symbol())
    return symbols


def parse_prices(args: argparse.Namespace) -> list[float]:
    if args.prices_csv:
        return load_prices_csv(Path(args.prices_csv).expanduser())
    if args.prices:
        return [float(part.strip()) for part in args.prices.split(",") if part.strip()]
    return default_demo_prices()


def price_source(args: argparse.Namespace) -> str:
    if args.prices_csv:
        return f"csv:{args.prices_csv}"
    if args.prices:
        return "inline"
    return "demo"


def validate_config(config) -> None:
    warnings = []
    if not config.llm.api_key:
        warnings.append(f"{config.llm.provider} LLM API key is missing")
    if not config.finnhub_api_key:
        warnings.append("FINNHUB_API_KEY is missing; live quotes/news will be empty")
    if not config.imessage_recipient and config.notifications.imessage_enabled:
        warnings.append("IMESSAGE_RECIPIENT is missing")
    if config.notifications.email_enabled and (not config.email_username or not config.email_app_password):
        warnings.append("email credentials are missing")
    print(f"config root: {config.root}")
    print(f"data dir: {config.data_dir}")
    print(f"holdings: {config.holdings_path}")
    print(f"llm provider: {config.llm.provider}")
    print(f"llm base url: {config.llm.base_url or 'provider default'}")
    print(f"monitor model: {config.llm.monitor_model}")
    print(f"event model: {config.llm.event_model}")
    print(f"memory: {'enabled' if config.memory.enabled else 'disabled'} ({config.data_dir / config.memory.sqlite_path})")
    print(f"runtime: {config.data_dir / config.runtime.sqlite_path}")
    print(f"logs: {observability_log_path(config)}")
    print(f"workers: {'enabled' if config.workers.enabled else 'disabled'}")
    print(f"health worker: {config.workers.health_processes} process(es), check every {config.health.check_seconds}s")
    print(f"metrics backend: {config.metrics.backend} ({config.metrics.max_workers} workers)")
    print(f"agent harness: {config.agent_harness.engine}, auto_patch={config.agent_harness.auto_patch_enabled}")
    for warning in warnings:
        print(f"warning: {warning}")


def print_profile_summary(summary: list[dict]) -> None:
    if not summary:
        print("暂无 profile 数据。")
        return
    for row in summary:
        print(
            f"- {row['operation']}: runs={row['runs']} ok={row['ok']} failed={row['failed']} "
            f"avg={row['avg_ms']}ms max={row['max_ms']}ms cpu_avg={row['avg_cpu_ms']}ms peak={row['max_peak_kb']}KB"
        )
        if row.get("last_error"):
            print(f"  last_error={row['last_error']}")


def render_agent_review(trace: dict) -> str:
    if not trace:
        return "未找到该 Agent run。"
    run = trace.get("run", {})
    tools = trace.get("tool_calls", [])
    reflections = trace.get("reflections", [])
    failed_tools = [tool for tool in tools if tool.get("status") != "done"]
    lines = [
        "Agent Review",
        f"- run_id: {run.get('id')}",
        f"- agent: {run.get('agent_name')}",
        f"- status: {run.get('status')}",
        f"- objective: {run.get('objective')}",
        f"- tool_calls: {len(tools)}",
        f"- failed_or_blocked_tools: {len(failed_tools)}",
    ]
    if reflections:
        lines.append(f"- reflection: {reflections[-1].get('content')}")
    if failed_tools:
        lines.append("## Failed/Blocked Tools")
        for tool in failed_tools[:8]:
            lines.append(f"- {tool.get('tool_name')}: {tool.get('status')} {tool.get('error', '')}")
    return "\n".join(lines)


def compact_agent_run(row: dict) -> dict:
    result = row.get("result") or {}
    return {
        "id": row.get("id"),
        "agent_name": row.get("agent_name"),
        "role": row.get("role"),
        "objective": row.get("objective"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "error": row.get("error", ""),
        "verification": result.get("verification", {}),
        "reflection_summary": (result.get("reflection") or {}).get("summary", ""),
    }


def compact_worker_summary(details: dict) -> dict:
    workers = details.get("current_workers") or {}
    return {
        role: {
            "running": (details.get("running_by_role") or {}).get(role, 0),
            "expected": (details.get("expected_workers") or {}).get(role, 0),
            "pids": [row.get("pid") for row in rows],
        }
        for role, rows in workers.items()
    }
