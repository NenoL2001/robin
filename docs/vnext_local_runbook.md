# Robin vNext Local Runbook

## Setup

```bash
python3 -m pip install -e '.[dev]'
```

Runtime output is written under `data/vnext/` and is ignored by git. Local secrets and personal state remain in `.env`, `config.yaml`, `holdings.yaml`, and `.portfolio_bot/`; none are required for fixture replay.

## Fixture Replay

```bash
robin-vnext replay run --root /tmp/robin-vnext-smoke
robin-vnext report render --root /tmp/robin-vnext-smoke
```

The fixture path uses:
- `tests/fixtures/vnext/raw_news.json`
- `tests/fixtures/vnext/security_master.csv`
- `tests/fixtures/vnext/ohlcv.csv`

These fixtures cover official evidence, corrected announcements, conflicting claims, and deterministic OHLCV factor computation.

## Individual Stages

```bash
tmp=$(mktemp -d)
robin-vnext ingest run --root "$tmp"
robin-vnext facts build-evidence --root "$tmp"
robin-vnext features compute-daily --root "$tmp"
robin-vnext backtest run --root "$tmp"
robin-vnext strategy decide SNDK --root "$tmp" --dry-run
robin-vnext audit lineage --root "$tmp" --artifact-id smoke --input-id fixture
```

## Legacy Compatibility Smoke

```bash
portfolio-bot report-now --dry-run
portfolio-bot research-now --dry-run
portfolio-bot worker health --once --dry-run
```

The old commands remain the default behavior unless legacy config explicitly enables `vnext.enabled`.

## Quality Gates

```bash
python3 -m py_compile $(rg --files portfolio_bot robin | rg '\.py$')
ruff check robin tests/unit tests/integration tests/replay tests/golden tests/test_vnext_compat.py
mypy robin tests/unit tests/integration tests/replay tests/golden tests/test_vnext_compat.py
pytest -q
pytest --cov=robin --cov-report=term-missing -q
portfolio-bot profile-suite --iterations 1 --json
```

## UNSPECIFIED Fallbacks

Live broker permission, paid market data, production news budget, and compliance-specific source lists are `UNSPECIFIED` in this repo. Fixture/local parquet/dry-run paths are the supported fallback until those inputs are configured explicitly.
