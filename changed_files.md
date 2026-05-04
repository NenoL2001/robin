# Changed Files

Date: 2026-05-04

## Stage 1: Audit And Architecture
- Added `target_architecture.mmd` as the target local-first architecture diagram required by the second report.
- Existing audit artifacts were preserved: `AUDIT.md`, `repo_map.json`, `dep_inventory.json`, `current_dataflow.mmd`, `risk_register.md`, and `migration_plan.md`.

## Stage 2: Data And Op Kernel
- No code changes were required in the op kernel during this pass; existing `robin/contracts`, `robin/ingest`, and `robin/features/ops` implementations passed contract, op, and architecture tests.

## Stage 3: News/Event Pipeline
- Updated `robin/news/pipeline.py`.
  - The pipeline now extracts per-document claims/events first, then applies cross-document corroboration by `(claim_type, entity_ids)`.
  - Cross-document groups now receive stable `event_cluster_id` values that are carried through claims, events, and factor rows.
  - Claim confidence now models `source_score`, entity linking, numeric consistency, source tier, event type, and independent source count.
  - Unconfirmed market-move articles remain below the formal evidence gate.
- Updated `robin/facts/scoring/evidence.py`.
  - Verified claims now contribute enough score for independent P1 corroboration to pass the evidence gate.
- Updated `robin/facts/evidence_graph/packet_builder.py`.
  - Evidence packets now store stable `security_id` values in `entity_ids`.

## Stage 4: Factor Metrics, Mining, And Backtest
- No code changes were required in factor metrics/miner/backtest during this pass; existing CLI, benchmark, and tests passed.

## Stage 5: Agent, Reports, CI, And Docs
- Added `tests/integration/test_vnext_news_event_pipeline.py::test_independent_p1_sources_corroborate_same_claim`.
  - Confirms two independent P1 sources can lift a claim into verified evidence and formal event factors.
- Preserved existing replay and architecture tests that block raw news from direct agent prompts and enforce insufficient evidence for unconfirmed moves.
- Added `changed_files.md`, `test_results.md`, and `benchmark_results.md` stage execution records.

## Pre-Existing Local Changes Not Touched
- `strategy_skills/_factor_specs/factors.yaml`
- `strategy_skills/_factor_specs/factors.yaml.bak`
- `Robin AI 交易 Agent 全面重构研究报告.pdf`
- `Robin AI 交易 Agent 全面重构研究报告-2.pdf`
