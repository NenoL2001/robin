from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(slots=True)
class ThresholdConfig:
    realtime_percent: float = 3.0
    intraday_percent: float = 5.0
    portfolio_percent: float = 2.0


@dataclass(slots=True)
class MonitorConfig:
    realtime_poll_seconds: int = 30
    deep_scan_seconds: int = 300
    report_time: str = "08:45"
    major_move_cooldown_minutes: int = 30
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)


@dataclass(slots=True)
class ResearchConfig:
    option_min_days: int = 180
    option_max_days: int = 548
    option_max_premium: float = 1500.0
    option_max_spread_percent: float = 35.0
    min_news_relevance: float = 0.55
    max_news_per_symbol: int = 3
    web_search_enabled: bool = True
    web_search_provider: str = "duckduckgo"
    web_search_api_provider: str = "tavily"
    web_search_max_results: int = 5
    web_search_timeout_seconds: int = 8
    default_universe: list[str] = field(default_factory=lambda: ["INTC", "AXTI", "AEHR", "POET"])
    leveraged_etfs: list[str] = field(default_factory=lambda: ["USD"])
    comparison_etfs: list[str] = field(default_factory=lambda: ["SOXL"])


@dataclass(slots=True)
class NotificationConfig:
    imessage_enabled: bool = True
    email_enabled: bool = False
    agentmail_enabled: bool = False
    agentmail_major_alerts_enabled: bool = True
    agentmail_market_hours_cooldown_minutes: int = 10
    agentmail_off_hours_cooldown_minutes: int = 45
    agentmail_off_hours_extreme_move_percent: float = 8.0
    batch_realtime_alerts: bool = True
    semantic_dedupe_enabled: bool = True
    dry_run: bool = False


@dataclass(slots=True)
class MemoryConfig:
    enabled: bool = True
    sqlite_path: str = "memory.sqlite"
    max_context_items: int = 8
    open_source_backend: str = "mem0"
    open_source_enabled: bool = False


@dataclass(slots=True)
class PaperConfig:
    sqlite_path: str = "paper_portfolio.sqlite"
    starting_cash: float = 100000.0


@dataclass(slots=True)
class BacktestConfig:
    sqlite_path: str = "backtest.sqlite"
    default_slippage_bps: float = 10.0
    default_commission: float = 0.0


@dataclass(slots=True)
class StrategyRiskConfig:
    max_paper_order_equity_pct: float = 0.02
    max_option_order_equity_pct: float = 0.005
    paper_drawdown_warn: float = -0.025
    paper_drawdown_stop: float = -0.05
    min_backtest_trades: int = 8
    max_backtest_drawdown: float = -0.35
    min_signal_score: float = 70.0
    min_signal_confidence: float = 0.45
    min_evidence_count: int = 1
    auto_paper_orders_enabled: bool = True
    max_auto_paper_orders_per_day: int = 3
    paper_order_cooldown_hours: int = 8
    require_official_source_for_earnings_orders: bool = True


@dataclass(slots=True)
class StrategyResearchConfig:
    enabled: bool = True
    max_queries_per_strategy: int = 8
    official_sources_first: bool = True
    secondary_search_on_gap: bool = True


@dataclass(slots=True)
class StrategyLabConfig:
    daily_factor_iteration_enabled: bool = True
    allow_new_factor_candidates: bool = True
    min_factor_observations_for_orders: int = 20


@dataclass(slots=True)
class MarketBarsConfig:
    enabled: bool = True
    sqlite_path: str = "market_bars.sqlite"
    default_windows: list[str] = field(default_factory=lambda: ["1d", "5d", "20d"])


@dataclass(slots=True)
class RelationGraphConfig:
    enabled: bool = True
    sqlite_path: str = "relation_graph.sqlite"
    min_confidence: float = 0.55


@dataclass(slots=True)
class EvidenceRankerConfig:
    max_items_per_symbol: int = 5


@dataclass(slots=True)
class ReportVerifierConfig:
    enabled: bool = True
    block_on_wrong_date: bool = True


@dataclass(slots=True)
class ReportConfig:
    max_sync_seconds: int = 30


@dataclass(slots=True)
class RuntimeConfig:
    sqlite_path: str = "runtime.sqlite"


@dataclass(slots=True)
class LoggingConfig:
    enabled: bool = True
    level: str = "INFO"
    jsonl_path: str = "logs/portfolio-bot.jsonl"
    profile_sample_seconds: int = 60


@dataclass(slots=True)
class HealthConfig:
    check_seconds: int = 30
    stale_job_seconds: int = 900
    alert_cooldown_minutes: int = 15
    max_failed_jobs: int = 20
    quote_stale_seconds: int = 1800
    alerts_enabled: bool = True


@dataclass(slots=True)
class DataHubConfig:
    news_cache_minutes: int = 15
    quote_cache_seconds: int = 60


@dataclass(slots=True)
class MetricsConfig:
    backend: str = "thread"
    max_workers: int = 4


@dataclass(slots=True)
class OrchestrationConfig:
    enabled: bool = True
    tick_seconds: int = 60
    strategy_review_time: str = "16:30"
    code_iteration_time: str = "17:30"
    profile_iterations: int = 2


@dataclass(slots=True)
class AgentHarnessConfig:
    enabled: bool = True
    engine: str = "local"
    default_model: str = "gpt-5.4-mini"
    deep_model: str = "gpt-5.5-pro"
    max_turns: int = 4
    max_tool_calls: int = 8
    max_run_seconds: int = 300
    max_auto_changed_files: int = 3
    max_patch_lines: int = 250
    auto_code_iteration: bool = True
    auto_patch_enabled: bool = False
    auto_restart_after_patch: bool = False
    auto_source_config_enabled: bool = False
    auto_restart_after_source_config: bool = False
    require_verification: bool = True


@dataclass(slots=True)
class WorkersConfig:
    enabled: bool = True
    orchestrator_processes: int = 1
    realtime_processes: int = 1
    news_processes: int = 1
    ai_processes: int = 1
    report_processes: int = 1
    agent_processes: int = 1
    strategy_processes: int = 1
    paper_processes: int = 1
    backtest_processes: int = 1
    health_processes: int = 1
    maintenance_processes: int = 1
    heartbeat_seconds: int = 30
    restart_backoff_seconds: int = 5


@dataclass(slots=True)
class RateLimitConfig:
    finnhub_concurrency: int = 4
    news_timeout_seconds: int = 10


@dataclass(slots=True)
class LLMConfig:
    provider: str = "openai"
    api_key: str = ""
    base_url: str = ""
    monitor_model: str = "gpt-5.4-mini"
    event_model: str = "gpt-5.5-pro"
    vision_model: str = ""
    reasoning_effort: str = "high"
    event_thinking_enabled: bool = True
    monitor_thinking_enabled: bool = False


@dataclass(slots=True)
class BotConfig:
    root: Path
    data_dir: Path
    holdings_path: Path
    analysts_path: Path
    strategy_root: Path
    config_path: Path | None = None
    timezone: str = "America/New_York"
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    paper: PaperConfig = field(default_factory=PaperConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    strategy_risk: StrategyRiskConfig = field(default_factory=StrategyRiskConfig)
    strategy_research: StrategyResearchConfig = field(default_factory=StrategyResearchConfig)
    strategy_lab: StrategyLabConfig = field(default_factory=StrategyLabConfig)
    market_bars: MarketBarsConfig = field(default_factory=MarketBarsConfig)
    relation_graph: RelationGraphConfig = field(default_factory=RelationGraphConfig)
    evidence_ranker: EvidenceRankerConfig = field(default_factory=EvidenceRankerConfig)
    report_verifier: ReportVerifierConfig = field(default_factory=ReportVerifierConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    data_hub: DataHubConfig = field(default_factory=DataHubConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    orchestration: OrchestrationConfig = field(default_factory=OrchestrationConfig)
    agent_harness: AgentHarnessConfig = field(default_factory=AgentHarnessConfig)
    workers: WorkersConfig = field(default_factory=WorkersConfig)
    rate_limits: RateLimitConfig = field(default_factory=RateLimitConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    openai_api_key: str = ""
    openai_monitor_model: str = "gpt-5.4-mini"
    openai_event_model: str = "gpt-5.5-pro"
    finnhub_api_key: str = ""
    tradier_access_token: str = ""
    tradier_base_url: str = "https://api.tradier.com/v1"
    x_bearer_token: str = ""
    imessage_recipient: str = ""
    email_to: str = "qling1007@yahoo.com"
    email_from: str = ""
    email_smtp_host: str = "smtp.mail.yahoo.com"
    email_smtp_port: int = 465
    email_username: str = ""
    email_app_password: str = ""
    agentmail_api_key: str = ""
    agentmail_base_url: str = "https://api.agentmail.to"
    agentmail_inbox_id: str = ""
    agentmail_client_id: str = "portfolio-bot-alerts"
    agentmail_display_name: str = "Portfolio Bot"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        value = yaml.safe_load(fh) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected mapping in {path}")
    return value


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def load_config(path: str | Path = "config.yaml") -> BotConfig:
    load_dotenv()
    config_path = Path(path).expanduser().resolve()
    root = config_path.parent if config_path.exists() else Path.cwd()
    raw = _read_yaml(config_path)

    monitor_raw = raw.get("monitor", {})
    thresholds_raw = monitor_raw.get("thresholds", {})
    research_raw = raw.get("research", {})
    notifications_raw = raw.get("notifications", {})
    memory_raw = raw.get("memory", {})
    paper_raw = raw.get("paper", {})
    backtest_raw = raw.get("backtest", {})
    strategy_risk_raw = raw.get("strategy_risk", {})
    strategy_research_raw = raw.get("strategy_research", {})
    strategy_lab_raw = raw.get("strategy_lab", {})
    market_bars_raw = raw.get("market_bars", {})
    relation_graph_raw = raw.get("relation_graph", {})
    evidence_ranker_raw = raw.get("evidence_ranker", {})
    report_verifier_raw = raw.get("report_verifier", {})
    report_raw = raw.get("report", {})
    runtime_raw = raw.get("runtime", {})
    logging_raw = raw.get("logging", {})
    health_raw = raw.get("health", {})
    data_hub_raw = raw.get("data_hub", {})
    metrics_raw = raw.get("metrics", {})
    orchestration_raw = raw.get("orchestration", {})
    agent_harness_raw = raw.get("agent_harness", {})
    workers_raw = raw.get("workers", {})
    rate_limits_raw = raw.get("rate_limits", {})
    llm_raw = raw.get("llm", {})

    monitor = MonitorConfig(
        realtime_poll_seconds=int(monitor_raw.get("realtime_poll_seconds", 30)),
        deep_scan_seconds=int(monitor_raw.get("deep_scan_seconds", 300)),
        report_time=str(monitor_raw.get("report_time", "08:45")),
        major_move_cooldown_minutes=int(monitor_raw.get("major_move_cooldown_minutes", 30)),
        thresholds=ThresholdConfig(
            realtime_percent=float(thresholds_raw.get("realtime_percent", 3.0)),
            intraday_percent=float(thresholds_raw.get("intraday_percent", 5.0)),
            portfolio_percent=float(thresholds_raw.get("portfolio_percent", 2.0)),
        ),
    )
    research = ResearchConfig(
        option_min_days=int(research_raw.get("option_min_days", 180)),
        option_max_days=int(research_raw.get("option_max_days", 548)),
        option_max_premium=float(research_raw.get("option_max_premium", 1500)),
        option_max_spread_percent=float(research_raw.get("option_max_spread_percent", 35)),
        min_news_relevance=float(research_raw.get("min_news_relevance", 0.55)),
        max_news_per_symbol=int(research_raw.get("max_news_per_symbol", 3)),
        web_search_enabled=bool(research_raw.get("web_search_enabled", True)),
        web_search_provider=str(research_raw.get("web_search_provider", "duckduckgo")),
        web_search_api_provider=str(research_raw.get("web_search_api_provider", "tavily")),
        web_search_max_results=int(research_raw.get("web_search_max_results", 5)),
        web_search_timeout_seconds=int(research_raw.get("web_search_timeout_seconds", 8)),
        default_universe=[_normalize_symbol(s) for s in research_raw.get("default_universe", ["INTC", "AXTI", "AEHR", "POET"])],
        leveraged_etfs=[_normalize_symbol(s) for s in research_raw.get("leveraged_etfs", ["USD"])],
        comparison_etfs=[_normalize_symbol(s) for s in research_raw.get("comparison_etfs", ["SOXL"])],
    )
    notifications = NotificationConfig(
        imessage_enabled=bool(notifications_raw.get("imessage_enabled", True)),
        email_enabled=bool(notifications_raw.get("email_enabled", False)),
        agentmail_enabled=bool(notifications_raw.get("agentmail_enabled", False)),
        agentmail_major_alerts_enabled=bool(notifications_raw.get("agentmail_major_alerts_enabled", True)),
        agentmail_market_hours_cooldown_minutes=int(notifications_raw.get("agentmail_market_hours_cooldown_minutes", 10)),
        agentmail_off_hours_cooldown_minutes=int(notifications_raw.get("agentmail_off_hours_cooldown_minutes", 45)),
        agentmail_off_hours_extreme_move_percent=float(notifications_raw.get("agentmail_off_hours_extreme_move_percent", 8.0)),
        batch_realtime_alerts=bool(notifications_raw.get("batch_realtime_alerts", True)),
        semantic_dedupe_enabled=bool(notifications_raw.get("semantic_dedupe_enabled", True)),
        dry_run=bool(notifications_raw.get("dry_run", False)),
    )
    memory = MemoryConfig(
        enabled=bool(memory_raw.get("enabled", True)),
        sqlite_path=str(memory_raw.get("sqlite_path", "memory.sqlite")),
        max_context_items=int(memory_raw.get("max_context_items", 8)),
        open_source_backend=str(memory_raw.get("open_source_backend", "mem0")),
        open_source_enabled=bool(memory_raw.get("open_source_enabled", False)),
    )
    paper = PaperConfig(
        sqlite_path=str(paper_raw.get("sqlite_path", "paper_portfolio.sqlite")),
        starting_cash=float(paper_raw.get("starting_cash", 100000.0)),
    )
    backtest = BacktestConfig(
        sqlite_path=str(backtest_raw.get("sqlite_path", "backtest.sqlite")),
        default_slippage_bps=float(backtest_raw.get("default_slippage_bps", 10.0)),
        default_commission=float(backtest_raw.get("default_commission", 0.0)),
    )
    strategy_risk = StrategyRiskConfig(
        max_paper_order_equity_pct=float(strategy_risk_raw.get("max_paper_order_equity_pct", 0.02)),
        max_option_order_equity_pct=float(strategy_risk_raw.get("max_option_order_equity_pct", 0.005)),
        paper_drawdown_warn=float(strategy_risk_raw.get("paper_drawdown_warn", -0.025)),
        paper_drawdown_stop=float(strategy_risk_raw.get("paper_drawdown_stop", -0.05)),
        min_backtest_trades=int(strategy_risk_raw.get("min_backtest_trades", 8)),
        max_backtest_drawdown=float(strategy_risk_raw.get("max_backtest_drawdown", -0.35)),
        min_signal_score=float(strategy_risk_raw.get("min_signal_score", 70.0)),
        min_signal_confidence=float(strategy_risk_raw.get("min_signal_confidence", 0.45)),
        min_evidence_count=int(strategy_risk_raw.get("min_evidence_count", 1)),
        auto_paper_orders_enabled=bool(strategy_risk_raw.get("auto_paper_orders_enabled", True)),
        max_auto_paper_orders_per_day=int(strategy_risk_raw.get("max_auto_paper_orders_per_day", 3)),
        paper_order_cooldown_hours=int(strategy_risk_raw.get("paper_order_cooldown_hours", 8)),
        require_official_source_for_earnings_orders=bool(strategy_risk_raw.get("require_official_source_for_earnings_orders", True)),
    )
    strategy_research = StrategyResearchConfig(
        enabled=bool(strategy_research_raw.get("enabled", True)),
        max_queries_per_strategy=int(strategy_research_raw.get("max_queries_per_strategy", 8)),
        official_sources_first=bool(strategy_research_raw.get("official_sources_first", True)),
        secondary_search_on_gap=bool(strategy_research_raw.get("secondary_search_on_gap", True)),
    )
    strategy_lab = StrategyLabConfig(
        daily_factor_iteration_enabled=bool(strategy_lab_raw.get("daily_factor_iteration_enabled", True)),
        allow_new_factor_candidates=bool(strategy_lab_raw.get("allow_new_factor_candidates", True)),
        min_factor_observations_for_orders=int(strategy_lab_raw.get("min_factor_observations_for_orders", 20)),
    )
    market_bars = MarketBarsConfig(
        enabled=bool(market_bars_raw.get("enabled", True)),
        sqlite_path=str(market_bars_raw.get("sqlite_path", "market_bars.sqlite")),
        default_windows=[str(value) for value in market_bars_raw.get("default_windows", ["1d", "5d", "20d"])],
    )
    relation_graph = RelationGraphConfig(
        enabled=bool(relation_graph_raw.get("enabled", True)),
        sqlite_path=str(relation_graph_raw.get("sqlite_path", "relation_graph.sqlite")),
        min_confidence=float(relation_graph_raw.get("min_confidence", 0.55)),
    )
    evidence_ranker = EvidenceRankerConfig(
        max_items_per_symbol=int(evidence_ranker_raw.get("max_items_per_symbol", 5)),
    )
    report_verifier = ReportVerifierConfig(
        enabled=bool(report_verifier_raw.get("enabled", True)),
        block_on_wrong_date=bool(report_verifier_raw.get("block_on_wrong_date", True)),
    )
    report = ReportConfig(max_sync_seconds=int(report_raw.get("max_sync_seconds", 30)))
    runtime = RuntimeConfig(sqlite_path=str(runtime_raw.get("sqlite_path", "runtime.sqlite")))
    logging = LoggingConfig(
        enabled=bool(logging_raw.get("enabled", True)),
        level=str(logging_raw.get("level", "INFO")),
        jsonl_path=str(logging_raw.get("jsonl_path", "logs/portfolio-bot.jsonl")),
        profile_sample_seconds=int(logging_raw.get("profile_sample_seconds", 60)),
    )
    health = HealthConfig(
        check_seconds=int(health_raw.get("check_seconds", 30)),
        stale_job_seconds=int(health_raw.get("stale_job_seconds", 900)),
        alert_cooldown_minutes=int(health_raw.get("alert_cooldown_minutes", 15)),
        max_failed_jobs=int(health_raw.get("max_failed_jobs", 20)),
        quote_stale_seconds=int(health_raw.get("quote_stale_seconds", 1800)),
        alerts_enabled=bool(health_raw.get("alerts_enabled", True)),
    )
    data_hub = DataHubConfig(
        news_cache_minutes=int(data_hub_raw.get("news_cache_minutes", 15)),
        quote_cache_seconds=int(data_hub_raw.get("quote_cache_seconds", 60)),
    )
    metrics = MetricsConfig(
        backend=str(metrics_raw.get("backend", "thread")),
        max_workers=int(metrics_raw.get("max_workers", 4)),
    )
    orchestration = OrchestrationConfig(
        enabled=bool(orchestration_raw.get("enabled", True)),
        tick_seconds=int(orchestration_raw.get("tick_seconds", 60)),
        strategy_review_time=str(orchestration_raw.get("strategy_review_time", "16:30")),
        code_iteration_time=str(orchestration_raw.get("code_iteration_time", "17:30")),
        profile_iterations=int(orchestration_raw.get("profile_iterations", 2)),
    )
    agent_harness = AgentHarnessConfig(
        enabled=bool(agent_harness_raw.get("enabled", True)),
        engine=str(agent_harness_raw.get("engine", "local")),
        default_model=str(agent_harness_raw.get("default_model", "gpt-5.4-mini")),
        deep_model=str(agent_harness_raw.get("deep_model", "gpt-5.5-pro")),
        max_turns=int(agent_harness_raw.get("max_turns", 4)),
        max_tool_calls=int(agent_harness_raw.get("max_tool_calls", 8)),
        max_run_seconds=int(agent_harness_raw.get("max_run_seconds", 300)),
        max_auto_changed_files=int(agent_harness_raw.get("max_auto_changed_files", 3)),
        max_patch_lines=int(agent_harness_raw.get("max_patch_lines", 250)),
        auto_code_iteration=bool(agent_harness_raw.get("auto_code_iteration", True)),
        auto_patch_enabled=bool(agent_harness_raw.get("auto_patch_enabled", False)),
        auto_restart_after_patch=bool(agent_harness_raw.get("auto_restart_after_patch", False)),
        auto_source_config_enabled=bool(agent_harness_raw.get("auto_source_config_enabled", False)),
        auto_restart_after_source_config=bool(agent_harness_raw.get("auto_restart_after_source_config", False)),
        require_verification=bool(agent_harness_raw.get("require_verification", True)),
    )
    workers = WorkersConfig(
        enabled=bool(workers_raw.get("enabled", True)),
        orchestrator_processes=int(workers_raw.get("orchestrator_processes", 1)),
        realtime_processes=int(workers_raw.get("realtime_processes", 1)),
        news_processes=int(workers_raw.get("news_processes", 1)),
        ai_processes=int(workers_raw.get("ai_processes", 1)),
        report_processes=int(workers_raw.get("report_processes", 1)),
        agent_processes=int(workers_raw.get("agent_processes", 1)),
        strategy_processes=int(workers_raw.get("strategy_processes", 1)),
        paper_processes=int(workers_raw.get("paper_processes", 1)),
        backtest_processes=int(workers_raw.get("backtest_processes", 1)),
        health_processes=int(workers_raw.get("health_processes", 1)),
        maintenance_processes=int(workers_raw.get("maintenance_processes", 1)),
        heartbeat_seconds=int(workers_raw.get("heartbeat_seconds", 30)),
        restart_backoff_seconds=int(workers_raw.get("restart_backoff_seconds", 5)),
    )
    rate_limits = RateLimitConfig(
        finnhub_concurrency=int(rate_limits_raw.get("finnhub_concurrency", 4)),
        news_timeout_seconds=int(rate_limits_raw.get("news_timeout_seconds", 10)),
    )
    llm = build_llm_config(llm_raw)

    data_dir = _resolve(root, raw.get("data_dir", ".portfolio_bot"))
    return BotConfig(
        root=root,
        data_dir=data_dir,
        holdings_path=_resolve(root, raw.get("holdings_path", "holdings.yaml")),
        analysts_path=_resolve(root, raw.get("analysts_path", "analysts.yaml")),
        strategy_root=_resolve(root, raw.get("strategy_root", "strategy_skills")),
        config_path=config_path if config_path.exists() else None,
        timezone=str(raw.get("timezone", "America/New_York")),
        monitor=monitor,
        research=research,
        notifications=notifications,
        memory=memory,
        paper=paper,
        backtest=backtest,
        strategy_risk=strategy_risk,
        strategy_research=strategy_research,
        strategy_lab=strategy_lab,
        market_bars=market_bars,
        relation_graph=relation_graph,
        evidence_ranker=evidence_ranker,
        report_verifier=report_verifier,
        report=report,
        runtime=runtime,
        logging=logging,
        health=health,
        data_hub=data_hub,
        metrics=metrics,
        orchestration=orchestration,
        agent_harness=agent_harness,
        workers=workers,
        rate_limits=rate_limits,
        llm=llm,
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_monitor_model=os.getenv("OPENAI_MONITOR_MODEL", llm.monitor_model),
        openai_event_model=os.getenv("OPENAI_EVENT_MODEL", llm.event_model),
        finnhub_api_key=os.getenv("FINNHUB_API_KEY", ""),
        tradier_access_token=os.getenv("TRADIER_ACCESS_TOKEN", ""),
        tradier_base_url=os.getenv("TRADIER_BASE_URL", "https://api.tradier.com/v1"),
        x_bearer_token=os.getenv("X_BEARER_TOKEN", ""),
        imessage_recipient=os.getenv("IMESSAGE_RECIPIENT", ""),
        email_to=os.getenv("EMAIL_TO", "qling1007@yahoo.com"),
        email_from=os.getenv("EMAIL_FROM", os.getenv("EMAIL_USERNAME", "")),
        email_smtp_host=os.getenv("EMAIL_SMTP_HOST", "smtp.mail.yahoo.com"),
        email_smtp_port=int(os.getenv("EMAIL_SMTP_PORT", "465")),
        email_username=os.getenv("EMAIL_USERNAME", ""),
        email_app_password=os.getenv("EMAIL_APP_PASSWORD", ""),
        agentmail_api_key=os.getenv("AGENTMAIL_API_KEY", ""),
        agentmail_base_url=os.getenv("AGENTMAIL_BASE_URL", "https://api.agentmail.to"),
        agentmail_inbox_id=os.getenv("AGENTMAIL_INBOX_ID", ""),
        agentmail_client_id=os.getenv("AGENTMAIL_CLIENT_ID", "portfolio-bot-alerts"),
        agentmail_display_name=os.getenv("AGENTMAIL_DISPLAY_NAME", "Portfolio Bot"),
    )


def _normalize_symbol(value: Any) -> str:
    if value is True:
        return "ON"
    if value is False:
        return "OFF"
    return str(value).strip().upper()


def build_llm_config(raw: dict[str, Any]) -> LLMConfig:
    provider = str(os.getenv("LLM_PROVIDER", raw.get("provider", "")) or default_llm_provider()).strip().lower()
    defaults = llm_provider_defaults(provider)
    api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv(f"{provider.upper()}_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY")
        or str(raw.get("api_key", ""))
    )
    base_url = os.getenv("LLM_BASE_URL") or str(raw.get("base_url", defaults["base_url"]))
    monitor_model = (
        os.getenv("LLM_MONITOR_MODEL")
        or os.getenv(f"{provider.upper()}_MONITOR_MODEL")
        or os.getenv("OPENAI_MONITOR_MODEL" if provider == "openai" else "")
        or str(raw.get("monitor_model", defaults["monitor_model"]))
    )
    event_model = (
        os.getenv("LLM_EVENT_MODEL")
        or os.getenv(f"{provider.upper()}_EVENT_MODEL")
        or os.getenv("OPENAI_EVENT_MODEL" if provider == "openai" else "")
        or str(raw.get("event_model", defaults["event_model"]))
    )
    return LLMConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        monitor_model=monitor_model,
        event_model=event_model,
        vision_model=os.getenv("LLM_VISION_MODEL") or str(raw.get("vision_model", defaults.get("vision_model", ""))),
        reasoning_effort=str(os.getenv("LLM_REASONING_EFFORT") or raw.get("reasoning_effort", defaults.get("reasoning_effort", "high"))),
        event_thinking_enabled=bool(raw.get("event_thinking_enabled", defaults.get("event_thinking_enabled", True))),
        monitor_thinking_enabled=bool(raw.get("monitor_thinking_enabled", defaults.get("monitor_thinking_enabled", False))),
    )


def default_llm_provider() -> str:
    if os.getenv("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "deepseek"


def llm_provider_defaults(provider: str) -> dict[str, Any]:
    if provider == "deepseek":
        return {
            "base_url": "https://api.deepseek.com",
            "monitor_model": "deepseek-v4-flash",
            "event_model": "deepseek-v4-pro",
            "vision_model": "",
            "reasoning_effort": "high",
            "event_thinking_enabled": True,
            "monitor_thinking_enabled": False,
        }
    if provider == "openai-compatible":
        return {
            "base_url": "",
            "monitor_model": "gpt-5.4-mini",
            "event_model": "gpt-5.5-pro",
            "vision_model": "",
            "reasoning_effort": "high",
            "event_thinking_enabled": False,
            "monitor_thinking_enabled": False,
        }
    return {
        "base_url": "",
        "monitor_model": "gpt-5.4-mini",
        "event_model": "gpt-5.5-pro",
        "vision_model": "gpt-5.4-mini",
        "reasoning_effort": "high",
        "event_thinking_enabled": False,
        "monitor_thinking_enabled": False,
    }
