from portfolio_bot.models import NewsItem
from portfolio_bot.storage import dedupe_news


def test_dedupe_news_by_url():
    seen = set()
    items = [
        NewsItem(title="A", url="https://example.com/a", source="x"),
        NewsItem(title="A again", url="https://example.com/a", source="y"),
    ]
    fresh = dedupe_news(items, seen)
    assert len(fresh) == 1
    assert "https://example.com/a" in seen
