from datetime import datetime, timezone

from portfolio_bot.market.news_analysis import analyze_news_items, format_analyzed_news_section, split_sentences
from portfolio_bot.market.metrics import keyword_matches_text, news_relevance, symbol_matches_text
from portfolio_bot.models import Holding, NewsItem


def test_news_section_marks_finnhub_links_as_via_source():
    item = NewsItem(
        title="Hims & Hers announces GLP-1 partnership",
        url="https://finnhub.io/api/news?id=abc",
        source="Yahoo",
        published_at=datetime.now(timezone.utc),
        symbols=["HIMS"],
        summary="Hims & Hers announced a partnership that expands GLP-1 offerings.",
        kind="company_news",
    )

    analyzed = analyze_news_items([item], [Holding(symbol="HIMS", name="Hims & Hers", quantity=1)], {"HIMS"}, min_relevance=0.55)
    section = format_analyzed_news_section(analyzed)

    assert "Yahoo via Finnhub" in section
    assert "发生了什么" in section
    assert "Hims & Hers announced a partnership" in section


def test_low_relevance_generic_news_is_filtered_from_research_bucket():
    items = [
        NewsItem(
            title="3 Market-Beating Stocks to Research Further",
            url="https://finnhub.io/api/news?id=generic",
            source="Yahoo",
            published_at=datetime.now(timezone.utc),
            symbols=["HIMS"],
            summary="A broad market list with no direct semiconductor catalyst.",
        ),
        NewsItem(
            title="POET announces silicon photonics customer qualification",
            url="https://finnhub.io/api/news?id=poet",
            source="Yahoo",
            published_at=datetime.now(timezone.utc),
            symbols=["POET"],
            summary="POET completed customer qualification for optical engines.",
        ),
    ]

    analyzed = analyze_news_items(items, [], {"POET"}, min_relevance=0.55)
    section = format_analyzed_news_section(analyzed)

    assert "POET announces" in section
    assert "3 Market-Beating Stocks" not in section


def test_short_ticker_on_requires_symbol_or_company_match():
    hims_item = NewsItem(
        title="JPMorgan initiates Hims & Hers with Overweight rating",
        url="https://finnhub.io/api/news?id=hims",
        source="Yahoo",
        published_at=datetime.now(timezone.utc),
        symbols=["HIMS"],
        summary="Coverage starts on the telehealth company after a new partnership.",
    )
    on_item = NewsItem(
        title="ON Semiconductor announces customer qualification",
        url="https://finnhub.io/api/news?id=on",
        source="Yahoo",
        published_at=datetime.now(timezone.utc),
        symbols=["ON"],
        summary="ON Semiconductor completed a customer qualification for power devices.",
    )

    assert not symbol_matches_text("ON", hims_item.title)
    assert news_relevance(hims_item, "ON") < 0.45
    assert news_relevance(on_item, "ON") >= 0.55


def test_short_keywords_do_not_match_inside_words():
    assert not keyword_matches_text("ai", "top gainers in today's session")
    assert not keyword_matches_text("ip", "GLP-1 partnership analysis")
    assert not keyword_matches_text("sic", "fresh analyst coverage")
    assert not keyword_matches_text("test", "latest analysis")
    assert keyword_matches_text("ai", "AI server demand")


def test_split_sentences_keeps_decimal_percentages_together():
    parts = split_sentences("Shares jumped 9.6% after coverage. Price target is $35.")

    assert parts[0] == "Shares jumped 9.6% after coverage."
