# Live Trading 212 GBP/GBX research universe

Money discovers individual stocks from the authenticated **live** Trading 212
accessible-instruments response. `RESEARCH_ELIGIBLE` requires `type == "STOCK"`,
`currencyCode` equal to `GBP` or `GBX`, a Trading 212 ticker/ID and non-conflicting
basic identity. Exchange metadata is enrichment, not an admission gate. USD/EUR
and non-stock instruments are excluded. Company enrichment and ethical status
do not block personal research, independent reports, comparison or backtesting.
Admission grants no buyability, licensing, ethical or production approval.
It does not select a stock for qualification based on investment attractiveness.
Greggs has no special runtime role; earlier single-stock preparation remains
historical audit material and is not the bulk workflow's source of membership.

## Run and resume

From the normal Mac terminal in the repository:

```bash
railway run --service Money --environment production -- sh -c 'MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/run_research_testing.py --instrument NICLl_EQ'
```

This entry point explicitly selects personal research and disables Qlib only
for the local process; it does not modify Railway configuration. Nichols is an
operator-selected pipeline test, not an admission special case. See
[research testing](research-testing.md) for the bounded workflow and market-data
fallback. The explicit local inference configuration selects
`data/qualified/local-inference`. The authoritative output is
`outputs/trading212-gbx-stock-universe.json`; readers accept the historical
`outputs/uk-isa-stock-universe.json` filename only for migration/compatibility.
Both GBP and GBX are included despite the legacy filename. Neither filename
establishes an ISA or UK-venue claim. Its CSV is only a
viewing aid. The provenance
and exception queue are `outputs/universe-provenance.json` and
`outputs/universe-review-queue.json`. A failed broker refresh never establishes
new membership or admits a stale previous universe. An unavailable exchange
response does not invalidate a successfully retrieved GBP/GBX stock universe.

The research runner freezes complete admission before screening, reads caches
for cheap screening and bounds enrichment to shortlisted stocks. To separately
resume bulk enrichment, use the finalizer in explicit personal mode:

```bash
railway run --service Money --environment production -- sh -c 'MONEY_USAGE_MODE=personal_research MONEY_QLIB_ENABLED=false MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/finalize_live_universe.py'
```

Repeat the same command to resume provider requests and reviewed inputs. The
default provider request budget is **300 new requests per run**, paced at
**30 requests/minute**. `--max-provider-requests` and `--requests-per-minute`
provide explicit bounded overrides; increasing them does not establish provider
entitlement. Successful response/probe caches last at most 24 hours, are checked
against actual artifact bytes, and do not consume the new-request budget.
Failures have a short five-minute backoff; an unavailable provider or ambiguous
stock does not turn other stock results into failures.

New network work uses a credential-bound, policy-versioned round-robin cursor in
`state/universe-enrichment-cursor.json`. Each run starts after the last identity
that consumed network budget, so repeated failures at the head of the catalogue
cannot starve the remaining stocks. All identity-valid members still receive
an enrichment attempt, including cache reuse after the budget is exhausted.
The cursor contains no approvals; replay does not advance it, and a changed
policy or credential binding invalidates its ordering. Partial evidence still has
its original expiry, and incomplete observations never become qualified.

Safe provider diagnostics distinguish numeric HTTP failures (authentication,
access denied, not found, rate limit, upstream failure) from transport failure
when that information is actually available. They never contain raw URLs,
headers, bodies or secrets, and an HTTP denial alone does not establish the
account's subscription entitlements.

For the separate strict commercial qualification workflow, genuine review inputs
and every production gate still apply. Without personal mode, the qualification
runner remains strict:

```bash
railway run --service Money --environment production sh -c 'MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/build_live_qualification.py'
```

Neither command places orders or accesses balances, positions or order history.
Credentials are read from the process environment, not placed in review files.

## Policy migration and offline replay

The active `universe_policy_version` is
`money-t212-gbp-gbx-research-universe-v7`. It is recorded in the authoritative universe
JSON, provenance, review queue, provider-stage result/checkpoint and bulk-mode
marker. A missing or different version triggers automatic reclassification on
the normal commands above; no manual directory deletion or rebuild flag is
required. CSV is regenerated from the versioned JSON, never read as authority.
Changing the explicit provider usage mode also rebuilds derived admission
decisions. Raw observations remain reusable; personal-use qualifications never
become commercial approval by changing an environment variable.

Derived universe/classification/checkpoint paths are moved into recoverable audit
history beneath `state/universe-policy-history/` before rebuilding, including:

- `outputs/uk-isa-stock-universe.json`
- `outputs/uk-isa-stock-universe.csv`
- `outputs/trading212-gbx-stock-universe.json`
- `outputs/trading212-gbx-stock-universe.csv`
- `outputs/universe-provenance.json`
- `outputs/universe-review-queue.json`
- `outputs/providers-result.json`
- `state/provider-stage.json`
- `state/bulk-universe-mode.json`

The migration preserves raw broker and provider artifacts/caches, their hashes
and original observation timestamps, operator inputs/reviews, snapshots and
other quantitative/native outputs. Archived classifications are audit history,
not reusable approvals. The new review queue is calculated from source evidence,
not copied from obsolete venue, account-scope or multi-review ethical decisions. Appending `--rebuild-universe` to the
finalizer forces this same narrowly scoped rebuild when explicitly needed.

For a network-free diagnostic replay of already saved genuine responses:

```bash
MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/finalize_live_universe.py --replay-saved
```

Replay verifies the stored raw response hashes and uses applicable cached
provider evidence only. It requires no fake credentials, does not refresh any
observation timestamp and records `status: REPLAYED` with
`scope: SAVED_RESPONSE_REPLAY_ONLY`. It cannot generate an `EligibilityReview`
or grant ethical or production approval. Its counts describe the saved
response, not a newly fetched live universe. Run the normal live command before
continuing qualification.

`--reclassify-saved` instead migrates derived classifications from a hash-verified
genuine prior live retrieval without network access. It preserves original
retrieval timestamps and labels the result `SAVED_LIVE_DERIVED_RECLASSIFICATION`,
not a current authenticated refresh. It cannot produce admitted eligibility
reviews. Raw evidence and human inputs are preserved; a live finalizer run is
still required before admission.

## Bulk facts and genuine review boundaries

Every currently accessible `STOCK` quoted in GBP/GBX enters the candidate pipeline,
including stocks with missing exchange metadata, unresolved venues, non-UK
venues or foreign ISINs. There is no London, `XLON`, country or `GB` ISIN-prefix
requirement. Venue names, exchange IDs and MICs are retained where verified;
missing venue enrichment alone never creates `UNRESOLVED_IDENTITY`. Genuine
identifier conflicts or malformed identifiers still fail identity qualification.

Initial membership is not permission to buy, ethical clearance or full
qualification. ISA/account-type/current-ISA-buyability are no longer qualification
concepts. Technical credential binding protects cache/replay integrity only.
Research additionally verifies current response integrity and basic identity.
Company, ethical and supplemental reviews are not research-admission gates;
strict commercial qualification and release requirements remain separate.

EODHD joins receive identity-valid GBP/GBX candidates before final qualification,
without waiting for venue resolution or ethical approval.
They use exact ISIN, the returned provider exchange/code, quote-unit agreement
and company/ticker corroboration; ambiguous matches stay unresolved. Money never
manufactures an EODHD `.LSE` suffix. Optional enrichment failures never revoke
research admission. The following company/review requirements describe strict
commercial qualification, not personal research admission. Companies House
mapping must distinguish the issuer from similarly named operating companies.
A genuinely foreign-incorporated issuer can be `NOT_APPLICABLE` for Companies
House; unknown incorporation or identity is unresolved, not assumed foreign.
Foreign companies still need independently admitted financial evidence.
Unknown issuer jurisdiction creates an applicability-resolution task, **not**
a demand for Companies House filings that might not exist. Once UK issuer
identity is established, company/profile, filing and rights requirements remain
mandatory. Neither venue nor ISIN prefix establishes incorporation.

The finalizer refreshes both `outputs/providers-result.json` and
`state/provider-stage.json`. Their `provider_stage_inputs` and
`provider_stage_input_count` expose candidates ready for provider enrichment.
They are separate from `instruments`, `eligible_counts` and `qualified_universe`,
which remain reserved for successfully qualified outputs. A nonzero provider
input count with zero eligible instruments is expected while genuine downstream
evidence or reviews are missing; it does not prevent enrichment from running.

The runner generates bulk inputs, **not thousands of per-stock identity review
templates**:

| File under the qualification directory | Required review |
|---|---|
| `inputs/universe/ethical-evidence.json` | Index genuine issuer/business source bytes for one machine-executable ethical screening per verified issuer; no ethical signature or second reviewer. |
| `inputs/universe/supplemental.json` | Link independently reviewed spread, costs, corporate-action coverage, financial and archived point-in-time evidence by ISIN. |
| `inputs/provider-rights/eodhd.json`, `inputs/provider-rights/companies-house.json` | Mandatory for hosted/commercial use. Explicit personal research records restricted `UNVERIFIED_PERSONAL_USE` metadata without requiring a signature. Working API access is not licensing approval. |

Legacy `inputs/universe/account-scope.json` and its schema are retained unchanged
for audit but ignored under the current policy. Their absence, expiration or
unresolved status cannot affect qualification, research or release. They are not
converted into approvals. The technical credential HMAC and live-response
hashes/retrieval timestamps remain independently verified.

The live provenance artifact supplies non-secret technical credential binding
and hash-bound retrieval facts, without claiming an account type or changing any
operator review. `outputs/universe-review-work.json` groups the global rights
reviews and provides one bulk factual dossier of returned provider identities,
issuer descriptions, profile facts, recent accounts metadata and evidence
references. It is explicitly unreviewed and is never an approval input.
With explicit `MONEY_USAGE_MODE=personal_research`, the provider-wide source-use
audit is non-blocking locally; no redistribution, public raw display, resale or
external sharing is allowed. Commercial/public release retains strict reviews.
See [personal research rights](personal-research-rights.md).
The review queue separates automated enrichment failures/deferred requests from
human decisions; global provider-rights reviews are not duplicated as thousands
of individual signatures. Working APIs, a company description or missing
exclusion keywords cannot clear the ethical gate.

An existing `inputs/universe/venues.json` may enrich venue information, but it is
optional. Neither an unresolved venue review nor missing country/MIC metadata
blocks initial admission or provider lookup. Missing country/MIC facts are
nullable enrichment, not venue-failure review tasks.

Follow the generated schemas and `inputs/universe/README.md`. Non-ethical
independent reviews, including supplemental evidence and release approval, still
require their actual reviewer identities, timestamps, validity and evidence.
Ethics uses one evidence-based issuer screening: `PASS`, `FAIL` or `UNKNOWN`.
Only PASS proceeds through strict commercial ethical qualification and is reused;
personal research may continue with PASS, FAIL, UNKNOWN or NOT_SCREENED.
Humans resolve UNKNOWN/conflicting
evidence, not a second ethical sign-off. No discovery, model description, company
name or SIC code supplies clearance. Defence, weapons, firearms, material military
contracting and the existing oil-related exclusions remain unchanged. See
[the ethical policy](ethical-policy.md) for source scope and cache invalidation.

Provider samples report actual accessible OHLCV, corporate-action and news
observations. Empty action/news responses are not invented observations or
automatic qualification. Companies House filing metadata is not financial
coverage. Missing reviews, stale evidence and ambiguous joins stay explicitly
unresolved for strict commercial qualification. Personal research uses the
separate `RESEARCH_ELIGIBLE` subset, regardless of optional coverage. Live broker membership
and provider/identity freshness retain their existing limits, including the
24-hour eligibility limit. Ethical clearance has its own configurable validity
(`MONEY_ETHICAL_CLEARANCE_DAYS`, default 30), not a daily ethical-review requirement.

The finalizer reports `raw_instruments`, `gbp_gbx_stocks`, `gbp_stocks`, `gbx_stocks`,
`research_eligible`, `research_identity_conflicts`, `enrichment_coverage`, `identity_valid`,
`identity_unresolved`, `provider_stage_input_count`, `eodhd_attempted`,
`eodhd_mapped`, `companies_house_mapped`, `ethical_review_required`,
`ethical_excluded`, `qualified` and `unresolved`, alongside freshness and dataset
coverage. Provider coverage uses the GBP/GBX universe, not a UK-venue subset.
Any `Venue resolved: N/N` count is informational coverage only.

## Freeze the whole universe, then bound expensive research

Personal research freezes **every** currently admitted member before selection.
Its cheap screen uses median GBP traded value from up to 20 genuine recent bars,
with stable broker-ID tie breaking; missing bars leave an instrument unscored,
not ineligible. The default shortlist is **one**, maximum 20. An explicit operator
pipeline test may select a member without a score. No expected return is claimed.

Snapshots carry `purpose=RESEARCH_TESTING`, complete-universe hashes and explicit
missing data. TradingAgents and AI Hedge Fund work independently. Both validated
reports are retained before FIRST_PASS_LOCKED and limited evidence comparison.
Qlib is disabled. Genuine LEAN remains required for LEAN_VALIDATED; real history,
mapping, appropriate actions, PIT-safe strategy inputs, OOS/walk-forward and
documented assumed costs are required, not company/ethical reviews. Comparison
alone does not complete CIO/Red Team or approve release.

### Separate strict commercial pipeline

Qualification freezes complete currently admitted membership and evidence hashes
in `outputs/universe-snapshot.json`, with per-stock evidence references in
`outputs/universe-snapshot-evidence.json`. Missing research data remains visible
for that member; it cannot silently disappear from the frozen membership or
enter expensive analysis. The numeric result is `outputs/universe-screen.json`.

The strict commercial screen ranks by mean daily **GBP traded value** across the latest
20 eligible PIT-safe OHLCV observations, breaking ties by ticker. GBX prices are
normalized to GBP. It selects at most **five** candidates by default; the
validated policy has a hard maximum of 20. This is auditable allocation of
research attention, not an expected-return score, buy recommendation or a
replacement for any eligibility or market-quality gate.

Each candidate `ResearchSnapshot` binds the complete frozen universe through
`universe_hash`. The live flow durably stores the full universe context before
candidate snapshots and first-pass reports. Resume validates the saved context,
candidate membership and reproducible screening. Native qualification's
`outputs/snapshot.json` is a screened compatibility sample, not the definition
of the universe.

Only screened candidates enter independent TradingAgents, AI-Hedge-Fund and,
when enabled, numeric Qlib research. Qlib is enabled by default; explicit
`MONEY_QLIB_ENABLED=false` selects two-firm mode without creating a substitute
Qlib report. The mode is frozen into snapshot hashes and the reviewed manifest.
All selected reports remain independent and sealed until `FIRST_PASS_LOCKED`;
LEAN is mandatory in both modes, followed by CrewAI CIO, evidence and
contradiction checks, Red Team and at most two cross-examination rounds.
Enabled Qlib still requires genuine training/manual promotion. LEAN, native
security, release and first-pass gates are unchanged. A qualified subset does
not qualify those independent stages. See [the runner guide](LIVE_QUALIFICATION_RUNNER.md#optional-qlib-mandatory-lean)
for the exact opt-out command.

## Local Ollama is not Railway production inference

`railway run` injects environment variables into a process running on the Mac.
The Mac can use its own Ollama listener; a hosted Railway worker cannot reach
that listener through `127.0.0.1`. Local inference is only local functionality
evidence. Hosted operation still needs a separately qualified remotely reachable
endpoint, enforced worker egress, qualified native dependencies/security,
approved release, valid production manifest and hosted end-to-end acceptance.
The finalizer does not create a production manifest or alter the production
OpenAI configuration.
