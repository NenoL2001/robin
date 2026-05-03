# Migration Plan

## Phase 1: Audit And Contracts
- Keep `portfolio_bot` CLI and imports compatible.
- Add vNext audit artifacts and `robin/` local-first contracts.
- Acceptance: docs exist, architecture tests run, legacy tests still pass.

## Phase 2: Data And Op Kernel
- Use `robin.ingest.DataLake` for Bronze/Silver/Gold local artifacts.
- Use `robin.features.ops` for pure feature ops with cache keys and lineage metadata.
- Acceptance: op registry tests validate contract columns, cache, and point-in-time filtering.

## Phase 3: News/Event Pipeline
- Route raw documents through canonicalization, entity linking, claim extraction, confidence scoring, event factor rows.
- Acceptance: unconfirmed market move replay returns `insufficient_evidence`.

## Phase 4: Factor Lab And Mining
- Evaluate factors with Rank IC, ICIR, quantile spread, turnover, t-stat, p-value, q-value.
- Write accepted/rejected candidate outputs with explicit rejection reasons.
- Acceptance: miner tests verify good factor acceptance and weak factor rejection.

## Phase 5: Agent And Reports
- Agent reads only local evidence packets, factor metrics, decisions, and backtest artifacts.
- It must output alternatives, counter evidence, invalidation conditions, and missing data.
- Acceptance: raw news context raises an error; insufficient evidence action is deterministic.

## Rollback
- Remove vNext wiring and keep `portfolio_bot` legacy path unchanged.
- Generated data is isolated under `data/vnext` or `.portfolio_bot` and is not required for legacy operation.
