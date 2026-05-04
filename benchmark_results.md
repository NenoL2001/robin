# Benchmark Results

Date: 2026-05-04

## Command

```bash
make benchmark
```

## Result

```json
{
  "benchmark": "factor_pipeline_smoke",
  "elapsed_seconds": 0.022087,
  "as_of": "2026-05-04",
  "metrics": {
    "factor_name": "return_1d",
    "observation_count": 24,
    "rank_ic": 0.25,
    "icir": 0.41833,
    "quantile_spread": 0.0322,
    "turnover": 1.0,
    "t_stat": 1.183216,
    "p_value": 0.236724,
    "q_value": 0.236724,
    "monotonic": true
  }
}
```

## Interpretation
- This is a smoke benchmark for the local factor pipeline, not a claim of production performance.
- The benchmark uses fixture OHLCV data and verifies that factor evaluation returns real metrics instead of mocked performance output.
