# Robin vNext Migration Plan

## Summary
Migration is incremental. The existing `portfolio_bot` package remains runnable while `robin/` vNext is introduced beside it. Each milestone is independently testable and can be reverted by removing its facade wiring and commit.

## Milestone 0: Documentation and Contracts
Deliver:
- `docs/rfc_vnext.md`
- this migration plan
- `robin/core`
- `robin/contracts`
- tests for contract serialization and deterministic IDs

Acceptance:
- `python3 -m py_compile $(rg --files robin | rg '\.py$')`
- `pytest tests/unit/test_contracts.py`

Rollback:
- remove `robin/core`, `robin/contracts`, and docs commit.

## Milestone 1: Local Data Lake and Ingest
Deliver:
- source registry and fetcher interfaces
- append-only bronze writer
- URL/hash de-duplication
- parquet/json fixture fallback
- `robin-vnext ingest run`

Acceptance:
- fixed raw fixture writes exactly one bronze document when run repeatedly
- source-down fixture returns a failure artifact without aborting the job
- `pytest tests/unit/test_ingest.py tests/integration/test_data_lake.py`

Rollback:
- disable `robin-vnext ingest` command and remove `robin/ingest` commit.

## Milestone 2: Evidence Pipeline
Deliver:
- canonical document normalizer
- mention extraction
- security-master entity resolver
- schema-driven event extraction
- claim verification and conflict handling
- evidence scoring and `evidence_packet.json`
- `robin-vnext facts build-evidence`

Acceptance:
- fixed raw news fixture emits deterministic evidence packet
- corrected announcement supersedes original claim while preserving lineage
- conflicting claim fixture emits `verification_status=conflicted`
- `pytest tests/unit/test_facts.py tests/integration/test_evidence_pipeline.py tests/replay/test_replay_evidence.py`

Rollback:
- remove facts command and keep bronze ingest data untouched.

## Milestone 3: Factor Factory
Deliver:
- factor registry
- daily incremental compute
- historical backfill
- rankIC, IC decay, IR, turnover, hit rate, stability
- industry/market-cap neutralization helper
- `robin-vnext features compute-daily`
- `robin-vnext features backfill`

Acceptance:
- fixed OHLCV fixture produces deterministic factor values and snapshot hash
- missing market data marks `UNSPECIFIED_MARKET_SOURCE`
- `pytest tests/unit/test_factors.py tests/replay/test_replay_factors.py`

Rollback:
- remove factor outputs under `data/vnext/features` and disable CLI commands.

## Milestone 4: Compatibility Facade
Deliver:
- `robin/app/compat.py`
- root `main.py` thin wrapper when absent
- optional vNext config gate with default safe fallback to old implementation
- compatibility tests for old import paths and CLI dry-run commands

Acceptance:
- `portfolio-bot report-now --dry-run`
- `portfolio-bot research-now SNDK --dry-run`
- `portfolio-bot worker realtime --once --dry-run`
- `pytest tests/test_*compat*.py`

Rollback:
- revert facade wiring; old implementation remains in place.

## Milestone 5: Backtest and Experiments
Deliver:
- walk-forward split
- purged split and minimal CPCV
- fixed fee + spread + simplified impact cost model
- gross/net analytics
- MLflow file-mode tracking with local artifact fallback
- `robin-vnext backtest run`

Acceptance:
- fixed fixture backtest matches golden gross/net metrics
- `BacktestRun` binds `snapshot_hash`, `config_hash`, `code_hash`, `factor_set_hash`
- MLflow unavailable still writes local artifact report
- `pytest tests/unit/test_backtest_vnext.py tests/golden/test_backtest_report.py`

Rollback:
- delete experiment artifact directory and disable backtest CLI.

## Milestone 6: Strategy, Reporting, Monitoring
Deliver:
- champion/challenger selector
- paper/canary/live states, with live hard-disabled by default
- strategy decision log
- constrained report renderer
- lineage audit log
- monitor metrics and `robin-vnext audit lineage`

Acceptance:
- selector reads only evidence packets and factor snapshots
- report conclusions cite `evidence_id` or `factor_eval_id`, otherwise emit `证据不足`
- strategy decision log is deterministic for fixture inputs
- `pytest tests/integration/test_strategy_decision.py tests/golden/test_report_renderer.py`

Rollback:
- disable vNext strategy/report commands; legacy report remains available.

## Operational Defaults
- `data/vnext` is gitignored runtime output.
- live broker execution is `UNSPECIFIED_BROKER_PERMISSION` and disabled.
- missing live data uses fixture/local parquet fallback and explicit `UNSPECIFIED` markers.
- no migration touches `config.yaml`, `holdings.yaml`, `.env`, or `.portfolio_bot`.

## Quality Gates
Run before merging each milestone:

```bash
ruff check robin tests
mypy robin tests
pytest --cov=robin --cov-report=term-missing
python3 -m py_compile $(rg --files robin portfolio_bot | rg '\.py$')
```

Replay fixtures must finish in CI within 10 minutes.
