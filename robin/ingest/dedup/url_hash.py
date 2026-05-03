from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from robin.core.ids import content_hash


TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}


def canonical_url(url: str) -> str:
    parsed = urlparse(url or "")
    query = urlencode([(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key not in TRACKING_PARAMS])
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", query, ""))


def document_dedupe_key(url: str, title: str, body: str) -> str:
    normalized_url = canonical_url(url)
    if normalized_url:
        return content_hash(normalized_url)
    return content_hash(f"{title}\n{body}")
