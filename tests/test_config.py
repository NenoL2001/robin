from portfolio_bot.config import load_config


def test_yaml_on_symbol_is_not_boolean(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
research:
  default_universe:
    - ON
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.research.default_universe == ["ON"]


def test_runtime_worker_rate_limit_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")
    config = load_config(path)

    assert config.runtime.sqlite_path == "runtime.sqlite"
    assert config.workers.enabled
    assert config.workers.orchestrator_processes == 1
    assert config.workers.realtime_processes == 1
    assert config.workers.strategy_processes == 1
    assert config.workers.health_processes == 1
    assert config.workers.maintenance_processes == 1
    assert config.logging.jsonl_path == "logs/portfolio-bot.jsonl"
    assert config.logging.profile_sample_seconds == 60
    assert config.health.check_seconds == 30
    assert config.data_hub.news_cache_minutes == 15
    assert config.orchestration.strategy_review_time == "16:30"
    assert config.rate_limits.finnhub_concurrency == 4
    assert config.notifications.agentmail_market_hours_cooldown_minutes == 10
    assert config.notifications.agentmail_off_hours_cooldown_minutes == 45
    assert config.notifications.agentmail_off_hours_extreme_move_percent == 8.0


def test_deepseek_env_selects_llm_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")

    config = load_config(path)

    assert config.llm.provider == "deepseek"
    assert config.llm.api_key == "test-key"
    assert config.llm.base_url == "https://api.deepseek.com"
    assert config.llm.monitor_model == "deepseek-v4-flash"
    assert config.llm.event_model == "deepseek-v4-pro"
