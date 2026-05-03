# Migration Guide

Use `portfolio-bot` for current production-compatible workflows.

Use `robin-vnext` for local-first replay and research workflows:

```bash
robin-vnext ingest run --dry-run
robin-vnext facts build-evidence --dry-run
robin-vnext features compute-daily --dry-run
robin-vnext ops list
robin-vnext miner run --dry-run
robin-vnext agent analyze
```

Compatibility rule: do not delete legacy `portfolio_bot` entrypoints until equivalent vNext commands have replay tests and docs.
