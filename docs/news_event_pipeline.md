# News Event Pipeline

The vNext news pipeline is claim-level, not document-level.

Flow:

1. `RawDocument`
2. `CanonicalDocument`
3. entity mentions and `SecurityMaster` linking
4. structured `EventRecord`
5. `ClaimRecord` with source/entity/numeric confidence fields
6. `EvidencePacket`
7. event factor rows for op/factor consumption

Confidence policy:

- `>= 0.75`: can support formal report conclusions.
- `0.50..0.75`: candidate event only.
- `< 0.50`: recall record only.

The replay test `test_unconfirmed_price_move_replay_does_not_force_attribution` locks the rule that a price move without official or cross-source confirmation must become `insufficient_evidence`.
