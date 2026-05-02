from datetime import datetime, timedelta, timezone

from portfolio_bot.config import ResearchConfig
from portfolio_bot.models import Holding, NewsItem, OptionContract, Quote
from portfolio_bot.strategies.registry import load_strategies
from portfolio_bot.strategies.semiconductor_reversal import SemiconductorReversalStrategy


def test_semiconductor_score_rewards_catalysts():
    strategy = SemiconductorReversalStrategy()
    news = [
        NewsItem(
            title="Company announces silicon photonics design win and raises guidance",
            url="https://example.com",
            source="test",
            symbols=["POET"],
            summary="Customer qualification complete with revenue growth expected.",
        )
    ]
    quote = Quote(symbol="POET", price=5.0, timestamp=datetime.now(timezone.utc), change_percent=4.0)
    score = strategy.evaluate("POET", quote, news)
    assert score.score > 55
    assert "design win" in score.catalysts
    assert score.confidence > 0.25


def test_strategy_keyword_fallback_uses_boundaries():
    strategy = SemiconductorReversalStrategy()
    news = [
        NewsItem(
            title="Telehealth company expands GLP-1 offerings",
            url="https://example.com",
            source="test",
            symbols=["HIMS"],
            summary="Latest analysis mentions top gainers but no financing or confirmed catalyst.",
        )
    ]
    score = strategy.evaluate("HIMS", None, news)

    assert "public offering" not in score.risk_flags
    assert "stock offering" not in score.risk_flags
    assert "ai" not in score.catalysts
    assert not score.metadata["chain_hits"]


def test_strategy_uses_portfolio_context_factor():
    strategy = SemiconductorReversalStrategy(holdings=[Holding(symbol="POET", asset_type="equity", quantity=10, market_value=120)])
    news = [
        NewsItem(
            title="POET announces silicon photonics design win",
            url="https://example.com",
            source="test",
            symbols=["POET"],
            summary="Customer qualification complete.",
        )
    ]
    quote = Quote(symbol="POET", price=12.0, timestamp=datetime.now(timezone.utc), change_percent=1.0)

    score = strategy.evaluate("POET", quote, news)

    factors = {row["name"]: row for row in score.metadata["factor_breakdown"]}
    assert "portfolio_context" in factors
    assert factors["portfolio_context"]["contribution"] > 0


def test_strategy_rewards_serenity_chain_and_leopold_compute_readthrough():
    strategy = SemiconductorReversalStrategy()
    news = [
        NewsItem(
            title="HBM and advanced packaging supply chain follow-through",
            url="https://x.com/i/web/status/1",
            source="X",
            symbols=["NVDA"],
            summary="Memory cycle, AI storage, and book-to-bill data are improving across the semiconductor supply chain.",
            raw={"handle": "aleabitoreddit", "macro_topics": ["semiconductor_chain"]},
        ),
        NewsItem(
            title="AI compute demand needs larger training cluster",
            url="https://x.com/i/web/status/2",
            source="X",
            symbols=["NVDA"],
            summary="Frontier lab GPU cluster and inference demand imply stronger data center buildout.",
            raw={"handle": "leopoldasch", "macro_topics": ["ai_compute"]},
        ),
    ]
    quote = Quote(symbol="NVDA", price=100.0, timestamp=datetime.now(timezone.utc), change_percent=2.0)

    score = strategy.evaluate("NVDA", quote, news)
    factors = {row["name"]: row for row in score.metadata["factor_breakdown"]}

    assert "serenity_chain_readthrough" in factors
    assert "leopold_compute_demand" in factors
    assert score.metadata["source_handles"] == ["aleabitoreddit", "leopoldasch"]
    assert score.score > 60


def test_strategy_flags_ai_compute_macro_risks():
    strategy = SemiconductorReversalStrategy()
    news = [
        NewsItem(
            title="AI compute capex pause as export controls tighten",
            url="https://x.com/i/web/status/3",
            source="X",
            symbols=["NVDA"],
            summary="Power bottleneck and data center bottleneck delay new GPU cluster deployments.",
            raw={"handle": "leopoldasch", "macro_topics": ["export_controls", "data_center_power"]},
        )
    ]

    score = strategy.evaluate("NVDA", None, news)
    factors = {row["name"]: row for row in score.metadata["factor_breakdown"]}

    assert "ai_compute_macro_risk" in factors
    assert "export controls" in score.risk_flags
    assert "power bottleneck" in score.risk_flags


def test_long_call_filter_and_ranking():
    strategy = SemiconductorReversalStrategy(ResearchConfig(option_min_days=180, option_max_days=548, option_max_premium=1500))
    now = datetime.now(timezone.utc)
    contracts = [
        OptionContract(
            underlying="POET",
            symbol="POET260117C00013000",
            expiration=now + timedelta(days=300),
            strike=13,
            option_type="call",
            bid=3.3,
            ask=3.7,
            delta=0.42,
            implied_volatility=0.8,
            open_interest=120,
        ),
        OptionContract(
            underlying="POET",
            symbol="POET260117P00013000",
            expiration=now + timedelta(days=300),
            strike=13,
            option_type="put",
            bid=3.3,
            ask=3.7,
        ),
        OptionContract(
            underlying="POET",
            symbol="POET270117C00013000",
            expiration=now + timedelta(days=700),
            strike=13,
            option_type="call",
            bid=3.3,
            ask=3.7,
        ),
    ]
    quote = Quote(symbol="POET", price=6.5, timestamp=now)
    candidates = strategy.rank_options("POET", quote, contracts, [])
    assert len(candidates) == 1
    assert candidates[0].contract.symbol == "POET260117C00013000"
    assert candidates[0].contract.premium == 350


def test_candidate_strategy_does_not_emit_active_signals(tmp_path):
    skill_dir = tmp_path / "semiconductor_reversal"
    skill_dir.mkdir(parents=True)
    (skill_dir / "strategy.yaml").write_text(
        """
name: semiconductor_reversal
version: 1.0.0
status: candidate
description: candidate only
""",
        encoding="utf-8",
    )

    assert load_strategies(tmp_path, ResearchConfig()) == []


def test_active_strategy_loads_from_calculation(tmp_path):
    skill_dir = tmp_path / "semiconductor_reversal"
    skill_dir.mkdir(parents=True)
    (skill_dir / "strategy.yaml").write_text(
        """
name: semiconductor_reversal
version: 1.0.0
status: active
description: active dynamic load
calculation:
  module: portfolio_bot.strategies.semiconductor_reversal
  class: SemiconductorReversalStrategy
""",
        encoding="utf-8",
    )

    strategies = load_strategies(tmp_path, ResearchConfig())

    assert len(strategies) == 1
    assert isinstance(strategies[0], SemiconductorReversalStrategy)
