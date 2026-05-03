# Mining Loop

Daily candidate mining should run after local data, event, op, and factor snapshots exist.

MVP loop:

1. Build local factor frame with forward returns in replay or offline mode.
2. Evaluate each candidate with fixed metrics.
3. Apply coverage, IC, FDR, turnover, and monotonicity gates.
4. Write `accepted_candidates.parquet` and `rejected_candidates.parquet`.
5. Feed accepted candidates into challenger evaluation only after replay tests pass.

Rejected candidates keep `rejection_reason` for later analysis.
