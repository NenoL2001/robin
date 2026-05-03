# Data Contracts

Robin vNext uses pydantic contracts as the stable boundary between stages.

- Bronze: `RawDocument` with event/published/ingested clocks and append-only parquet layout.
- Silver: `CanonicalDocument` with normalized URL, text, language, source hash, lineage.
- Entity: `EntityMention` and `CanonicalEntity` resolved through `SecurityMaster`.
- Facts: `EventRecord`, `ClaimRecord`, and `EvidencePacket`.
- Features: `OpSpec`, `OpRunContext`, `OpExecutionMetadata`, `FactorDefinition`, `FactorValueDaily`.
- Decisions: `StrategyDecision` and `ExecutionReport`.

All local artifacts must carry ids, schema version, lineage, and created timestamp. Missing external source, broker, model, or budget assumptions must be represented as `UNSPECIFIED`.
