from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from robin.contracts.raw_document import RawDocument
from robin.ingest.sources.registry import SourceConfig


@dataclass(slots=True)
class FetchFailure:
    source_id: str
    reason: str
    retryable: bool = True


class Fetcher(Protocol):
    def fetch(self, source: SourceConfig) -> tuple[list[RawDocument], list[FetchFailure]]:
        ...
