# Agent Contracts

The agent layer may only consume local evidence bundles and metric tables.

Required output fields:

- `thesis`
- `evidence_refs`
- `counter_evidence`
- `alternative_hypotheses`
- `portfolio_implication`
- `confidence`
- `invalidation_conditions`
- `missing_data`

Raw news fields such as `raw_text`, `body`, `html`, or `pdf_bytes` are rejected by `robin.agent.analyzer`.

If verified evidence is missing, the recommendation must be `insufficient_evidence`.
