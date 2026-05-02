from datetime import datetime, timezone

from portfolio_bot.config import load_config
from portfolio_bot.data_hub import DataHub
from portfolio_bot.features import FeatureEngine
from portfolio_bot.market.metrics import MetricService
from portfolio_bot.models import Holding, NewsItem, Quote
from portfolio_bot.orchestrator import OrchestratorAgent
from portfolio_bot.runtime import RuntimeStore, runtime_path
from portfolio_bot.strategies.semiconductor_reversal import SemiconductorReversalStrategy


class CountingFinnhub:
    configured = True

    def __init__(self):
        self.news_calls = 0
        self.quote_calls = 0

    def quote(self, symbol):
        self.quote_calls += 1
        return Quote(symbol=symbol.upper(), price=10.0, timestamp=datetime.now(timezone.utc), change_percent=2.0)

    def company_news(self, symbol, start, end):
        self.news_calls += 1
        return [
            NewsItem(
                title=f"{symbol} announces silicon photonics design win",
                url=f"https://example.com/{symbol}",
                source="test",
                published_at=datetime.now(timezone.utc),
                symbols=[symbol.upper()],
                summary="Customer qualification completed.",
            )
        ]


class EmptyNewsFinnhub(CountingFinnhub):
    def company_news(self, symbol, start, end):
        self.news_calls += 1
        return []


class EmptyX:
    def recent_semiconductor_posts(self, analyst_config):
        return []


def test_data_hub_caches_news_and_quotes(tmp_path):
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
    (tmp_path / "analysts.yaml").write_text("analysts: []\n", encoding="utf-8")
    config = load_config(config_path)
    fake = CountingFinnhub()
    hub = DataHub(config, finnhub=fake, x_api=EmptyX())

    first = hub.collect_news(["POET"], commit=True)
    second = hub.collect_news(["POET"], commit=True)
    fresh_once = hub.collect_news(["POET"], commit=True, fresh_only=True)
    fresh_after_seen = hub.collect_news(["POET"], commit=True, fresh_only=True)
    q1 = hub.quote("POET")
    q2 = hub.quote("POET")

    assert len(first) == 1
    assert len(second) == 1
    assert len(fresh_once) == 1
    assert fresh_after_seen == []
    assert fake.news_calls == 1
    assert q1.price == q2.price == 10.0
    assert fake.quote_calls == 1


def test_data_hub_caches_empty_news_results(tmp_path):
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
    (tmp_path / "analysts.yaml").write_text("analysts: []\n", encoding="utf-8")
    config = load_config(config_path)
    fake = EmptyNewsFinnhub()
    hub = DataHub(config, finnhub=fake, x_api=EmptyX())

    assert hub.collect_news(["POET"], commit=True) == []
    assert hub.collect_news(["POET"], commit=True) == []

    assert fake.news_calls == 1


def test_feature_engine_feeds_strategy(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    news = [
        NewsItem(
            title="POET announces silicon photonics design win and raises guidance",
            url="https://example.com/poet",
            source="test",
            published_at=datetime.now(timezone.utc),
            symbols=["POET"],
            summary="Customer qualification completed.",
        )
    ]
    quote = Quote(symbol="POET", price=12.0, timestamp=datetime.now(timezone.utc), change_percent=6.0)
    features = FeatureEngine(store).compute_symbol("POET", quote, news)
    score = SemiconductorReversalStrategy().evaluate("POET", quote, news, features=features)

    assert features["positive_keyword_score"] >= 20
    assert "silicon_photonics" in features["chain_hits"]
    assert score.score > 60
    assert store.latest_features("POET")["symbol"] == "POET"


def test_feature_engine_includes_holding_exposure(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    news = [
        NewsItem(
            title="POET announces silicon photonics design win",
            url="https://example.com/poet",
            source="test",
            published_at=datetime.now(timezone.utc),
            symbols=["POET"],
            summary="Customer qualification completed.",
        )
    ]
    quote = Quote(symbol="POET", price=12.0, timestamp=datetime.now(timezone.utc), change_percent=1.0)

    features = FeatureEngine(store).compute_many(
        ["POET"],
        {"POET": quote},
        news,
        holdings=[Holding(symbol="POET", asset_type="equity", quantity=10, market_value=120)],
        commit=False,
    )

    assert features["POET"]["portfolio_quantity"] == 10
    assert features["POET"]["portfolio_market_value"] == 120


def test_metric_pipeline_supports_async_and_process_backends(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    news = [
        NewsItem(
            title="POET announces silicon photonics design win",
            url="https://example.com/poet",
            source="test",
            published_at=datetime.now(timezone.utc),
            symbols=["POET"],
            summary="Customer qualification completed.",
        )
    ]
    quote = Quote(symbol="POET", price=12.0, timestamp=datetime.now(timezone.utc), change_percent=6.0)

    async_bundle = MetricService(store, backend="async").compute_many(["POET"], {"POET": quote}, news, commit=False)["POET"]
    process_bundle = MetricService(store, backend="process", max_workers=1).compute_many(["POET"], {"POET": quote}, news, commit=True)["POET"]

    assert async_bundle.sentiment["positive_score"] >= 20
    assert "silicon_photonics" in process_bundle.chain_exposure
    assert store.latest_metric_snapshot("POET", "feature_bundle")["value"]["symbol"] == "POET"


def test_orchestrator_enqueues_operator_jobs(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
timezone: UTC
monitor:
  report_time: "00:00"
orchestration:
  strategy_review_time: "00:00"
  code_iteration_time: "00:00"
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    scheduled = OrchestratorAgent(config).schedule_once()
    jobs = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path)).jobs(limit=20)

    assert scheduled["news_scan"] == 1
    assert {"daily_report", "strategy_review", "agent_run"} <= {job.type for job in jobs}
