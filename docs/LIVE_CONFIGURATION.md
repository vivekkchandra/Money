# Explicit live assembly

The live assembly is implemented, but this repository is **PRODUCTION BLOCKED**.
Never turn on live mode with fabricated qualification hashes or demo data.

## Configuration inputs

1. Use `uv run python scripts/validate_live.py --schema` for the current strict
   JSON Schema. A manifest contains reviewed instruments/identifiers, provider
   qualifications and `(sha256, relative-path)` qualification artifacts. Referenced
   files must exist, remain within the manifest directory, be bounded and match
   their digest; no symlink or missing-proof acceptance.
2. Each instrument needs current verified ISA/availability and deterministic
   activity classification, proof hashes, provider-specific symbols, measured
   spread evidence, corporate-action completeness and explicit cost applicability.
   Supplemental normalized financial facts need qualified provider provenance.
   This is an administrative source artifact, not an API caller's assertion.
3. Original-publication historical bars may be supplied as
   `archived_market_evidence` with `archived_market_proof_hash`. Their provider
   must declare verified original publication. Current observations are not
   backdated; overlapping values must match exactly or the snapshot is rejected.
   Use `historical_dataset_hash` in `money.backtest.lean` for the reviewed LEAN
   corpus. It excludes per-job IDs but includes economic/source/publication facts.
4. Register and independently approve the immutable Qlib artifact using
   `scripts/model_registry.py`. The manifest pins its registry ID and artifact
   hash. Withdrawal takes effect even for a request with an older cutoff; no
   automatic promotion or fallback model exists.
5. Select exact inference provider/model/endpoint/protocol and credential environment
   variable independently for TradingAgents, AI-HF and CrewAI. Configure token
   limits, temperature/reasoning, timeouts and rates only if known. Keys are read
   by the compute process, never embedded in the manifest or decision artifacts.
   Supported transports are OpenAI-compatible chat completions and Anthropic
   Messages; a provider must return the exact pinned model ID. Aliases that change
   model identity are rejected.
6. Budget maximum native calls and input/output limits explicitly. Default budget
   limits intentionally do not authorize an expensive complete native workflow.
   Preflight rejects limits smaller than the configured worst-case invocation
   reservations. Unknown actual usage remains pessimistically reserved.
7. Pin the LEAN image digest and reviewed study parameters, dated historical costs,
   dataset identity, historical eligibility/survivorship/corporate-action proofs.
   Supply a genuine independently reviewed host-egress verification artifact.
   `native_egress_policy_verified=true` alone is not proof: referenced bytes and
   actual host enforcement must also be qualified.

The LEAN `scenario_policy` must exactly equal the manifest's `signal_policy`;
the report carries its hash and signal generation checks it again. The study
uses the same Wilder ATR, rounded GBP levels, observed next-session open inside
the interval and calendar-day horizon. It never assumes an intraday fill. A
legacy percentage-threshold study cannot qualify a different displayed ATR setup.

Offline validation performs no paid call:

```sh
uv run python scripts/validate_live.py --manifest /run/money-qualified/manifest.json --sha256 <reviewed-digest>
```

Set backend `MONEY_ENV=production`, `MONEY_RESEARCH_MODE=live`,
`MONEY_LIVE_MANIFEST`, `MONEY_LIVE_MANIFEST_SHA256`, PostgreSQL `DATABASE_URL`,
the independent `RESEARCH_API_TOKEN` and workspace configuration. Mount the
read-only manifest/proofs in the API and worker. Provider secrets belong only to
the worker: `EODHD_API_KEY`, `COMPANIES_HOUSE_API_KEY` and the explicitly selected
inference credential variables. Optional Trading 212 metadata credentials are
not necessary for the manifest eligibility reader and do not prove ISA coverage.

Production startup rejects an unconfigured/demo runtime, invalid manifest/hash,
unqualified/stale providers, SQLite or missing API authentication. This is only a
configuration gate: native package/source equality, registry promotion, provider
responses, historical validation and host controls still need live qualification.
The worker fails safely if these are unavailable; it never substitutes DEMO.L.
Offline model withdrawal/administration uses a separate database-only settings
contract so an expired provider manifest cannot prevent an operator withdrawing
an unsafe model. Production administration still requires PostgreSQL and direct
authorized database credentials; there is no public administrative-code endpoint.

## Runtime boundaries and limitations

The native inference subprocess receives only its frozen inputs and inference
credential; unrelated environment credentials, databases and provider tools are
denied. Source fingerprints are checked before native imports. Current installed
CrewAI differs from the pinned checkout, and three other pinned packages are
absent. Build/lock/review a qualified native image; do not disable attestation.
The explicit test bypass labels its result demo and live adapters reject it.

LEAN runs the fixed Money study, not arbitrary source, in a non-root, offline,
read-only OCI sandbox with CPU/memory/PID/time limits. Do not mount a privileged
container socket into the API or Netlify. The isolated compute host must provide
the reviewed LEAN execution facility; default Compose is not its qualification.

Discovery invokes the approved numeric model without exposing its opinion to
qualitative first-pass firms. No-discovery candidates are rejected before paid
research. Sealed reports, initial CIO audit and LEAN must exist before challenge
artifacts. Missing independent challenge capabilities leave disputes unresolved;
the system does not iterate until agreement or overwrite first-pass reports.

Native correspondence is now implemented behind explicit
`enable_native_cross_examination=true`; the default remains disabled until the
operator qualifies its runtimes and budget. Set
`cross_examination_maximum_challenges` (1–24, default 8). At most two rounds run.
Manifest budget validation includes worst-case TradingAgents two calls, AI-HF one
call and independent CrewAI one call per challenge/round. No provider/model switch
or quiet reduction in specialist work is used to fit a budget.

Responses and independent verifications run in separate bounded native processes.
Respondents receive only their own sealed report, objective snapshot and explicit
challenge. Original claim/report hashes, permitted citations and independent
source quotation/recalculation are checked. Each paid-capable invocation reserves
tokens before execution, seals its typed result before settlement and reuses that
result on recovery. Unmeasured interrupted calls retain the pessimistic charge.
Qlib/LEAN rechecks cannot manufacture new validation or missing controls. The
original audit, reports and hard vetoes remain immutable; a later correspondence
finding is not permission to remove their deterministic vetoes. This path is
tested with scripted native inference, not qualified paid production execution.

## Reviewed filing-document ingestion

`VerifiedInstrument.filing_documents` optionally contains up to four
`FinancialCurrencyProof` objects: exact Companies House number, filing transaction
ID, `currency="GBP"`, raw `document_content_hash` and review `evidence_hash`.
The accounting units require independent review; a GBP/GBX stock quotation is
not that proof. A changed document representation requires a new review.

`LiveManifest.filing_document_storage_hosts` optionally contains up to eight exact
hosts with `review_evidence_hash`. Both review hashes must reference actual bytes
in `qualification_artifacts`. No wildcard/default cloud-storage host is trusted.
Companies House qualification must cover both `filing` and `financial`. Install
the pinned stream-read-xbrl converter in the research image; missing parser or
unsupported PDF-only representation is an explicit failure, not a fallback.

Selected documents run behind the provider circuit inside the supervised worker.
Their safe provenance is sealed inside snapshot evidence, never retroactively
added to the factory-time manifest. No raw document or signed location enters the
packet. Availability is retrieval time, not the filing's processed date or an
assumed historical publication date. Freshness is capped by all reviewed provider
and identifier limits. WORK_FILINGS.md documents official APIs and exact live
qualification requirements. Empty selections retain the existing explicit
reviewed-facts path; they do not silently select or invent financial statements.

Run opt-in genuine-provider tests only after reviewing cost/data access:

```sh
MONEY_RUN_PRODUCTION_INTEGRATION=1 uv run pytest tests/production -v
```

Native test-specific input paths are documented in WORK_NATIVE.md. Test runners
report `PASSED`, `FAILED`, `SKIPPED_MISSING_CREDENTIAL` or
`SKIPPED_EXTERNAL_UNAVAILABLE`; a skip is not successful qualification.

Offline baseline dataset preparation/training is documented in WORK_TRAINING.md.
It uses actual NumPy ridge fits compatible with the Qlib inference feature order,
not a claim of native Qlib training. Source hashes/timestamps alone are not
independent source verification; outputs remain explicitly unqualified.
