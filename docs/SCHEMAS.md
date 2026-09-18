# Contract boundaries

Canonical contracts live in `src/money/schemas/contracts.py`; Pydantic rejects unknown fields. Upstream-native objects never cross the API boundary.

| Contract | Invariant |
| --- | --- |
| ResearchMandate | Trading212 live GBX individual stocks; capital ≤ £200; horizons 1–30 days; exclusions cannot be removed; stretch target cannot change risk. Legacy account_type is read-only compatibility, not a requirement |
| InstrumentMetadata | Current broker membership, currency, business activities and verification provenance. Legacy isa_available is ignored by qualification and not populated for new records |
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

New strict contracts outside the core file:

- `research/live.py`: administrator-owned `LiveManifest`, `InferenceSelection`,
  reviewed identifier/source artifacts and frozen `LiveProvenance`.
- `data/qualification.py`, `data/quality/market.py`, `risk/costs.py`: explicit
  provider admission, deterministic quality and dated instrument-specific costs.
- `adapters/native_qlib.py`, `models/registry.py`: bounded JSON linear artifacts,
  feature/training/validation versions and append-only manual promotion history.
- `crews/cross_examination.py`: sealed original-report hashes, challenges,
  independently verified responses, at most two rounds and unresolved IDs.
- `signals/generation.py`: a reproducible `SignalDesign` retaining policy,
  normalized/raw price levels, measured quality, costs and bounded downside.

New packet version/snapshot/manifest/cross-examination references omit themselves
when absent, preserving legacy packet hashes. Storage independently recomputes
consensus, evidence independence and positive signal design before publishing.
