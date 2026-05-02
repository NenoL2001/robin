from datetime import datetime, timezone

from portfolio_bot.config import load_config
from portfolio_bot.monitor import PortfolioMonitor

from portfolio_bot.monitor import LLM_ANALYSIS_BACKOFF, LLM_ANALYSIS_BACKOFF_KEY, format_major_event_email, format_major_event_email_batch, holding_alert_symbols, major_event_email_worthy, off_hours_major_event_email_worthy, percent_change, is_high_impact_lead, select_high_impact_news
from portfolio_bot.models import Holding, MarketEvent, NewsItem, OptionContract, Quote
from portfolio_bot.runtime import RuntimeStore, runtime_path


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, subject, body):
        self.sent.append((subject, body))


class FakeFinnhub:
    def quote(self, symbol):
        return None


class FakeOpenAI:
    configured = True

    def analyze_event(self, event, related_news, memory_context=""):
        return "深度分析补充"


def test_percent_change():
    assert percent_change(100, 103) == 3


def test_high_impact_lead_keywords():
    assert is_high_impact_lead("Company announces major contract and design win")
    assert not is_high_impact_lead("Routine product blog post")


def test_option_contract_spread_and_premium():
    contract = OptionContract(
        underlying="POET",
        symbol="POETC",
        expiration=datetime.now(timezone.utc),
        strike=13,
        option_type="call",
        bid=3.0,
        ask=4.0,
    )
    assert contract.premium == 350
    assert round(contract.spread_percent or 0, 2) == 28.57


def test_holding_alert_symbols_expand_snxx_to_sndk():
    symbols = holding_alert_symbols([Holding(symbol="SNXX", asset_type="equity", quantity=10)])

    assert {"SNXX", "SNDK"} <= symbols


def test_high_event_sends_quick_alert_and_queues_deep_analysis(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    config.llm.api_key = "test"
    notifier = FakeNotifier()
    monitor = PortfolioMonitor(config, notifier=notifier, finnhub=FakeFinnhub(), openai_service=FakeOpenAI())

    monitor.handle_event(MarketEvent(symbol="POET", event_type="intraday_move", severity="high", message="POET moved +6%"))

    assert notifier.sent[0][0] == "Portfolio alert: POET intraday_move"
    jobs = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path)).jobs(status="pending")
    assert len(jobs) == 1
    assert jobs[0].type == "major_event_analysis"


def test_openai_backoff_skips_deep_analysis_enqueue(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    config.llm.api_key = "test"
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    runtime.check_and_touch_cooldown(LLM_ANALYSIS_BACKOFF_KEY, LLM_ANALYSIS_BACKOFF, commit=True)
    monitor = PortfolioMonitor(config, notifier=FakeNotifier(), finnhub=FakeFinnhub(), openai_service=FakeOpenAI())

    monitor.handle_event(MarketEvent(symbol="POET", event_type="intraday_move", severity="high", message="POET moved +6%"))

    assert RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path)).jobs(status="pending") == []


def test_select_high_impact_news_dedupes_symbols_and_limits():
    items = [
        NewsItem(title="POET announces customer qualification", url="1", source="test", symbols=["POET"]),
        NewsItem(title="POET announces design win", url="2", source="test", symbols=["POET"]),
        NewsItem(title="AEHR routine blog post", url="3", source="test", symbols=["AEHR"]),
        NewsItem(title="INTC raises guidance", url="4", source="test", symbols=["INTC"]),
        NewsItem(title="AXTI public offering priced", url="5", source="test", symbols=["AXTI"]),
    ]

    selected = select_high_impact_news(items, max_items=2)

    assert len(selected) == 2
    assert {item.symbols[0] for item in selected} == {"POET", "INTC"}

    held_only = select_high_impact_news(items, max_items=5, symbols={"POET"})
    assert len(held_only) == 1
    assert held_only[0].symbols == ["POET"]

    related_but_not_direct = [
        NewsItem(title="Tesla Q1 Earnings Beat, But the Narrative Is Weakening", url="6", source="test", symbols=["GOOGL"])
    ]
    assert select_high_impact_news(related_but_not_direct, symbols={"GOOGL"}) == []


def test_dry_run_scan_does_not_mutate_state(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    monitor = PortfolioMonitor(config, notifier=FakeNotifier(), finnhub=FakeFinnhub(), openai_service=FakeOpenAI(), dry_run=True)

    events = monitor.detect_events(
        [Holding(symbol="POET", quantity=1)],
        {"POET": Quote(symbol="POET", price=10, timestamp=datetime.now(timezone.utc), change_percent=8)},
        commit=False,
    )

    assert len(events) == 1
    assert not (config.data_dir / "state.json").exists()
    assert RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path)).status()["quote_snapshots"] == 0


def test_major_event_email_worthy_for_big_moves_and_major_news():
    assert major_event_email_worthy(MarketEvent(symbol="POET", event_type="intraday_move", severity="high", message="move", metadata={"change_percent": 6.0}))
    assert not major_event_email_worthy(MarketEvent(symbol="POET", event_type="intraday_move", severity="high", message="move", metadata={"change_percent": 2.0}))
    assert major_event_email_worthy(
        MarketEvent(
            symbol="POET",
            event_type="high_impact_news",
            severity="high",
            message="news",
            news=NewsItem(title="POET announces major contract", url="https://example.com", source="test", symbols=["POET"]),
        )
    )


def test_off_hours_major_event_email_worthy_is_more_selective():
    assert not off_hours_major_event_email_worthy(MarketEvent(symbol="POET", event_type="intraday_move", severity="high", message="move", metadata={"change_percent": 6.0}))
    assert off_hours_major_event_email_worthy(MarketEvent(symbol="POET", event_type="intraday_move", severity="high", message="move", metadata={"change_percent": 9.0}))
    assert off_hours_major_event_email_worthy(
        MarketEvent(
            symbol="POET",
            event_type="high_impact_news",
            severity="high",
            message="news",
            news=NewsItem(title="POET reports quarterly results", url="https://example.com", source="test", symbols=["POET"]),
        )
    )


def test_agentmail_major_event_cooldown_is_shared_across_sessions(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
notifications:
  imessage_enabled: false
  agentmail_enabled: true
  agentmail_major_alerts_enabled: true
  agentmail_market_hours_cooldown_minutes: 10
  agentmail_off_hours_cooldown_minutes: 45
  agentmail_off_hours_extreme_move_percent: 8.0
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    monitor = PortfolioMonitor(config, notifier=FakeNotifier(), finnhub=FakeFinnhub(), openai_service=FakeOpenAI())
    event = MarketEvent(symbol="POET", event_type="intraday_move", severity="high", message="move", metadata={"change_percent": 9.0})

    monkeypatch.setattr("portfolio_bot.market.monitor.market_session_now", lambda timezone_name: "off_hours")
    assert monitor.should_send_major_event_email(event)
    monkeypatch.setattr("portfolio_bot.market.monitor.market_session_now", lambda timezone_name: "market_hours")
    assert not monitor.should_send_major_event_email(event)


def test_major_event_email_batch_sends_one_email_for_multiple_events(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
notifications:
  imessage_enabled: false
  agentmail_enabled: true
  agentmail_major_alerts_enabled: true
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    sent = []

    class FakeAgentMail:
        def __init__(self, config):
            pass

        def send(self, subject, body):
            sent.append((subject, body))

    monkeypatch.setattr("portfolio_bot.market.monitor.AgentMailNotifier", FakeAgentMail)
    monkeypatch.setattr("portfolio_bot.market.monitor.market_session_now", lambda timezone_name: "market_hours")
    monitor = PortfolioMonitor(config, notifier=FakeNotifier(), finnhub=FakeFinnhub(), openai_service=FakeOpenAI())

    monitor.send_major_event_email_batch(
        [
            MarketEvent(symbol="POET", event_type="intraday_move", severity="high", message="POET moved", metadata={"change_percent": 9.0}),
            MarketEvent(symbol="HIMS", event_type="intraday_move", severity="high", message="HIMS moved", metadata={"change_percent": 8.0}),
        ]
    )

    assert len(sent) == 1
    assert sent[0][0] == "Portfolio major alerts: 2 events"
    assert "POET intraday_move" in sent[0][1]
    assert "HIMS intraday_move" in sent[0][1]


def test_format_major_event_email_contains_reference_link():
    event = MarketEvent(
        symbol="POET",
        event_type="high_impact_news",
        severity="high",
        message="POET major contract",
        news=NewsItem(title="POET announces major contract", url="https://example.com", source="test", symbols=["POET"], summary="contract"),
    )

    body = format_major_event_email(event)

    assert "参考: https://example.com" in body
    assert "不是自动买卖指令" in body


def test_format_major_event_email_batch_merges_events():
    body = format_major_event_email_batch(
        [
            MarketEvent(symbol="POET", event_type="intraday_move", severity="high", message="POET moved", metadata={"change_percent": 9.0}),
            MarketEvent(symbol="HIMS", event_type="intraday_move", severity="high", message="HIMS moved", metadata={"change_percent": 8.0}),
        ]
    )

    assert "合并重大提醒: 2 个事件" in body
    assert "POET intraday_move" in body
    assert "HIMS intraday_move" in body
