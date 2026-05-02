from __future__ import annotations

from datetime import datetime, timezone

from portfolio_bot.agents.source_config import SourceConfigManager
from portfolio_bot.backtest import BacktestResult, BacktestStore
from portfolio_bot.config import StrategyRiskConfig, load_config
from portfolio_bot.market.daily_digest import DailyNewsDigestBuilder
from portfolio_bot.market.strategy_news_scout import StrategyNewsScout, expand_strategy_symbols
from portfolio_bot.market.web_search import DuckDuckGoHtmlProvider, TavilySearchProvider
from portfolio_bot.memory import MemoryStore, memory_path
from portfolio_bot.models import NewsItem, Quote, StrategySignal, WebEvidence
from portfolio_bot.research import ResearchEngine
from portfolio_bot.runtime import RuntimeStore, runtime_path
from portfolio_bot.strategies.factor_specs import FactorSpecStore
from portfolio_bot.strategies.risk_gate import RiskGateContext, StrategyRiskGate


def good_backtest(trades: int = 8, drawdown: float = -0.1) -> BacktestResult:
    return BacktestResult(
        backtest_id="bt-test",
        strategy_name="semiconductor_reversal",
        strategy_version="1.0.0",
        asset_type="equity",
        total_return=0.25,
        max_drawdown=drawdown,
        win_rate=0.6,
        trade_count=trades,
        average_trade_return=0.03,
        losing_trades=2,
        metadata={},
    )


def good_signal() -> StrategySignal:
    return StrategySignal(
        signal_id="sig-test",
        symbol="POET",
        strategy_name="semiconductor_reversal",
        strategy_version="1.0.0",
        action="paper_buy",
        score=82,
        confidence=0.72,
        reason="confirmed catalyst",
    )


def good_evidence() -> list[NewsItem]:
    return [
        NewsItem(
            title="POET announces silicon photonics design win",
            url="https://example.com/poet",
            source="example",
            published_at=datetime.now(timezone.utc),
            symbols=["POET"],
            summary="Customer qualification and revenue growth catalyst.",
        )
    ]


def test_strategy_risk_gate_pass_warn_stop_backtest_evidence_and_exposure():
    gate = StrategyRiskGate(StrategyRiskConfig())
    base = RiskGateContext(
        portfolio_equity=100000,
        paper_drawdown=0,
        latest_backtest=good_backtest(),
        evidence=good_evidence(),
    )

    assert gate.evaluate(good_signal(), base).allowed

    base_kwargs = {
        "portfolio_equity": base.portfolio_equity,
        "paper_drawdown": base.paper_drawdown,
        "real_exposure": base.real_exposure,
        "paper_exposure": base.paper_exposure,
        "asset_type": base.asset_type,
        "latest_backtest": base.latest_backtest,
        "evidence": base.evidence,
        "price": base.price,
    }

    warn = gate.evaluate(good_signal(), RiskGateContext(**{**base_kwargs, "paper_drawdown": -0.03}))
    assert warn.allowed
    assert warn.severity == "warn"
    assert warn.max_notional == 1000

    stop = gate.evaluate(good_signal(), RiskGateContext(**{**base_kwargs, "paper_drawdown": -0.06}))
    assert not stop.allowed
    assert "paper_drawdown_stop" in stop.blocked_checks

    weak_backtest = gate.evaluate(good_signal(), RiskGateContext(**{**base_kwargs, "latest_backtest": good_backtest(trades=2)}))
    assert not weak_backtest.allowed
    assert "weak_backtest_trades" in weak_backtest.blocked_checks

    missing_evidence = gate.evaluate(good_signal(), RiskGateContext(**{**base_kwargs, "evidence": []}))
    assert not missing_evidence.allowed
    assert "missing_evidence" in missing_evidence.blocked_checks

    exposure = gate.evaluate(good_signal(), RiskGateContext(**{**base_kwargs, "real_exposure": 12000}))
    assert not exposure.allowed
    assert "portfolio_exposure_cap" in exposure.blocked_checks


class FakeGetSession:
    def get(self, *args, **kwargs):
        return FakeResponse(
            text="""
            <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fpoet">POET announces design win</a>
            <div class="result__snippet">POET customer qualification in silicon photonics.</div>
            """
        )


class FakePostSession:
    def post(self, *args, **kwargs):
        return FakeResponse(
            payload={
                "results": [
                    {
                        "title": "POET raises guidance",
                        "url": "https://example.com/tavily-poet",
                        "content": "POET customer qualification and revenue growth.",
                        "published_date": datetime.now(timezone.utc).isoformat(),
                    }
                ]
            }
        )


class FakeResponse:
    def __init__(self, text: str = "", payload=None):
        self.text = text
        self._payload = payload or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_web_search_providers_parse_mocked_results():
    ddg = DuckDuckGoHtmlProvider(session=FakeGetSession()).search("POET news", symbols=["POET"], max_results=5, timeout=1)
    assert ddg[0].url == "https://example.com/poet"
    assert ddg[0].symbols == ["POET"]

    tavily = TavilySearchProvider(api_key="test", session=FakePostSession()).search("POET news", symbols=["POET"], max_results=5, timeout=1)
    assert tavily[0].provider == "tavily"
    assert tavily[0].published_at is not None
    assert tavily[0].confidence >= 0.5


class FakeDataHub:
    def collect_news(self, symbols, days=3, commit=True):
        return good_evidence()


class FakeWebSearch:
    def search(self, query, *, symbols=None, commit=True):
        return [
            WebEvidence(
                title="POET announces silicon photonics design win",
                url="https://example.com/poet",
                source="example",
                query=query,
                symbols=["POET"],
                summary="duplicate web result",
                confidence=0.55,
                relevance_score=0.8,
                provider="test",
                published_at=datetime.now(timezone.utc),
            )
        ]


class EmptyWebSearch:
    def search(self, query, *, symbols=None, commit=True):
        return []


class LITXRelationshipWebSearch:
    def search(self, query, *, symbols=None, commit=True):
        if "LITX" not in query.upper():
            return []
        return [
            WebEvidence(
                title="GraniteShares 2x Long LITE Daily ETF (LITX)",
                url="https://example.com/litx-prospectus",
                source="example",
                query=query,
                symbols=["LITX"],
                summary="The ETF seeks two times long daily leveraged exposure to Lumentum Holdings Inc. ticker LITE.",
                confidence=0.72,
                relevance_score=0.9,
                provider="test",
                published_at=datetime.now(timezone.utc),
            )
        ]


def write_strategy_config(tmp_path, extra: str = ""):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
timezone: UTC
research:
  default_universe:
    - POET
  web_search_enabled: false
strategy_risk:
  min_signal_confidence: 0.3
  min_backtest_trades: 1
memory:
  enabled: true
notifications:
  imessage_enabled: false
{extra}
""",
        encoding="utf-8",
    )
    (tmp_path / "analysts.yaml").write_text("analysts: []\n", encoding="utf-8")
    return load_config(config_path)


def test_daily_digest_dedupes_sources_and_preserves_urls(tmp_path):
    config = write_strategy_config(tmp_path)
    config.research.web_search_enabled = True
    digest = DailyNewsDigestBuilder(
        config,
        data_hub=FakeDataHub(),
        web_search=FakeWebSearch(),
        runtime=RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path)),
        memory=MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=True),
    ).build(["POET"], commit=False, include_web=True)

    assert len(digest.items) == 1
    assert digest.items[0].url == "https://example.com/poet"
    assert digest.web_evidence[0].url == "https://example.com/poet"


def test_strategy_news_scout_expands_snxx_and_extracts_sndk_events(tmp_path):
    config = write_strategy_config(tmp_path)
    scout = StrategyNewsScout(
        config,
        web_search=EmptyWebSearch(),
        runtime=RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path)),
        memory=MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=True),
    )

    result = scout.scout("semiconductor_reversal", ["SNXX"], commit=False, deep=True)
    event_types = {event.event_type for event in result.events}
    expanded = set(expand_strategy_symbols(["SNXX"]))

    assert {"SNXX", "SNDK"} <= expanded
    assert "HBF" not in expanded
    assert "NBM" not in expanded
    assert any("investor.sandisk.com" in query for query in result.queries)
    assert any("investor.sandisk.com/node/7896/pdf" in item.url for item in result.evidence)
    assert {"earnings_surprise", "guidance_revision", "datacenter_mix_shift", "hbf_ai_inference_moat"} <= event_types
    assert not result.gaps


def test_factor_iteration_persists_candidate_specs(tmp_path):
    config = write_strategy_config(tmp_path)
    result = ResearchEngine(config).iterate_strategy_factors(dry_run=False)
    memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=True)
    weights = FactorSpecStore(config.strategy_root).weights()

    assert "earnings_surprise" in result.added
    assert weights["earnings_surprise"] > 0
    assert memory.recent(kind="factor_spec", limit=1)


class GoodFinnhub:
    configured = True

    def quote(self, symbol):
        return Quote(symbol=symbol.upper(), price=10.0, timestamp=datetime.now(timezone.utc), change_percent=4.0)

    def company_news(self, symbol, start, end):
        return [
            NewsItem(
                title=f"{symbol} announces silicon photonics design win and raises guidance",
                url=f"https://example.com/{symbol}",
                source="test",
                published_at=datetime.now(timezone.utc),
                symbols=[symbol.upper()],
                summary="Customer qualification, revenue growth, and major contract.",
            )
        ]


class LITXFinnhub(GoodFinnhub):
    def quote(self, symbol):
        return Quote(symbol=symbol.upper(), price=20.0, timestamp=datetime.now(timezone.utc), change_percent=6.0, previous_close=18.86, volume=123456)

    def company_news(self, symbol, start, end):
        if symbol.upper() == "LITE":
            return [
                NewsItem(
                    title="Lumentum raises guidance on data center optical demand",
                    url="https://example.com/lite-guidance",
                    source="test",
                    published_at=datetime.now(timezone.utc),
                    symbols=["LITE"],
                    summary="Raises guidance, revenue growth, data center buildout, and margin expansion.",
                )
            ]
        return []


class EmptyX:
    def recent_semiconductor_posts(self, analyst_config):
        return []


class EmptyTradier:
    configured = False


def test_strategy_plan_dry_run_proposes_only_and_does_not_enqueue_paper_buy(tmp_path):
    config = write_strategy_config(tmp_path)
    BacktestStore(config.data_dir / config.backtest.sqlite_path).save(good_backtest(trades=2))
    engine = ResearchEngine(config, finnhub=GoodFinnhub(), tradier=EmptyTradier(), x_api=EmptyX())

    plan = engine.generate_strategy_plan(["POET"], dry_run=True)
    jobs = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path)).jobs(limit=20)

    assert plan["paper_order_proposals"]
    assert all(job.type != "paper_buy" for job in jobs)
    signal = plan["signals"][0]
    assert signal["signal_id"]
    assert signal["metadata"]["factor_breakdown"]
    assert signal["metadata"]["evidence_links"]
    assert signal["metadata"]["risk_gate_verdict"]["allowed"] is True


def test_strategy_plan_uses_sndk_scout_evidence_for_snxx(tmp_path):
    config = write_strategy_config(tmp_path)
    BacktestStore(config.data_dir / config.backtest.sqlite_path).save(good_backtest(trades=2))
    engine = ResearchEngine(config, finnhub=GoodFinnhub(), tradier=EmptyTradier(), x_api=EmptyX())

    plan = engine.generate_strategy_plan(["SNXX"], dry_run=True)
    assert [signal["symbol"] for signal in plan["signals"]] == ["SNXX"]
    snxx = plan["signals"][0]
    factors = {row["name"] for row in snxx["metadata"]["factor_breakdown"]}

    assert {"earnings_surprise", "guidance_revision", "hbf_ai_inference_moat"} <= factors
    assert snxx["metadata"]["strategy_scout_events"]
    assert plan["paper_order_proposals"]


def test_strategy_plan_infers_litx_to_lite_relationship_and_bridges_news(tmp_path):
    config = write_strategy_config(tmp_path)
    BacktestStore(config.data_dir / config.backtest.sqlite_path).save(good_backtest(trades=2))
    scout = StrategyNewsScout(
        config,
        web_search=LITXRelationshipWebSearch(),
        runtime=RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path)),
        memory=MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=True),
    )
    engine = ResearchEngine(config, finnhub=LITXFinnhub(), tradier=EmptyTradier(), x_api=EmptyX(), strategy_news_scout=scout)

    plan = engine.generate_strategy_plan(["LITX"], dry_run=True)
    signal = plan["signals"][0]
    factors = {row["name"] for row in signal["metadata"]["factor_breakdown"]}
    relations = plan["strategy_scout"]["relationships"]

    assert signal["symbol"] == "LITX"
    assert any(row["source_symbol"] == "LITX" and row["related_symbol"] == "LITE" for row in relations)
    assert "underlying_relation_strength" in factors
    assert "intraday_followthrough" in factors


def test_strategy_plan_non_dry_run_enqueues_paper_buy(tmp_path):
    config = write_strategy_config(tmp_path)
    BacktestStore(config.data_dir / config.backtest.sqlite_path).save(good_backtest(trades=2))
    engine = ResearchEngine(config, finnhub=GoodFinnhub(), tradier=EmptyTradier(), x_api=EmptyX())

    plan = engine.generate_strategy_plan(["POET"], dry_run=False)
    jobs = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path)).jobs(limit=20)

    assert plan["paper_order_jobs"]
    assert any(job.type == "paper_buy" for job in jobs)


def test_strategy_roundtable_persists_views(tmp_path):
    config = write_strategy_config(tmp_path)
    engine = ResearchEngine(config, finnhub=GoodFinnhub(), tradier=EmptyTradier(), x_api=EmptyX())

    memo = engine.generate_strategy_roundtable(dry_run=False)
    memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=True)
    runs = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path)).recent_agent_runs(agent_name="strategy_roundtable", limit=1)

    assert "Research Agent" in memo
    assert "Risk Agent" in memo
    assert memory.recent(kind="strategy_roundtable", limit=1)
    assert runs and runs[0]["status"] == "done"


def test_source_config_change_restores_and_refuses_restart_on_failed_validation(tmp_path):
    config = write_strategy_config(
        tmp_path,
        """
agent_harness:
  auto_source_config_enabled: true
  auto_restart_after_source_config: true
""",
    )
    original = config.config_path.read_text(encoding="utf-8")

    result = SourceConfigManager(config).apply_updates({"research.web_search_timeout_seconds": "bad"}, dry_run=False, restart=True)

    assert not result.validation_ok
    assert not result.restart_ok
    assert config.config_path.read_text(encoding="utf-8") == original
