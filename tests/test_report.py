from pathlib import Path
from datetime import datetime, timezone

from portfolio_bot.config import load_config
from portfolio_bot.market.news_analysis import analyze_news_items
from portfolio_bot.models import Holding, NewsItem, Quote
from portfolio_bot.research import ResearchEngine


class EmptyFinnhub:
    def quote(self, symbol):
        return None

    def company_news(self, symbol, start, end):
        return []


class NewsFinnhub:
    def quote(self, symbol):
        return Quote(symbol=symbol.upper(), price=12.0, timestamp=datetime.now(timezone.utc), change_percent=4.2)

    def company_news(self, symbol, start, end):
        return [
            NewsItem(
                title=f"{symbol} announces silicon photonics design win",
                url=f"https://example.com/{symbol}",
                source="test",
                symbols=[symbol.upper()],
                summary="Customer qualification and major contract.",
            )
        ]


class EmptyTradier:
    configured = False


class EmptyX:
    def recent_semiconductor_posts(self, analyst_config):
        return []


def test_daily_report_dry_run_is_chinese_and_separates_real_and_paper(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
research:
  default_universe:
    - POET
memory:
  enabled: true
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    (tmp_path / "analysts.yaml").write_text("analysts: []\n", encoding="utf-8")
    config = load_config(config_path)
    engine = ResearchEngine(config, finnhub=EmptyFinnhub(), tradier=EmptyTradier(), x_api=EmptyX())

    report = engine.generate_daily_report(
        [
            Holding(symbol="AEHR", asset_type="equity", quantity=102.38, market_value=9847),
            Holding(
                symbol="POET_2027-01-15_13C",
                name="POET 13C 2027-01-15",
                asset_type="option",
                quantity=3,
                market_value=1785,
                metadata={"underlying": "POET", "expiration": "2027-01-15", "strike": 13},
            ),
        ],
        dry_run=True,
    )

    for section in [
        "今日组合概览",
        "大行情和重大新闻",
        "当前真实持仓观察",
        "模拟组合净值和盈亏",
        "策略 Skill 表现",
        "候选公司详解",
        "长期期权候选",
        "半导体链路机会",
        "今日复盘和明日关注",
        "新策略候选/是否需要创建新 Skill",
    ]:
        assert section in report
    assert "真实持仓与模拟持仓分开展示" in report
    assert "POET:" in report
    assert "持仓:" in report
    assert "当前状态=active" in report
    assert Path(config.strategy_root / "semiconductor_reversal" / "review_memory.jsonl").exists()


def test_research_brief_formats_news_and_company_detail(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
research:
  default_universe:
    - POET
memory:
  enabled: true
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    (tmp_path / "analysts.yaml").write_text("analysts: []\n", encoding="utf-8")
    config = load_config(config_path)
    engine = ResearchEngine(config, finnhub=NewsFinnhub(), tradier=EmptyTradier(), x_api=EmptyX())

    brief = engine.generate_research_brief(
        ["POET"],
        holdings=[Holding(symbol="POET", asset_type="equity", quantity=10, market_value=120)],
        dry_run=True,
    )

    assert "主动新闻整理与公司分析" in brief
    assert "新闻整理" in brief
    assert "候选公司详解" in brief
    assert "POET:" in brief
    assert "design win" in brief


def test_daily_report_maps_snxx_to_sndk_leveraged_exposure(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
research:
  default_universe: []
memory:
  enabled: true
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    (tmp_path / "analysts.yaml").write_text("analysts: []\n", encoding="utf-8")
    config = load_config(config_path)
    engine = ResearchEngine(config, finnhub=NewsFinnhub(), tradier=EmptyTradier(), x_api=EmptyX())

    report = engine.generate_daily_report(
        [Holding(symbol="SNXX", name="SNXX", asset_type="equity", quantity=10, market_value=1000)],
        dry_run=True,
    )

    assert "SNXX" in report
    assert "杠杆暴露 2x 做多 SNDK" in report
    assert "SNDK:" in report


def test_news_analysis_treats_sndk_as_snxx_holding_exposure():
    items = analyze_news_items(
        [
            NewsItem(
                title="SNDK announces major contract",
                url="https://example.com/sndk",
                source="test",
                symbols=["SNDK"],
                summary="SanDisk customer qualification and major contract.",
            )
        ],
        [Holding(symbol="SNXX", name="SNXX", asset_type="equity", quantity=10, market_value=1000)],
        {"SNXX", "SNDK"},
    )

    assert items[0].relation == "real_holding"
    assert "SNXX" in items[0].portfolio_impact
    assert "2x 做多 SNDK" in items[0].portfolio_impact
