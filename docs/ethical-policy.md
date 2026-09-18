# One issuer, one ethical screening

Money screens each verified issuer once and records `PASS`, `FAIL` or `UNKNOWN`.
Only `PASS` may continue. `NOT_YET_SCREENED` in bulk progress means that a usable
issuer screening has not run; it is not clearance. Existing defence, weapons,
firearms, material military contracting and oil exclusions are unchanged.

The screen binds exact issuer identity, every configured exclusion, admissible
source references and bytes, the result, screening time and expiry. It is machine
executable when evidence is sufficient; there is no independent second ethical
reviewer. Missing facts, conflicting disclosures or an ambiguous issuer remain
`UNKNOWN`. A ticker, name, SIC code or absence of keywords cannot establish PASS.

One persisted PASS is reused in universe qualification, the frozen snapshot,
independent first-pass research, mandatory LEAN and CIO/Red Team. Those stages do
not ask the same ethical question again. Exact issuer identifiers may link share
classes; a similar company name is not a safe issuer join. Re-screen on identity
or material business evidence change, a configured exclusion change, credible
contradictory evidence or expiry. Live broker and provider data retain their own
freshness rules; those do not turn ethical screening into a daily human review.
`MONEY_ETHICAL_CLEARANCE_DAYS` defaults to 30 and accepts 1–365 days. Set
`MONEY_ETHICAL_SCREENINGS_PER_RUN` to bound newly evaluated issuers per run
(default 20); unexpired applicable cached screenings are reused without calls.

## Global licensing, not per-issuer signatures

The existing `inputs/provider-rights/{provider}.json` review can explicitly cover
ethical reuse through `review.ethical_research_datasets`. Its attached actual
licence evidence, reviewer and validity still must be genuine. Approved values:

- EODHD: `issuer-profile`, `financial`, `news`.
- Companies House: `company`, `filing`, `financial`.

Specify only datasets whose actual licence permits that use. This field does not
claim API access, document retrieval or financial coverage: each is independently
verified. Free-text "research" in `usage_purpose` and successful requests do not
grant permission. No extra ethical-source approval is required after the same
global review explicitly covers the use. Existing independently reviewed global
`inputs/universe/source-rights/*.json` files remain compatible optional scope
supplements; new runs do not generate those duplicate review templates.

## Operator outputs

Additional genuine documents are indexed in
`inputs/universe/ethical-evidence.json`, under `documents`. Each entry identifies
`isin`, the globally approved `provider` and `dataset`, `evidence_file`, actual
`published_at` and `retrieved_at`, and `evidence_kind` (for example
`annual_report`). These are source facts, not a human PASS/signature. See the
generated `inputs/universe/ethical-document.schema.json` for the exact contract.
Custom document bytes must themselves contain the exact ISIN, or the verified
Companies House registration number together with its exact verified legal
company name. A ticker or short-name substring is not document identity evidence.
Do not invent document text, dates, exposure assertions or licence permissions.

`outputs/ethical-screenings.json` records screening results. The issuer-grouped
`outputs/ethics-work-queue.json` separates cached PASS reuse, excluded FAIL,
UNKNOWN evidence resolution and automatic work for NOT_YET_SCREENED. Factual
dossiers reuse approved provider evidence; unapproved content remains references
only. `outputs/FIRST_STOCK_NEXT.md` exposes the selected stock's actual next
gate, without manufacturing a PASS to move the pipeline forward.

Preparation grants no rights, writes no reviewer signature and changes no
financial, PIT, cost, native/security, snapshot, first-pass, mandatory LEAN, CIO,
release or hosted acceptance requirement. Qlib remains disabled when configured
false; it is not replaced by synthetic evidence.
