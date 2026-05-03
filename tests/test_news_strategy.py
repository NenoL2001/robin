from __future__ import annotations

from datetime import datetime, timezone

from portfolio_bot.config import load_config
from portfolio_bot.market.daily_digest import DailyNewsDigestBuilder
from portfolio_bot.market.news_strategy import build_news_query_plan, enrich_web_evidence, news_quality_score
from portfolio_bot.models import WebEvidence
from portfolio_bot.strategies.factor_specs import FactorSpec
from portfolio_bot.strategies.factor_validation import validate_factor_flow


def test_news_query_plan_expands_snxx_to_official_sndk_research() -> None:
    plan = build_news_query_plan(["SNXX"], max_queries=8, official_sources_first=True)
    queries = [item.query for item in plan]

    assert any("investor.sandisk.com" in query for query in queries)
    assert any("HBF" in query or "High Bandwidth Flash" in query for query in queries)
    assert any(item.symbol == "SNDK" for item in plan)
    assert any(item.symbol == "SNXX" for item in plan)


def test_enrich_web_evidence_tags_source_events_quality_and_related_symbol() -> None:
    item = WebEvidence(
        title="SNDK reports earnings beat and raises guidance",
        url="https://investor.sandisk.com/node/7896/pdf",
        source="investor.sandisk.com",
        query="SNDK earnings",
        symbols=["SNDK"],
        summary="Revenue was above guidance, data center demand improved, and HBF sampling accelerated.",
        published_at=datetime.now(timezone.utc),
        confidence=0.4,
    )

    enriched = enrich_web_evidence(item, ["SNXX"])
    news_item = enriched.to_news_item()
    quality, reasons = news_quality_score(news_item, "SNDK")

    assert {"SNDK", "SNXX"} <= set(enriched.symbols)
    assert enriched.raw["source_tier"] == "P0_official"
    assert {"earnings_surprise", "guidance_revision", "hbf_ai_inference_moat"} <= set(enriched.raw["event_types"])
    assert quality >= 0.8
    assert "P0_official" in reasons


class RecordingWebSearch:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query, *, symbols=None, commit=True):
        self.queries.append(query)
        if "investor.sandisk.com" in query:
            return [
                WebEvidence(
                    title="SNDK official earnings beat",
                    url="https://investor.sandisk.com/node/7896/pdf",
                    source="investor.sandisk.com",
                    query=query,
                    symbols=["SNDK"],
                    summary="Earnings beat, guidance revision, and data center revenue growth.",
                    published_at=datetime.now(timezone.utc),
                    confidence=0.5,
                    relevance_score=0.7,
                    provider="test",
                )
            ]
        return [
            WebEvidence(
                title="Generic SNDK market movers",
                url="https://finance.yahoo.com/generic",
                source="Yahoo",
                query=query,
                symbols=["SNDK"],
                summary="Generic market movers and stocks to buy.",
                published_at=datetime.now(timezone.utc),
                confidence=0.35,
                relevance_score=0.4,
                provider="test",
            )
        ]


class EmptyDataHub:
    def collect_news(self, symbols, days=3, commit=True):
        return []


def test_daily_digest_uses_structured_query_plan_and_ranks_official(tmp_path) -> None:
    config = write_config(tmp_path)
    config.research.web_search_enabled = True
    recorder = RecordingWebSearch()

    digest = DailyNewsDigestBuilder(config, data_hub=EmptyDataHub(), web_search=recorder).build(["SNXX"], commit=False, include_web=True)

    assert len(recorder.queries) > 1
    assert digest.metadata["query_log"]
    assert digest.items[0].url == "https://investor.sandisk.com/node/7896/pdf"
    assert digest.items[0].raw["source_tier"] == "P0_official"
    assert "已执行新闻 query" in digest.summary


def test_factor_flow_validation_catches_missing_gate_and_proposes_factors() -> None:
    plan = {
        "signals": [
            {
                "symbol": "SNDK",
                "score": 75,
                "metadata": {
                    "factor_breakdown": [{"name": "earnings_surprise", "contribution": 14}],
                    "evidence_links": ["https://investor.sandisk.com/node/7896/pdf"],
                },
            }
        ]
    }
    result = validate_factor_flow(
        plan,
        [FactorSpec(name="earnings_surprise", weight=14, query_terms=["earnings"])],
        proposed_factors=[],
        attribution_summary=[],
        dry_run=True,
    )

    assert not result.ok
    assert any("risk gate" in issue.message for issue in result.issues)


def write_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
timezone: UTC
research:
  web_search_enabled: true
strategy_research:
  max_queries_per_strategy: 6
memory:
  enabled: false
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    (tmp_path / "analysts.yaml").write_text("analysts: []\n", encoding="utf-8")
    (tmp_path / "strategy_skills").mkdir(exist_ok=True)
    return load_config(config_path)
