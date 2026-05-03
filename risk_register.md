# Risk Register

| Risk | Status | Mitigation |
| --- | --- | --- |
| Raw news reaches LLM prompt | Controlled | `robin.agent.analyzer` rejects raw text/body fields; architecture tests cover this. |
| Unconfirmed news forced into attribution | Controlled | News/event pipeline marks low-confidence claims as `insufficient_evidence`; replay test covers this. |
| Lookahead leakage in local ops | Controlled | Op executor filters outputs by `asof_ts`; op tests cover future rows. |
| Candidate factor bypasses fixed harness | Controlled | Miner emits accepted/rejected decisions with reasons; factor lifecycle keeps candidates shadow-only in legacy strategy. |
| External data permission unspecified | Open | Code uses fixture/local fallback and marks production sources `UNSPECIFIED`. |
| Heavy framework adoption risk | Deferred | MVP keeps Qlib/Dagster-compatible boundaries without forcing heavyweight runtime. |
