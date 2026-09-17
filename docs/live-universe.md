# Live Trading 212 ISA universe

Money discovers the entire current GBX individual-stock universe from the
authenticated **live** Trading 212 accessible-instruments response. Initial
membership requires exactly `type == "STOCK"` and `currencyCode == "GBX"` in
that current response. Exchange metadata is optional enrichment, not an admission
gate. GBP, USD and EUR instruments and every non-stock instrument are excluded
by this bulk filter; other existing currency contracts are unchanged.
It does not select a stock for qualification based on investment attractiveness.
Greggs has no special runtime role; earlier single-stock preparation remains
historical audit material and is not the bulk workflow's source of membership.

## Run and resume

From the normal Mac terminal in the repository:

```bash
railway run --service Money --environment production sh -c 'MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/finalize_live_universe.py'
```

The explicit local inference configuration selects
`data/qualified/local-inference`. The authoritative output is
`outputs/uk-isa-stock-universe.json`; the legacy filename is retained for
resumability and does **not** imply a UK-venue requirement. Its CSV is only a
viewing aid. The provenance
and exception queue are `outputs/universe-provenance.json` and
`outputs/universe-review-queue.json`. A failed broker refresh never establishes
new membership or admits a stale previous universe. An unavailable exchange
response does not invalidate a successfully retrieved GBX stock universe.

Repeat the same command to resume provider requests and reviewed inputs. The
default provider request budget is **300 new requests per run**, paced at
**30 requests/minute**. `--max-provider-requests` and `--requests-per-minute`
provide explicit bounded overrides; increasing them does not establish provider
entitlement. Successful response/probe caches last at most 24 hours, are checked
against actual artifact bytes, and do not consume the new-request budget.
Failures have a short five-minute backoff; an unavailable provider or ambiguous
stock does not turn other stock results into failures.

After resolving required review inputs, continue with the one additional command
printed by the finalizer:

```bash
railway run --service Money --environment production sh -c 'MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/build_live_qualification.py'
```

Neither command places orders or accesses balances, positions or order history.
Credentials are read from the process environment, not placed in review files.

## Policy migration and offline replay

The active `universe_policy_version` is
`money-t212-gbx-stock-universe-v2`. It is recorded in the authoritative universe
JSON, provenance, review queue, provider-stage result/checkpoint and bulk-mode
marker. A missing or different version triggers automatic reclassification on
the normal commands above; no manual directory deletion or rebuild flag is
required. CSV is regenerated from the versioned JSON, never read as authority.

Only these seven derived paths are moved into recoverable audit history beneath
`state/universe-policy-history/` before rebuilding:

- `outputs/uk-isa-stock-universe.json`
- `outputs/uk-isa-stock-universe.csv`
- `outputs/universe-provenance.json`
- `outputs/universe-review-queue.json`
- `outputs/providers-result.json`
- `state/provider-stage.json`
- `state/bulk-universe-mode.json`

The migration preserves raw broker and provider artifacts/caches, their hashes
and original observation timestamps, operator inputs/reviews, snapshots and
other quantitative/native outputs. Archived classifications are audit history,
not reusable approvals. The new review queue is calculated from source evidence,
not copied from obsolete venue decisions. Appending `--rebuild-universe` to the
finalizer forces this same narrowly scoped rebuild when explicitly needed.

For a network-free diagnostic replay of already saved genuine responses:

```bash
MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/finalize_live_universe.py --replay-saved
```

Replay verifies the stored raw response hashes and uses applicable cached
provider evidence only. It requires no fake credentials, does not refresh any
observation timestamp and records `status: REPLAYED` with
`scope: SAVED_RESPONSE_REPLAY_ONLY`. It cannot generate an `EligibilityReview`
or grant account, ethical or production approval. Its counts describe the saved
response, not a newly fetched live universe. Run the normal live command before
continuing qualification.

## Bulk facts and genuine review boundaries

Every currently accessible `STOCK` quoted in GBX enters the candidate pipeline,
including stocks with missing exchange metadata, unresolved venues, non-UK
venues or foreign ISINs. There is no London, `XLON`, country or `GB` ISIN-prefix
requirement. Venue names, exchange IDs and MICs are retained where verified;
missing venue enrichment alone never creates `UNRESOLVED_IDENTITY`. Genuine
identifier conflicts or malformed identifiers still fail identity qualification.

Initial membership is not ISA approval, permission to buy, ethical clearance or
full qualification. Account binding/current purchase availability and every
downstream evidence and review gate remain independently mandatory.

EODHD joins receive every identity-valid GBX candidate before final qualification,
without waiting for venue resolution or an account/ethical approval.
They use exact ISIN, the returned provider exchange/code, quote-unit agreement
and company/ticker corroboration; ambiguous matches stay unresolved. Money never
manufactures an EODHD `.LSE` suffix. Companies House
mapping must distinguish the issuer from similarly named operating companies.
A genuinely foreign-incorporated issuer can be `NOT_APPLICABLE` for Companies
House; unknown incorporation or identity is unresolved, not assumed foreign.
Foreign companies still need independently admitted financial evidence.

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
| `inputs/universe/account-scope.json` | Confirm the credential binding belongs to the Stocks & Shares ISA; substantiate account-specific accessible membership and current purchase availability. Listing alone does not establish either. |
| `inputs/universe/ethics.json` | Assess all existing exclusion categories using rights-approved company, filing or annual-report evidence; one independent review may cover many structured entries. |
| `inputs/universe/supplemental.json` | Link independently reviewed spread, costs, corporate-action coverage, financial and archived point-in-time evidence by ISIN. |
| `inputs/provider-rights/eodhd.json`, `inputs/provider-rights/companies-house.json` | Review explicit permitted use and retention/redistribution rights. Working API access is not licensing approval. |

Account scope is **one universe-level review** in
`inputs/universe/account-scope.json`, not a separate review for every stock.
The explicit `STOCKS_AND_SHARES_ISA` operator attestation is bound to the
credential HMAC and source-response hashes/retrieval evidence in provenance.
It is identified as an operator attestation, never as account type returned by
the metadata API. The review queue carries this requirement once at its top
level while individual entries describe their remaining instrument issues.

An existing `inputs/universe/venues.json` may enrich venue information, but it is
optional. Neither an unresolved venue review nor missing country/MIC metadata
blocks initial admission or provider lookup. Missing country/MIC facts are
nullable enrichment, not venue-failure review tasks.

Follow the generated schemas and `inputs/universe/README.md`. Approval requires
real, distinct preparer/reviewer identities, actual review timestamps, finite
validity and attached evidence. No discovery, model description, company name or
SIC code supplies an ethical approval. Defence, weapons, firearms, material
military contracting and the existing oil-related exclusions remain unchanged.
Unknown material exposure remains `ETHICAL_REVIEW_REQUIRED` and is not admitted.

Provider samples report actual accessible OHLCV, corporate-action and news
observations. Empty action/news responses are not invented observations or
automatic qualification. Companies House filing metadata is not financial
coverage. Missing reviews, stale evidence and ambiguous joins stay explicitly
unresolved; only the qualified subset can enter research. Membership and the
existing eligibility/ethical freshness limits never extend beyond 24 hours.

The finalizer reports `raw_instruments`, `gbx_stocks`, `identity_valid`,
`identity_unresolved`, `provider_stage_input_count`, `eodhd_attempted`,
`eodhd_mapped`, `companies_house_mapped`, `ethical_review_required`,
`ethical_excluded`, `qualified` and `unresolved`, alongside freshness and dataset
coverage. Provider coverage uses the GBX universe, not a UK-venue subset.
Any `Venue resolved: N/N` count is informational coverage only.

## Freeze the whole universe, then bound expensive research

Qualification freezes complete currently admitted membership and evidence hashes
in `outputs/universe-snapshot.json`, with per-stock evidence references in
`outputs/universe-snapshot-evidence.json`. Missing research data remains visible
for that member; it cannot silently disappear from the frozen membership or
enter expensive analysis. The numeric result is `outputs/universe-screen.json`.

The initial screen ranks by mean daily **GBP traded value** across the latest
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

Only screened candidates enter independent TradingAgents, AI-Hedge-Fund and
numeric Qlib research. The three reports remain independent and sealed until
`FIRST_PASS_LOCKED`; LEAN then validates, followed by CrewAI CIO, evidence and
contradiction checks, Red Team and at most two cross-examination rounds. Qlib
training/manual promotion, LEAN, native security, release and first-pass gates
are unchanged. A qualified subset does not qualify those independent stages.

## Local Ollama is not Railway production inference

`railway run` injects environment variables into a process running on the Mac.
The Mac can use its own Ollama listener; a hosted Railway worker cannot reach
that listener through `127.0.0.1`. Local inference is only local functionality
evidence. Hosted operation still needs a separately qualified remotely reachable
endpoint, enforced worker egress, qualified native dependencies/security,
approved release, valid production manifest and hosted end-to-end acceptance.
The finalizer does not create a production manifest or alter the production
OpenAI configuration.
