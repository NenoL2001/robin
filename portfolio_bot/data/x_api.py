from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from ..models import NewsItem


class XApiClient:
    def __init__(self, bearer_token: str, timeout: int = 10):
        self.bearer_token = bearer_token
        self.timeout = timeout
        self.base_url = "https://api.x.com/2"

    @property
    def configured(self) -> bool:
        return bool(self.bearer_token)

    def recent_semiconductor_posts(self, analyst_config: dict[str, Any], max_results: int = 25) -> list[NewsItem]:
        if not self.configured:
            return []
        query = build_x_query(analyst_config)
        if not query:
            return []
        data = self._get(
            "/tweets/search/recent",
            {
                "query": query,
                "max_results": max(10, min(max_results, 100)),
                "tweet.fields": "created_at,author_id,public_metrics,entities",
                "expansions": "author_id",
                "user.fields": "username",
            },
        )
        posts = data.get("data", [])
        users = {
            str(user.get("id", "")): str(user.get("username", "")).lstrip("@")
            for user in data.get("includes", {}).get("users", [])
            if isinstance(user, dict)
        }
        items: list[NewsItem] = []
        for row in posts:
            tweet_id = row.get("id", "")
            created = row.get("created_at")
            published = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None
            text = str(row.get("text", ""))
            handle = users.get(str(row.get("author_id", "")), "")
            cashtags = _extract_cashtags(text)
            macro_topics = infer_macro_topics(text, analyst_config)
            inferred_symbols = infer_macro_symbols(macro_topics, analyst_config)
            symbols = sorted({*cashtags, *inferred_symbols})
            items.append(
                NewsItem(
                    title=text[:180],
                    url=f"https://x.com/i/web/status/{tweet_id}" if tweet_id else "",
                    source="X",
                    published_at=published,
                    symbols=symbols,
                    summary=text,
                    kind="x_post",
                    raw={
                        **row,
                        "handle": handle,
                        "macro_topics": macro_topics,
                        "inferred_symbols": inferred_symbols,
                        "cashtags": cashtags,
                        "low_confidence_symbol_inference": bool(macro_topics and inferred_symbols and not cashtags),
                    },
                )
            )
        return items

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = requests.get(
            self.base_url + path,
            params=params,
            headers={"Authorization": f"Bearer {self.bearer_token}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()


def build_x_query(config: dict[str, Any]) -> str:
    handles = [
        str(item.get("handle", "")).lstrip("@")
        for item in config.get("analysts", [])
        if isinstance(item, dict) and item.get("handle")
    ]
    handle_terms = [f"from:{handle}" for handle in handles[:25]]
    keywords = [f'"{kw}"' if " " in str(kw) else str(kw) for kw in topic_keywords(config) if kw]
    cashtags = [f"${str(tag).upper().lstrip('$')}" for tag in config.get("cashtags", []) if tag]
    groups = []
    if handle_terms:
        groups.append("(" + " OR ".join(handle_terms) + ")")
    topic_terms = keywords[:20] + cashtags[:30]
    if topic_terms:
        groups.append("(" + " OR ".join(topic_terms) + ")")
    if not groups:
        return ""
    return " ".join(groups) + " -is:retweet lang:en"


def topic_keywords(config: dict[str, Any]) -> list[str]:
    values = [str(item) for item in config.get("keywords", []) if item]
    macro_topics = config.get("macro_topics", {})
    if isinstance(macro_topics, dict):
        for keywords in macro_topics.values():
            if isinstance(keywords, list):
                values.extend(str(item) for item in keywords if item)
    return dedupe_preserve_order(values)


def infer_macro_topics(text: str, config: dict[str, Any]) -> list[str]:
    from ..market.metrics import keyword_matches_text

    lowered = text.lower()
    macro_topics = config.get("macro_topics", {})
    if not isinstance(macro_topics, dict):
        return []
    matches: list[str] = []
    for topic, keywords in macro_topics.items():
        if not isinstance(keywords, list):
            continue
        if any(keyword_matches_text(str(keyword), lowered) for keyword in keywords if keyword):
            matches.append(str(topic))
    return matches


def infer_macro_symbols(topics: list[str], config: dict[str, Any]) -> list[str]:
    topic_symbols = config.get("macro_topic_symbols", {})
    if not isinstance(topic_symbols, dict):
        return []
    symbols: list[str] = []
    for topic in topics:
        values = topic_symbols.get(topic, [])
        if isinstance(values, list):
            symbols.extend(str(item).upper().lstrip("$") for item in values if item)
    return dedupe_preserve_order(symbols)


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _extract_cashtags(text: str) -> list[str]:
    symbols: list[str] = []
    for token in text.replace("\n", " ").split():
        if token.startswith("$") and len(token) > 1:
            symbols.append(token[1:].strip(".,:;!?()[]{}").upper())
    return symbols
