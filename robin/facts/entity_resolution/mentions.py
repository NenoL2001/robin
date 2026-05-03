from __future__ import annotations

import re

from robin.contracts.canonical_document import CanonicalDocument
from robin.contracts.entity import EntityMention
from robin.core.ids import stable_id


SYMBOL_PATTERN = re.compile(r"(?<![A-Za-z0-9])\$?([A-Z][A-Z0-9]{1,5})(?![A-Za-z0-9])")


def extract_mentions(document: CanonicalDocument) -> list[EntityMention]:
    mentions: list[EntityMention] = []
    for match in SYMBOL_PATTERN.finditer(f"{document.title} {document.text}"):
        text = match.group(1).upper()
        mention_id = stable_id("mention", {"doc": document.id, "text": text, "start": match.start()})
        mentions.append(
            EntityMention(
                id=mention_id,
                canonical_document_id=document.id,
                text=text,
                start_char=match.start(),
                end_char=match.end(),
                candidate_type="security_symbol",
                confidence=0.65,
                lineage=[document.id],
            )
        )
    return mentions
