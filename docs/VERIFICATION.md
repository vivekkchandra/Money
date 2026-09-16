# Production verification — 2026-09-16

**PRODUCTION BLOCKED.** The reported live Netlify 404 has not been repaired or
independently reproduced from this restricted session. Local builds are not live
deployment evidence. This follow-up starts from pushed `main` / `acb38f1`, not the
older foundation recorded in historical work notes. No new commit, push, remote
configuration change or deployment was completed.

Allowed statuses: VERIFIED, FAILED, BLOCKED_CREDENTIAL, BLOCKED_EXTERNAL_INFRA,
NOT_IMPLEMENTED. A passing contract/scripted-inference test is not live qualification.
VERIFIED always applies only to the stated scope/environment. Legacy drill tools
may emit `BLOCKED_ENVIRONMENT`; this document classifies it as external infrastructure.

## Production incident and remote evidence

| Requirement | Status | Actual command/test and evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Correct release baseline | VERIFIED | Git branch/log/remote and connected GitHub branch API confirm `main` / `acb38f188a763af19abccb4954f015a1be2913b5` | Local + GitHub read API | Follow-up remains uncommitted |
| Local existing Netlify link | VERIFIED | `.netlify/state.json`: `c66f8305-899f-4deb-92c8-b2ee55f5acab` | Local only | Remote account/project identity still requires CLI confirmation |
| Netlify direct CLI/status | BLOCKED_EXTERNAL_INFRA | `npx --yes netlify-cli@latest status`, `logs --source deploy --since 24h`, `build` fail registry DNS; offline latest CLI resolution also fails | Restricted shell | Direct remote identity/status and published SHA unavailable |
| Supplied production deploy log | VERIFIED | User-provided deploy `6aaaa3c8a1d24e0009cc631b`: production/main, root config, correct apps/web base/publish, successful Next build, 40 files/zero functions uploaded, post-processing complete | User-supplied actual Netlify log | Commit SHA omitted; direct live HTTP still unavailable |
| SSR deployment packaging | FAILED | Supplied log has dynamic routes but no adapter; subsequent user API inspection confirms `plugins: []` and `available_functions: []` | User-supplied production log/API findings | Explicit adapter fix now local, but not published or host-verified |
| Live production routes/assets | BLOCKED_EXTERNAL_INFRA | Bounded curl fails DNS; `npm run check:deployment --prefix apps/web -- https://neon-griffin-08e616.netlify.app --production` fails fetch; web retrieval also unavailable | Public HTTP attempts | No actual HTTP status observed; user-reported generic 404 remains unresolved |
| Local monorepo/SSR invariants | VERIFIED | Deployment checker enforces base `apps/web`, command `npm run build`, publish `.next`, root catch-all, no static export/SPA rewrite/shadow config; Next manifest contains root/API routes | Local artifacts/config | Not proof of what Netlify uploaded |
| GitHub baseline CI | FAILED | [Run 35106931767](https://github.com/vivekkchandra/Money/actions/runs/35106931767): pytest, web HTTP smoke and Python dependency audit steps failed; Next and Docker/Compose builds passed | Observed GitHub jobs/steps API | Full failure logs unavailable; no guessed diagnosis or green rerun |
| Git-linked Netlify production/Preview | BLOCKED_EXTERNAL_INFRA | Commit status API has no statuses; supplied production log proves main was fetched and deployed, not that SSR worked | GitHub API + supplied Netlify log | Corrected adapter build, deployed SHA, live acceptance and Preview qualification |
| Publish this follow-up | BLOCKED_EXTERNAL_INFRA | Later staging denied `.git/index.lock`; push dry-run failed GitHub DNS; GitHub write tool rejected unavailable approval | Restricted filesystem/network/tool policy | Write/network-enabled execution; no remote write occurred |

No `netlify init`, project creation/deletion, fake index, SPA redirect or
speculative publish-directory rewrite was performed. After the supplied log,
`@netlify/plugin-nextjs` was explicitly configured and its available `5.15.11`
package installed/locked offline. This is not a completed Netlify build or current
advisory scan. `live-web.yml` adds a
read-only public acceptance check; it has not run remotely. CI now produces
bounded test/advisory summaries without dumping assertion bodies or secrets.

## Checks and acceptance evidence

| Requirement | Status | Evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Locked Python installation | VERIFIED | `uv sync --locked`: 191 resolved, 188 checked; existing lock retained | Local Python 3.12 | None for base runtime |
| Python lint | VERIFIED | `uv run ruff check .`: all checks passed | Local | None |
| Python typing | VERIFIED | `uv run mypy src/money`: 79 source files clean | Local | None |
| Expanded Python suite | VERIFIED | Final `uv run pytest -q`: 536 passed, 43 explicitly skipped, zero failed, 28 dependency deprecation warnings in 28.20s | Local | Skips: 32 real-PostgreSQL variants and 11 genuine-provider/native opt-in cases |
| SQLite migrations/schema drift | VERIFIED | Integration suite applies all migrations and Alembic check reports no operations | Local SQLite only | Does not establish PostgreSQL semantics |
| PostgreSQL locking/migrations/recovery | BLOCKED_EXTERNAL_INFRA | `uv run python scripts/backend_acceptance.py` reports ENVIRONMENT_PERMISSION_DENIED at postgres_init; PostgreSQL 17.10 initdb shared-memory allocation denied | Isolated temporary local cluster | No migration/restore claim; permitted shared memory/listeners required |
| Migration serialization | VERIFIED | Transaction-scoped advisory lock and bounded connect/lock/statement timeouts implemented; SQLite migration regression passes; drill launches concurrent migrations | Code/SQLite tests | Actual PG advisory-lock execution still unverified |
| Durable enqueue/worker/retrieval | VERIFIED | API TestClient → HTTP 202 → committed queue → separate subprocess → fresh API/store retrieval | Local SQLite/process tests | Actual network/browser/PostgreSQL path below |
| Claims/leases/fences/retries/deadline recovery | VERIFIED | Concurrent claims, stale owner, timeout/crash and sealed checkpoint tests | Local; real PG variants configured | Actual PG run still required |
| Idempotency/budget admission/settlement | VERIFIED | Concurrent atomic batch admission, rollback, usage reconcile, unknown cost and stale-worker tests | Local SQLite | PG variants include budget/correspondence recovery |
| Native correspondence recovery | VERIFIED | Barriers before paid calls, immutable input/result hashes, per-call journal, interrupted settlement and stale-worker fences; stage-filtered reconciliation prevents double charging | Local SQLite/optional PG tests | Paid runtime and PG variants unverified |
| Live worker capability binding | VERIFIED | Regression proves runtime factory receives claimed/fenced store before correspondence captures it | Local patched-factory test | No deployed worker started |
| Private authentication/authorization | VERIFIED | HttpOnly/Secure/SameSite cookie, CSRF, expiry/signatures, durable revocation/login rate limit and workspace predicates tested | Local API/web tests | Deployed cookie/proxy/TLS/browser acceptance |
| Source/prompt security | VERIFIED | SSRF, DNS/IP/redirect, body/decompression/archive/XML/markup limits; native tool/file/network denial; strict output/usage JSON | Local adversarial tests | Host OS egress is not proven by Python guards |
| API request receive deadline | VERIFIED | Slow body cancelled with 408 before business logic; small chunks replay as one bounded body | Local ASGI tests | Host TLS/header/connection limits remain deployment controls |
| Full history / bounded qualitative context | VERIFIED | 1500-bar fixture retains exact latest80 bars plus every non-price record, reports omissions, rejects unavailable/conflicting omitted history and out-of-context citations | Local native contract tests | No live model-quality claim |
| Research-only / Netlify compute boundary | VERIFIED | `uv run python scripts/check_deployment.py`; adversarial static capability/import/client-secret tests | Local | Static checks complement deployment/runtime policy |
| Eligibility/ethics/currency/£200 gates | VERIFIED | Unknown/stale/nonstock/currency/exclusion/capital tests fail closed | Local contracts | Actual current ISA and business proofs absent |
| GBX/GBP, quality, archive/PIT | VERIFIED | Raw/normalized price properties, financial/source conflicts, future timestamps, action-basis and archive/current mismatch tests | Local | Genuine historical publication corpus unavailable |
| TA-Lib / discovery channels | VERIFIED | Native deterministic indicators; title catalyst/financial-delta triggers; private Qlib scan reuse and union tests | Local synthetic fixtures | Live coverage/model qualification required |
| UK HTTP provider implementations | VERIFIED | Real endpoint/auth/normalization/coverage code with deterministic transport contract tests | Local test transport | No successful credentialed production call claimed |
| Filing document → financial snapshot | VERIFIED | 102 new transport/assembly tests: exact filing/content/unit proof, redirects/credentials, safe provenance, freshness and no fallback; selected documents integrated in live builder | Injected HTTP/parser tests | Companies House key, reviewed manifest/hosts/units/licences and actual source qualification |
| Native XBRL source attestation | VERIFIED | 13 new tests reject missing/mismatched/unsafe standalone modules before import; exact read-only upstream source digest verified; injected parser explicitly unattested | Local attestation tests | Pinned parser package/dependencies not installed; no native live conversion claimed |
| Real ISA availability | BLOCKED_CREDENTIAL | Metadata client does not invent ISA membership; reviewed manifest artifact required | Not run live | Verified ISA-specific source/coverage and optional metadata credentials |
| Real market/actions/news/filings | BLOCKED_CREDENTIAL | Six opt-in provider checks, including real document conversion, explicitly skipped | Not run live | EODHD entitlement/key, Companies House key, reviewed manifest/licences/parser |
| TradingAgents / AI-HF real paid qualification | BLOCKED_CREDENTIAL | Native assemblies and escape tests implemented; AI-HF pinned lifecycle exercised with scripted test inference only | Local read-only checkout | Pinned installed runtimes, inference credentials, host qualification |
| Qlib real approved production model | BLOCKED_EXTERNAL_INFRA | Native DatasetH/LinearModel seam, strict artifact/hash/registry tests; no fabricated score | Local contracts | Installed pinned Qlib, real PIT corpus, independent OOS review/promotion |
| Offline baseline training | VERIFIED | 20 actual NumPy training/CLI tests, future-label mutation isolation, purged/embargoed temporal folds, source/artifact/report hashes and rejected automatic promotion | Local synthetic data only | Production artifact remains PIT-unqualified and UNPROMOTED |
| LEAN actual engine / historical costs | BLOCKED_EXTERNAL_INFRA | Fixed OCI study/resource/PIT/cost/sensitivity contracts tested; real engine not run | Local tests | Accessible Docker, digest image, archived dataset/cost/survivorship controls |
| CrewAI real paid qualification | BLOCKED_CREDENTIAL | Actual Flow/Agent/Task lifecycle exercised with scripted inference, including isolated subprocess | Local test/demo only | Installed source mismatches pin; correct runtime and inference credentials |
| Blind barrier / immutable reports | VERIFIED | Capability, provider-escape, cross-connection barrier, DB mutation and peer-opinion exclusion tests | Local; PG CI coverage | Native deployed escape qualification pending |
| CIO / Red Team / consensus / challenges | VERIFIED | Independent numeric recomputation, unsupported-claim blocking, VETO, bounded immutable challenge provenance, no vote consensus | Local | Original audit vetoes remain intact even if later correspondence adds evidence |
| Native structured correspondence | VERIFIED | TA native bear factory, AI-HF persona, separate CrewAI source verifier and deterministic Qlib/LEAN checks; explicit permitted citations, source hashes, max two rounds and invocation metadata | Scripted/contract tests | Disabled by default; genuine runtime/source/budget qualification required |
| Signal design/expiry/invalidation/replay | VERIFIED | Recomputed risk/cost/targets; exact LEAN scenario-policy binding; actual TA-Lib ATR parity and calendar-day horizon; forged artifacts rejected; read-time expiry; immutable replay | Local test fixtures | No positive live signal qualified |
| Strong candidates / calibrated confidence | NOT_IMPLEMENTED | Intentionally unavailable without empirical qualification | None | Sufficient independent OOS outcomes and component calibration |
| Research-reference outcomes / QuantStats | VERIFIED | Durable operator-supplied outcome records and existing native diagnostics; research != actual trade | Local fixtures | Automated ingestion and adjusted basis remain unimplemented |
| Web installation | VERIFIED | `npm ci --prefix apps/web` passed (385 packages after locked Next adapter addition); bounded fetch retries/timeouts | Local cache/network-restricted shell | npm emitted existing ESLint9.39.4 deprecation; offline audit message is not a current advisory scan |
| Web lint / typecheck / unit tests | VERIFIED | Final npm lint/typecheck passed; 129 Vitest tests across nine files passed, zero skipped/failed | Local Node | None in this scope |
| Netlify SSR deployment guard | VERIFIED | 19 adversarial artifact-fixture tests; explicit locked adapter, local plugin after it, actual bundled-handler manifest/path/catch-all and published JS/CSS required; raw Next output rejected | Local fixture tests | Actual hosted adapter/guard execution not verified; pinned layout/version upgrade requires review |
| Cross-examination UX | VERIFIED | 34 component tests cover separate organisations/rounds/original claims, independent findings, source links, unknown costs, malformed/inconsistent packets and safe markup | Local components | Actual browser acceptance still blocked |
| Smoke process bounds | VERIFIED | Eight lifecycle tests cover finite operation/shutdown deadlines, escalation and safe temporary-data retention | Local synthetic child tests | Does not identify the previous remote smoke failure cause |
| Next.js production build | VERIFIED | `npm run build --prefix apps/web` passed without research/provider secrets | Local | Not a Netlify-hosted build |
| Browser / web HTTP E2E | BLOCKED_EXTERNAL_INFRA | Real login→202→worker→sealed packet→reload smoke implemented; listener attempts hit EPERM | Local sandbox | Allowed localhost listeners/Chromium runtime; remote baseline HTTP smoke actually failed |
| HTTP deployment checker | VERIFIED | Tests reject generic 404, wrong routes, missing/HTML assets, unsafe headers/nonces and nonclosed APIs; root/dashboard/research/system aliases tested | Injected HTTP tests | Actual public HTTP acceptance blocked |
| Compose configuration | VERIFIED | `docker compose config --quiet` passed using disposable nonproduction values | Local CLI | No running containers implied |
| Docker image build | BLOCKED_EXTERNAL_INFRA | `docker build -t money-production-check .` denied Docker socket access | Local sandbox | Accessible daemon; old acb38f1 image passed remotely, not this follow-up |
| Netlify CLI build | BLOCKED_EXTERNAL_INFRA | Latest CLI resolution fails registry DNS; cached npx CLI27.8.0 offline diagnostic starts but cannot write its global preferences (`EPERM`) | Local sandbox | Supported writable CLI environment/network; no HOME override, credentials copied or permissions weakened |
| Dependency advisory scans | FAILED | Remote baseline npm production audit passed; Python pip-audit failed; local npm/pip-audit retrieval blocked by DNS | Remote baseline + local | Obtain real advisory/error details and fix safely; no suppression or blanket upgrades |
| Backups / restore / OCI operations | BLOCKED_EXTERNAL_INFRA | Actual isolated concurrent-migration/killed-worker/PG-restart/dump/restore drill implemented and CI-wired; local execution stops at initdb | Local attempted drill | Successful real drill and host controls required; documentation is not verification |
| Production compute/API/worker/PG | BLOCKED_EXTERNAL_INFRA | No qualified host/production connection config found; legacy loopback PostgreSQL not reachable | Local configuration inspection | Authorized OCI host, TLS PostgreSQL, production secrets/live manifest |
| Genuine production-integration suite | BLOCKED_CREDENTIAL | Explicit opt-in `MONEY_RUN_PRODUCTION_INTEGRATION=1 uv run pytest tests/production -q`: eleven explicit skips | Local opt-in | Missing manifest/hash and genuine snapshot/credentials; zero live passes |
| Scheduled ingestion/calibration & broad corporate-action transforms | NOT_IMPLEMENTED | Explicitly listed in IMPLEMENTATION_PLAN.md | None | Remaining engineering plus qualified data |
| Money Graphify update | VERIFIED | Final `graphify update .`: 1868 nodes, 5477 edges, 135 communities; AST-only, no LLM; curated graph backed up by tool | Local | Semantic document refresh not run; upstream graphs untouched |

## Release blockers

1. Network/write-enabled execution for this repository and the existing authorized
   Netlify project. Obtain deploy logs/SHA/adapter output, diagnose the reported
   404 packaging fix, publish reviewed changes and pass live HTTP route/asset/auth/header
   acceptance. The supplied log identifies missing adapter execution; the exact
   reason automatic installation did not occur remains unobserved; the user API
   inspection confirms the current runtime/plugin is absent. No replacement project is needed.
2. Obtain failing GitHub pytest, HTTP-smoke and Python-audit logs, fix actual
   failures and rerun release jobs. New safe summaries are not yet pushed.
3. Provide an authorized OCI compute host and TLS PostgreSQL; run safe migrations,
   API/worker/queue/restart acceptance and an actual isolated backup/restore drill.
4. Verified ISA/ethical/source/licensing data, original-publication history,
   corporate-action coverage, dated costs and live market/filing/inference keys.
5. Pinned native dependency images, independently validated/manually promoted Qlib
   artifact, isolated LEAN engine and verified host egress/resource controls; then
   genuine end-to-end paid qualification, not a fixture demonstration.
6. Remaining engineering explicitly listed in IMPLEMENTATION_PLAN.md. Automated
   universe/automatic filing qualification, unsupported action transformations, scheduled outcome
   calibration and optional external alert delivery are not credential blockers.

Detailed workstream evidence: WORK_BACKEND.md, WORK_NATIVE.md, WORK_WEB.md,
WORK_DEPLOY.md, WORK_FILINGS.md, WORK_PROVIDERS_REVIEW.md, WORK_BUDGET_RECOVERY.md and WORK_TRAINING.md.
The final consolidated counts above supersede intermediate workstream counts.
Graphify's pre-existing
installed Claude-skill/package version warning does not authorize global changes.
