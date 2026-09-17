# Production execution record — 2026-09-17

Status: **PRODUCTION BLOCKED**. This is an execution record, not a qualification
manifest or a provider/runtime approval. No production evidence was fabricated.
The supplied production settings were not changed.

## Implemented locally

- `Trading212LiveEligibilityService` refreshes only the authenticated instrument
  metadata endpoint and intersects it with independently reviewed ISA, current
  availability, identifier and ethical evidence. Unknown, conflicting or expired
  rows fail closed. The refresh timestamp never renews a review. All account,
  portfolio, order and execution capabilities remain outside this client.
- `LiveManifest.instrument_catalog` may refer to an external JSON array of
  `VerifiedInstrument` records by `(sha256, relative_path)`. That exact pair must
  appear in `qualification_artifacts`. `load_manifest` checks the catalogue bytes,
  all referenced proofs, uniqueness and financial dataset coverage. Catalogue-only
  manifests require no embedded seed list. Snapshot construction, licensing,
  search and acceptance selection consume `reviewed_instruments`.
- `scripts/qualify_providers.py` probes EODHD OHLCV, corporate actions and news,
  or Companies House company profiles and filings. It stores actual normalized
  response artifacts and validates their hashes and coverage before admission.
  A fresh usage-rights review and its real supporting bytes are required to issue
  a `ProviderQualification`. Financial document coverage and original-publication
  history are explicitly not inferred from these probes.
- `data/configuration/live-inference.json` supplies three independently usable
  `InferenceSelection` objects for `openai`, `gpt-5.4-2026-03-05`, the HTTPS Chat
  Completions endpoint and the environment variable name `OPENAI_API_KEY`. No key
  is stored. These selections pass offline schema validation; account access and
  actual model responses have not been qualified. The exact snapshot and endpoint
  are documented in the [official model page](https://developers.openai.com/api/docs/models/gpt-5.4).
  A complete manifest still needs deliberate invocation budgets matching these
  limits and the actual native workflows.
- `scripts/train_qlib.py` invokes attested native Qlib training on archived Money
  evidence, with training-only preprocessing and purged walk-forward/OOS splits.
  It cannot promote a model. Outputs retain `UNPROMOTED` and `pit_validated=false`
  until the existing independent proof/approval contract is satisfied.
- Native Python capability restrictions now also block datagram sends, reverse
  DNS and non-gateway legacy DNS. This is tested containment, not OS/network
  isolation qualification.
- `scripts/backend_acceptance.py` fixes SQLAlchemy result iteration in the real
  recovery/restore drill, reports safe source locations without exception values,
  and supports `--full-tests` against its own disposable PostgreSQL cluster.

## Genuine remaining qualification inputs

Trading 212's [documented metadata response](https://docs.trading212.com/api/instruments/instruments)
does not contain Stocks ISA or current purchase-availability proof. A current
independently verifiable source for those fields and ethical reviews remains
necessary. Authenticated metadata alone must not be promoted into that evidence.

EODHD account dataset access, Companies House filing/financial conversion and the
source-specific usage rights remain unverified. The checked-in commercial licence
table still records unresolved rights; no entry was changed to fabricate a grant.
Current versionless market retrieval cannot be backdated into historical
point-in-time evidence. No production archive, qualified model, independent
approval, historical eligibility/survivorship study or LEAN audit is available.

The worker image needs a resolvable, tested pinned native dependency build. See
[the concrete native build record](NATIVE_WORKER_BUILD.md) for conflicts, missing
dependencies, source attestations and the unresolved ChromaDB advisories. Neither
source hashing nor Python restrictions establish a secure qualified runtime.

## Environment and deployment

The authenticated Railway CLI is installed (5.57.7), but its `whoami` and `status`
requests fail resolving `backboard.railway.com`. Provider credentials supplied on
Railway are not present in the local process environment. The Docker API socket
is denied by the execution sandbox. Local HTTP smoke cannot bind a loopback port
(`EPERM`). These restrictions were not bypassed.

No remote service variables, project, database or service were created/changed.
Money CRASHED and Postgres ONLINE are user-reported states, not freshly verified
hosted observations. Worker state and hosted health/heartbeat are unverified.

There is no genuine `data/qualified/live/manifest.json`, no manifest SHA256,
no Qlib registry ID/artifact hash, and no native-egress qualification hash. The
requested manifest validation, deployment and hosted end-to-end/restart acceptance
must follow actual qualification; no substitute hash or synthetic pass was used.

## Validation

The isolated real-PostgreSQL recovery drill completed successfully: concurrent
migrations, PostgreSQL integration checks, killed-worker recovery, stale-lease
fencing, sealed report retention, database restart, backup restore and immutable
published output. It used synthetic research in a disposable local cluster,
explicitly reported `production_qualified=false`, and removed its temporary data.
This does not qualify the existing Railway database or live research.

The explicit production-integration invocation collected eleven tests and skipped
all eleven because `QUALIFICATION_MANIFEST_REQUIRED`. These skips are **not a
successful production acceptance**. Web unit tests (315), lint, type checking and
production build passed; HTTP smoke is blocked by loopback listener permission.

The full ordinary `uv run pytest` invocation passed 1,177 tests with 157 skips and
28 visible warnings. The skips include PostgreSQL cases without a database URL
and the eleven opt-in production cases. Subsequent safe-diagnostic coverage added
one test; the backend-runner suite passes all ten tests. Python Ruff, mypy (113
source files), reference table integrity and deployment-boundary checks pass.
The final provider/security/catalogue regression run passed 170 tests.

The expanded `uv run python scripts/backend_acceptance.py --full-tests` run then
passed **1,322 tests, with 13 skips and 28 warnings**, against its disposable real
PostgreSQL cluster. The same run passed concurrent migrations, killed-worker
recovery/fencing, sealed report retention, database restart and backup restore.
It explicitly records local synthetic scope and `production_qualified=false`.
The isolated temporary database was stopped and removed successfully.

The repository secret-pattern scan found no private keys or recognized API-token
patterns. No credentials were printed or added to configuration/artifacts.
