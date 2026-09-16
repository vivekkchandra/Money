# Contract boundaries

Canonical contracts live in `src/money/schemas/contracts.py`; Pydantic rejects unknown fields. Upstream-native objects never cross the API boundary.

| Contract | Invariant |
| --- | --- |
| ResearchMandate | Trading212 ISA, individual stocks, GBP/GBX; capital ≤ £200; horizons 1–30 days; exclusions cannot be removed; stretch target cannot change risk |
| InstrumentMetadata | Explicit current ISA availability, currency, business activities and verification provenance |
| EvidenceRecord | Objective typed payload plus source identities, timestamps and stable hash |
| ResearchSnapshot | Frozen common evidence; no firm opinions; cutoff checks and deterministic digest |
| Firm report | Firm identity, snapshot digest, claims referencing evidence, versions and usage |
| LeanValidationReport | PASS / FAIL / INSUFFICIENT_EVIDENCE, statistics and validation assumptions |
| CIO audit / Red Team | Independently verified claims and PASS / WARN / VETO findings |
| EvidenceIndependenceReport | Deduplicated sources and overlap, never agent vote totals |
| ResearchSignal | Research state, issue/expiry times, entry range, invalidators, targets and bounded illustrative capital |
| DecisionPacket | Immutable collection of locked reports, evidence, versions, gates and final state |

Jobs expose QUEUED → ELIGIBILITY_CHECK → DISCOVERY → SNAPSHOT_BUILD → FIRST_PASS_RESEARCH → FIRST_PASS_LOCKED → LEAN_VALIDATION → CREWAI_AUDIT → CROSS_EXAMINATION → CONSENSUS → COMPLETE. Any stage can terminate as REJECTED or FAILED. A completed packet may be INSUFFICIENT_EVIDENCE; COMPLETE does not mean an investment opportunity was endorsed.

Provider coverage and the bounded XBRL converter seam live in `data/providers.py`.
Outcome contracts live in `performance/outcomes.py`: normalized GBP bars with
availability times feed calendar-day returns and interval-valued target/failure
occurrences; same-bar ordering stays ambiguous. `offline_research/promotion.py`
binds four validation artifacts and an independent reviewer to a model hash;
eligibility for manual promotion never executes or activates the artifact.
