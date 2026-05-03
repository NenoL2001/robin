#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"

robin-vnext ingest run --root "$ROOT" --registry tests/fixtures/vnext/source_registry.yaml
robin-vnext facts build-evidence --root "$ROOT" --security-master tests/fixtures/vnext/security_master.csv
robin-vnext features compute-daily --root "$ROOT" --ohlcv tests/fixtures/vnext/ohlcv.csv --as-of 2026-05-01
robin-vnext backtest run --root "$ROOT" --ohlcv tests/fixtures/vnext/ohlcv.csv --factor return_1d
robin-vnext report render --root "$ROOT"
