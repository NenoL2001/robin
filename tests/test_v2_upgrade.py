from __future__ import annotations

from datetime import datetime, timedelta, timezone

from portfolio_bot.config import load_config
from portfolio_bot.market.bars import BarSnapshot, BarStore
from portfolio_bot.market.evidence_ranker import EvidenceRanker
from portfolio_bot.market.features import FeatureEngine
from portfolio_bot.market.relations import RelationGraph, StoredSymbolRelation
from portfolio_bot.market.report_verifier import ReportVerifier
from portfolio_bot.market.strategy_news_scout import extract_symbol_relationships
from portfolio_bot.models import Holding, NewsItem, Quote, StrategySignal, WebEvidence
from portfolio_bot.runtime import RuntimeStore
from portfolio_bot.strategies.factor_attribution import FactorAttributionStore


def test_bar_aware_behavior_uses_history(tmp_path):
    runtime = RuntimeStore(tmp_path / "runtime.sqlite")
    bars = BarStore(tmp_path / "bars.sqlite")
    now = datetime(2026, 5, 3, tzinfo=timezone.utc)
    closes = [100.0, 103.0, 106.0, 109.0, 112.0]
    for index, close in enumerate(closes):
        bars.upsert(
            BarSnapshot(
                symbol="LITX",
                window="1d",
                timestamp=now - timedelta(days=5 - index),
                open=close - 1,
                high=close + 2,
                low=close - 3,
                close=close,
                volume=1000 + index * 100,
            )
        )
    quote = Quote("LITX", 112.0, now, change_percent=1.0, previous_close=109.0, volume=1800)

    features = FeatureEngine(runtime, bar_store=bars).compute_symbol("LITX", quote, [], commit=False)

    behavior = features["daily_behavior"]
    assert behavior["history_missing"] is False
    assert behavior["history_bar_count"] == 5
    assert behavior["return_3d"] > 0
    assert behavior["relative_volume"] > 1
    assert "history_missing" not in behavior["flags"]


def test_bar_behavior_marks_missing_history(tmp_path):
    runtime = RuntimeStore(tmp_path / "runtime.sqlite")
    quote = Quote("AAOI", 10.0, datetime.now(timezone.utc), change_percent=2.0, previous_close=9.8, volume=0)

    features = FeatureEngine(runtime).compute_symbol("AAOI", quote, [], commit=False)

    assert features["daily_behavior"]["history_missing"] is True
    assert "history_missing" in features["behavior_flags"]


def test_relation_extraction_and_graph_persistence(tmp_path):
    evidence = [
        WebEvidence(
            title="GraniteShares 2x Long LITE Daily ETF (LITX)",
            url="https://stockanalysis.com/etf/litx/",
            source="StockAnalysis",
            query="LITX underlying ETF 2x long fund prospectus",
            symbols=["LITX"],
            summary="The fund seeks two times daily performance of Lumentum Holdings Inc. ticker LITE. Product nav also lists SNXX 2X SNDK and LRCU 2X LRCX.",
            confidence=0.75,
        )
    ]

    relationships = extract_symbol_relationships(evidence, ["LITX", "SNDK", "SNXX"])
    graph = RelationGraph(tmp_path / "relations.sqlite")
    stored = graph.upsert_many_from_scout(relationships, remember=False)

    assert any(item.source_symbol == "LITX" and item.related_symbol == "LITE" for item in stored)
    assert not any(item.source_symbol == "SNDK" for item in stored)
    persisted = graph.relationships_for(["LITX"])
    assert persisted[0].related_symbol == "LITE"
    assert persisted[0].multiplier == 2.0


def test_evidence_ranking_prefers_direct_official_evidence(tmp_path):
    config = write_config(tmp_path)
    official = NewsItem(
        "Sandisk Reports Fiscal Third Quarter 2026 Financial Results",
        "https://investor.sandisk.com/node/7896/pdf",
        "investor.sandisk.com",
        datetime(2026, 4, 30, tzinfo=timezone.utc),
        ["SNDK"],
        "Q3 revenue above guidance; datacenter revenue up sharply.",
        raw={"source_tier": "official", "event_types": ["earnings_surprise", "guidance_revision"]},
    )
    generic = NewsItem(
        "Yahoo market movers: NVDA and stocks to buy",
        "https://finance.yahoo.com/example",
        "Yahoo",
        datetime(2026, 5, 3, tzinfo=timezone.utc),
        ["SNDK"],
        "Generic biggest moves article with NVDA and market-beating stocks to buy.",
    )

    ranked = EvidenceRanker(config).rank_news([generic, official], ["SNDK"], commit=False)

    assert ranked[0].url == official.url
    assert ranked[0].score > ranked[-1].score


def test_report_verifier_blocks_common_report_failures(tmp_path):
    config = write_config(tmp_path)
    holdings = [Holding("SNXX", asset_type="equity", quantity=655.58, market_value=76000.0)]
    relationships = [
        StoredSymbolRelation(
            "SNXX",
            "SNDK",
            "leveraged_underlying",
            multiplier=2.0,
            confidence=0.95,
            evidence_title="static",
        )
    ]
    report = "2026-05-02\nSNXX 市值 $655.58。\n未发现公开新闻，待查证。"

    result = ReportVerifier(config).verify(
        report,
        holdings,
        relationships,
        query_log=[],
        now=datetime(2026, 5, 3, tzinfo=timezone.utc),
        commit=False,
    )

    assert result.blocked is True
    assert any("报告日期" in error for error in result.errors)
    assert any("quantity" in error for error in result.errors)
    assert any("底层关系" in error for error in result.errors)
    assert any("query log" in error for error in result.errors)


def test_factor_attribution_records_and_summarizes_forward_return(tmp_path):
    store = FactorAttributionStore(tmp_path / "factor.sqlite")
    signal = StrategySignal(
        signal_id="sig-1",
        symbol="SNDK",
        strategy_name="semiconductor_reversal",
        strategy_version="1.0.0",
        action="paper_buy",
        score=80,
        confidence=0.8,
        reason="test",
        metadata={"factor_breakdown": [{"name": "earnings_surprise", "value": 1, "contribution": 14.0}]},
    )
    entry = Quote("SNDK", 100.0, datetime.now(timezone.utc))
    forward = Quote("SNDK", 110.0, datetime.now(timezone.utc))

    assert store.record_signal(signal, entry, remember=False) == 1
    assert store.update_forward_returns({"SNDK": forward}, horizon="1d", remember=False) == 1
    summary = store.summary(horizon="1d", min_observations=1)

    assert summary[0].factor_name == "earnings_surprise"
    assert summary[0].avg_forward_return == 10.0
    assert summary[0].directional_score == 10.0


def write_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    (tmp_path / "strategy_skills").mkdir()
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
notifications:
  imessage_enabled: false
memory:
  enabled: false
""",
        encoding="utf-8",
    )
    return load_config(config_path)
