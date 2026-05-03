# RFC: Robin vNext Layered Research and Strategy System

## Status
Accepted for staged implementation. This RFC defines the vNext architecture that coexists with the current `portfolio_bot` package until migration is complete.

## Problem
The existing system still has too much logic in the path `collect news -> summarize with LLM -> report/strategy`. That makes facts hard to replay, factor quality hard to audit, and strategy iteration dependent on prompt text. vNext changes the system into a local-first layered pipeline:

`raw inputs -> bronze documents -> canonical documents -> entity/event/claim verification -> evidence packets -> factors -> experiments/backtests -> strategy decisions -> constrained LLM synthesis`

LLMs may explain and synthesize verified artifacts, but must not be the source of truth for facts, factor values, risk gates, or strategy selection.

## Design Principles
- **Local data first:** all source payloads land append-only before downstream parsing.
- **Three clocks:** every document-like artifact carries `event_time`, `published_time`, and `ingested_time`.
- **Security master first:** NER only creates `EntityMention`; binding to securities requires the security master.
- **Claim verification before sentiment:** no sentiment/causal factor can consume an unverified claim.
- **No raw-news selector:** strategy selector reads only verified events, evidence packets, and factor snapshots.
- **Reproducibility:** every run stores `snapshot_hash`, `config_hash`, and `code_hash`.
- **Prompt isolation:** self-iteration may tune mechanism parameters, feature sets, and portfolio rules; it may not rewrite fact rules, source tiers, or claim-verification rules automatically.
- **Compatibility:** current `portfolio_bot` entrypoints remain available through a facade.

## Layered Architecture

### Core
`robin/core` owns configuration, deterministic IDs, clock helpers, structured logging, and shared enums. It must not import IO adapters or strategy code.

### Contracts
`robin/contracts` owns pydantic v2 public data objects. Contracts are versioned and treated as wire/storage shapes.

Required contracts:
- `RawDocument`
- `CanonicalDocument`
- `EntityMention`
- `CanonicalEntity`
- `EventRecord`
- `ClaimRecord`
- `EvidencePacket`
- `FactorDefinition`
- `FactorValueDaily`
- `BacktestRun`
- `ExperimentRun`
- `StrategyDecision`
- `ExecutionReport`

All public contracts include `id`, `schema_version`, `lineage`, and `created_at`. Documents include the three clocks. Fact and experiment artifacts include source/config/code hashes where relevant.

### Data Lake
vNext writes local partitions under `data/vnext` by default:

```text
data/vnext/bronze/raw_documents/source=.../date=.../*.parquet
data/vnext/silver/canonical_documents/date=.../*.parquet
data/vnext/gold/evidence_packets/date=.../*.json
data/vnext/catalog.duckdb
data/vnext/metadata.sqlite
```

If a live source, paid market feed, or compliance permission is not specified, the implementation must mark it as `UNSPECIFIED` and use fixture/local-parquet/dry-run fallback.

### Ingest
`robin/ingest` owns source registry, fetcher interfaces, canonical raw-document normalization, URL/hash de-duplication, and append-only bronze writes. Source outages emit `FetchFailure` telemetry and do not break deterministic replay.

### Facts
`robin/facts` owns entity resolution, event extraction, claim verification, evidence scoring, and evidence graph generation. The pipeline order is fixed:

1. canonicalize document
2. extract `EntityMention` candidates
3. resolve `CanonicalEntity` through security master
4. extract schema-driven `EventRecord`
5. verify `ClaimRecord`
6. score evidence by source tier, recency, entity match, and conflict state
7. emit `EvidencePacket`

### Features
`robin/features` owns factor registry, daily incremental compute, backfill, and factor evaluation. It uses Polars/Arrow/DuckDB for local columnar work. Holding positions are portfolio exposure inputs only and must not seed fact attribution or labels.

### Research and Backtest
`robin/research` owns labels, factor evaluation, selection, and report artifacts. `robin/backtest` owns walk-forward, purged splits, CPCV minimal implementation, costs, analytics, and MLflow/local artifact logging.

### Strategy
`robin/strategy` owns champion/challenger selection, paper/canary/live state, risk checks, execution reports, and promotion policies. Live trading is hard-disabled unless broker permission is explicitly provided; current default is `UNSPECIFIED_BROKER_PERMISSION`.

### Reporting
Reports only consume `EvidencePacket`, factor evaluation summaries, backtest artifacts, and strategy decisions. Every key conclusion must cite an `evidence_id` or `factor_eval_id`. If no citation exists, the renderer must output `证据不足`.

### Monitoring and Audit
`robin/monitor` records ingestion freshness, parse success rate, verification pass rate, factor job latency, replay diff, paper/live divergence, and source failure rate. The lineage audit log records artifact inputs, run IDs, code hashes, config hashes, and output hashes.

## Compatibility
Current top-level modules and commands remain valid:

- `portfolio_bot.research`
- `portfolio_bot.monitor`
- `portfolio_bot.workers`
- `portfolio_bot.data_hub`
- `portfolio-bot ...`

The compatibility layer lives in `robin/app/compat.py`. New vNext commands are exposed through `robin-vnext`.

## Security and Privacy
Ignored runtime files remain out of git: `.env`, `config.yaml`, `holdings.yaml`, `analysts.yaml`, `.portfolio_bot`. Public examples must use fixtures only.

## Non-goals
- No heavy workflow framework in P0/P1.
- No automated live trading.
- No automatic mutation of source tiers, security-master rules, or claim-verification rules.
- No monolithic LangChain/LangGraph black-box workflow for vNext core.

## Milestones
- **P0:** contracts, data lake, evidence pipeline, factor registry/daily compute, replay tests, compatibility layer.
- **P1:** backtest engine, cost model, MLflow/local artifacts, champion/challenger, paper decisions, report renderer, lineage.
- **P2:** richer event schemas, advanced impact models, outer-loop meta-learning.
