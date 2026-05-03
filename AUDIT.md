# Robin AI Trading Agent Audit

## confirmed_current_behavior
- Legacy `portfolio_bot` remains the operational entrypoint for monitoring, reports, paper trading, and notifications.
- vNext `robin/` already contains contracts, ingest, facts, features, backtest, strategy, report, and audit modules.
- Earlier work blocked raw-news-only strategy flow with evidence ranking, strategy scout, factor lifecycle, and paper-only risk gates.
- Current implementation adds local-first op, news/event, metrics/miner, and evidence-only agent layers under `robin/`.

## likely_risks_to_verify
- Some external data permissions remain `UNSPECIFIED`; fixture and local parquet fallbacks are required.
- Full Qlib/Dagster/Pandera adoption is not yet hard dependency; current MVP keeps interfaces independent.
- Production-scale source adapters need paid/API compliance review before live use.
- The legacy report path still exists for compatibility and must keep moving toward vNext evidence bundles.

## must_delete_or_deprecate_paths
- Any future path that sends raw article body directly into an LLM prompt must be deprecated.
- Keyword-only news attribution without claim confidence must remain report-only and cannot support orders.
- Real broker execution remains disabled unless broker permission is explicitly specified.

## implemented_stage_mvp
- Audit artifacts, repo/dependency inventory, current dataflow diagram, and risk register.
- vNext op registry with eight pure local ops and point-in-time output filtering.
- Claim/event/factor news pipeline with official-source confidence gates.
- Factor metrics, FDR q-values, candidate miner, accepted/rejected outputs.
- Agent contract enforcing evidence-only context, alternatives, counter evidence, and insufficient-evidence fallback.
