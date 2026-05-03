from __future__ import annotations

from robin.contracts.canonical_document import CanonicalDocument
from robin.contracts.raw_document import RawDocument
from robin.core.ids import stable_id
from robin.ingest.dedup.url_hash import canonical_url


def canonicalize_document(raw: RawDocument) -> CanonicalDocument:
    text = " ".join((raw.body or raw.title or "").split())
    payload = {"raw": raw.id, "source_hash": raw.source_hash, "text": text}
    return CanonicalDocument(
        id=stable_id("candoc", payload),
        raw_document_id=raw.id,
        source_id=raw.source_id,
        canonical_url=canonical_url(raw.url),
        title=" ".join(raw.title.split()),
        text=text,
        language=str(raw.metadata.get("language", "en")),
        source_hash=raw.source_hash,
        event_time=raw.event_time,
        published_time=raw.published_time,
        ingested_time=raw.ingested_time,
        lineage=[raw.id],
        metadata={"source_tier": raw.source_tier.value, **raw.metadata},
    )
