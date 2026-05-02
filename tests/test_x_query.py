from portfolio_bot.data.x_api import XApiClient, build_x_query, infer_macro_symbols, infer_macro_topics


def test_build_x_query_includes_handles_keywords_and_cashtags():
    query = build_x_query(
        {
            "analysts": [{"handle": "@semiwatch"}],
            "keywords": ["silicon photonics", "HBM"],
            "cashtags": ["POET", "INTC"],
        }
    )
    assert "from:semiwatch" in query
    assert '"silicon photonics"' in query
    assert "$POET" in query
    assert "-is:retweet" in query


def test_build_x_query_includes_macro_topics_and_known_handles():
    query = build_x_query(
        {
            "analysts": [{"handle": "@aleabitoreddit"}, {"handle": "leopoldasch"}],
            "keywords": ["semiconductor"],
            "cashtags": ["NVDA"],
            "macro_topics": {"ai_compute": ["AI compute", "training cluster"]},
        }
    )

    assert "from:aleabitoreddit" in query
    assert "from:leopoldasch" in query
    assert '"AI compute"' in query
    assert '"training cluster"' in query
    assert "$NVDA" in query


def test_macro_topic_symbol_inference_for_uncashtagged_posts():
    config = {
        "macro_topics": {"ai_compute": ["AI compute", "training cluster"]},
        "macro_topic_symbols": {"ai_compute": ["NVDA", "AMD"]},
    }

    topics = infer_macro_topics("Frontier lab AI compute needs another training cluster", config)

    assert topics == ["ai_compute"]
    assert infer_macro_symbols(topics, config) == ["NVDA", "AMD"]


def test_x_client_infers_symbols_and_returns_empty_without_token():
    assert XApiClient("").recent_semiconductor_posts({"analysts": [{"handle": "leopoldasch"}]}) == []

    class FakeX(XApiClient):
        def _get(self, path, params):
            return {
                "data": [
                    {
                        "id": "1",
                        "author_id": "u1",
                        "created_at": "2026-04-30T12:00:00Z",
                        "text": "AI compute demand needs larger training cluster and data center buildout",
                    }
                ],
                "includes": {"users": [{"id": "u1", "username": "leopoldasch"}]},
            }

    items = FakeX("token").recent_semiconductor_posts(
        {
            "analysts": [{"handle": "leopoldasch"}],
            "macro_topics": {"ai_compute": ["AI compute", "training cluster"]},
            "macro_topic_symbols": {"ai_compute": ["NVDA", "AMD"]},
        }
    )

    assert items[0].symbols == ["AMD", "NVDA"]
    assert items[0].raw["handle"] == "leopoldasch"
    assert items[0].raw["macro_topics"] == ["ai_compute"]
    assert items[0].raw["low_confidence_symbol_inference"] is True
