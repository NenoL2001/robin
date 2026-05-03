# Op Spec

Feature ops live in `robin.features.ops`.

- Signature: `compute(ctx: OpRunContext, **inputs: pl.DataFrame) -> pl.DataFrame`.
- Outputs must contain `date` and `entity_id`; executor adds `op_version`, `snapshot_id`, `asof_ts`.
- Executor filters rows after `ctx.asof_ts` to reduce lookahead risk.
- Cache key includes op name, version, params, snapshot id, partition, and input fingerprints.
- Ops are pure local functions: no network, no file writes, no LLM calls.

Initial ops:

- `ma_gap_n`
- `volume_zscore_n`
- `industry_neutralization`
- `earnings_surprise`
- `news_sentiment_score`
- `event_shock_label`
- `filing_novelty_score`
- `peer_diffusion_score`
