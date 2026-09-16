# Production verification — 2026-09-16

**PRODUCTION BLOCKED.** This records observed local evidence, not an assertion
that GitHub CI, Netlify production, Deploy Preview or a live research path passed.
Starting baseline: `main` at `6607d76`, 94 Python tests passed and one PostgreSQL
test skipped. Working changes are uncommitted; no push or deployment was performed.

Allowed statuses: VERIFIED, FAILED, BLOCKED_CREDENTIAL, BLOCKED_ENVIRONMENT,
NOT_IMPLEMENTED. A passing contract/scripted-inference test is not live qualification.

## Checks and acceptance evidence

| Requirement | Status | Evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Locked Python installation | VERIFIED | `uv sync --locked`: 191 resolved, 188 checked; existing lock retained | Local Python 3.12 | None for base runtime |
| Python lint | VERIFIED | `uv run ruff check .`: all checks passed | Local | None |
| Python typing | VERIFIED | `uv run mypy src/money`: 76 source files clean | Local | None |
| Expanded Python suite | VERIFIED | Final `uv run pytest -q`: 354 passed, 19 explicitly skipped, 28 dependency deprecation warnings in 15.49s | Local | Skips are nine real-PostgreSQL cases and ten genuine-provider/native opt-in cases |
| SQLite migrations/schema drift | VERIFIED | Integration suite applies all migrations and Alembic check reports no operations | Local SQLite only | Does not establish PostgreSQL semantics |
| PostgreSQL locking/migrations/recovery | BLOCKED_ENVIRONMENT | Nine PostgreSQL tests skipped without TEST_DATABASE_URL; PostgreSQL 17 service configured in CI | Local sandbox | Accessible disposable PostgreSQL; shared-memory/listener restrictions |
| Durable enqueue/worker/retrieval | VERIFIED | API TestClient → HTTP 202 → committed queue → separate subprocess → fresh API/store retrieval | Local SQLite/process tests | Actual network/browser/PostgreSQL path below |
| Claims/leases/fences/retries/deadline recovery | VERIFIED | Concurrent claims, stale owner, timeout/crash and sealed checkpoint tests | Local; real PG variants configured | Actual PG run still required |
| Idempotency/budget admission/settlement | VERIFIED | Concurrent atomic batch admission, rollback, usage reconcile, unknown cost and stale-worker tests | Local SQLite | Nine PG skips include budget variants |
| Private authentication/authorization | VERIFIED | HttpOnly/Secure/SameSite cookie, CSRF, expiry/signatures, durable revocation/login rate limit and workspace predicates tested | Local API/web tests | Deployed cookie/proxy/TLS/browser acceptance |
| Source/prompt security | VERIFIED | SSRF, DNS/IP/redirect, body/decompression/archive/XML/markup limits; native tool/file/network denial; strict output/usage JSON | Local adversarial tests | Host OS egress is not proven by Python guards |
| API request receive deadline | VERIFIED | Slow body cancelled with 408 before business logic; small chunks replay as one bounded body | Local ASGI tests | Host TLS/header/connection limits remain deployment controls |
| Full history / bounded qualitative context | VERIFIED | 1500-bar fixture retains exact latest80 bars plus every non-price record, reports omissions, rejects unavailable/conflicting omitted history and out-of-context citations | Local native contract tests | No live model-quality claim |
| Research-only / Netlify compute boundary | VERIFIED | `uv run python scripts/check_deployment.py`; adversarial static capability/import/client-secret tests | Local | Static checks complement deployment/runtime policy |
| Eligibility/ethics/currency/£200 gates | VERIFIED | Unknown/stale/nonstock/currency/exclusion/capital tests fail closed | Local contracts | Actual current ISA and business proofs absent |
| GBX/GBP, quality, archive/PIT | VERIFIED | Raw/normalized price properties, financial/source conflicts, future timestamps, action-basis and archive/current mismatch tests | Local | Genuine historical publication corpus unavailable |
| TA-Lib / discovery channels | VERIFIED | Native deterministic indicators; title catalyst/financial-delta triggers; private Qlib scan reuse and union tests | Local synthetic fixtures | Live coverage/model qualification required |
| UK HTTP provider implementations | VERIFIED | Real endpoint/auth/normalization/coverage code with deterministic transport contract tests | Local test transport | No successful credentialed production call claimed |
| Real ISA availability | BLOCKED_CREDENTIAL | Metadata client does not invent ISA membership; reviewed manifest artifact required | Not run live | Verified ISA-specific source/coverage and optional metadata credentials |
| Real market/actions/news/filings | BLOCKED_CREDENTIAL | Five opt-in provider checks explicitly skipped | Not run live | EODHD entitlement/key, Companies House key, reviewed manifest/licences |
| TradingAgents / AI-HF real paid qualification | BLOCKED_CREDENTIAL | Native assemblies and escape tests implemented; AI-HF pinned lifecycle exercised with scripted test inference only | Local read-only checkout | Pinned installed runtimes, inference credentials, host qualification |
| Qlib real approved production model | BLOCKED_ENVIRONMENT | Native DatasetH/LinearModel seam, strict artifact/hash/registry tests; no fabricated score | Local contracts | Installed pinned Qlib, real PIT corpus, independent OOS review/promotion |
| Offline baseline training | VERIFIED | 20 actual NumPy training/CLI tests, future-label mutation isolation, purged/embargoed temporal folds, source/artifact/report hashes and rejected automatic promotion | Local synthetic data only | Production artifact remains PIT-unqualified and UNPROMOTED |
| LEAN actual engine / historical costs | BLOCKED_ENVIRONMENT | Fixed OCI study/resource/PIT/cost/sensitivity contracts tested; real engine not run | Local tests | Accessible Docker, digest image, archived dataset/cost/survivorship controls |
| CrewAI real paid qualification | BLOCKED_CREDENTIAL | Actual Flow/Agent/Task lifecycle exercised with scripted inference, including isolated subprocess | Local test/demo only | Installed source mismatches pin; correct runtime and inference credentials |
| Blind barrier / immutable reports | VERIFIED | Capability, provider-escape, cross-connection barrier, DB mutation and peer-opinion exclusion tests | Local; PG CI coverage | Native deployed escape qualification pending |
| CIO / Red Team / consensus / challenges | VERIFIED | Independent numeric recomputation, unsupported-claim blocking, VETO, bounded immutable challenge provenance, no vote consensus | Local | Native challenge responders not enabled; disputes remain unresolved |
| Signal design/expiry/invalidation/replay | VERIFIED | Recomputed risk/cost/targets; exact LEAN scenario-policy binding; actual TA-Lib ATR parity and calendar-day horizon; forged artifacts rejected; read-time expiry; immutable replay | Local test fixtures | No positive live signal qualified |
| Strong candidates / calibrated confidence | NOT_IMPLEMENTED | Intentionally unavailable without empirical qualification | None | Sufficient independent OOS outcomes and component calibration |
| Research-reference outcomes / QuantStats | VERIFIED | Durable operator-supplied outcome records and existing native diagnostics; research != actual trade | Local fixtures | Automated ingestion and adjusted basis remain unimplemented |
| Web installation | VERIFIED | `npm ci --prefix apps/web` passed (384 packages; bounded fetch retries/timeouts); earlier offline lock reproduction also passed | Local cache/network-restricted shell | npm emitted existing ESLint9.39.4 deprecation; advisory success not inferred |
| Web lint / typecheck / unit tests | VERIFIED | npm lint/typecheck passed; 53 Vitest tests passed | Local Node | None |
| Next.js production build | VERIFIED | `npm run build --prefix apps/web` passed without research/provider secrets | Local | Not a Netlify-hosted build |
| Browser / web HTTP E2E | BLOCKED_ENVIRONMENT | Real login→202→worker→sealed packet→reload smoke implemented; listener attempts hit EPERM | Local sandbox | Allowed localhost listeners/Chromium runtime; configured in CI |
| Compose configuration | VERIFIED | `docker compose config --quiet` passed using disposable nonproduction values | Local CLI | No running containers implied |
| Docker image build | BLOCKED_ENVIRONMENT | `docker build -t money-production-check .` denied Docker socket access | Local sandbox | Accessible daemon |
| Netlify CLI build | BLOCKED_ENVIRONMENT | `npx --yes netlify-cli@latest build` failed registry.npmjs.org ENOTFOUND | Local sandbox | Registry/network and existing linked-site context |
| Dependency advisory scans | BLOCKED_ENVIRONMENT | npm audit DNS failure; locked Python export succeeded but pip-audit package/advisory retrieval blocked | Local sandbox | Advisory/package network access; not a clean scan |
| GitHub CI / production / Deploy Preview | BLOCKED_ENVIRONMENT | Existing topology preserved; no push/deploy and no remote green result observed | External | Owner-run remote CI/deployment acceptance |
| Backups / restore / OCI operations | VERIFIED | OPERATIONS.md documents commands, pools/resources, staged migrations, isolated restore and rollback | Documentation only | Actual backup/restore drill and host controls unverified |
| Scheduled ingestion/calibration & broad corporate-action transforms | NOT_IMPLEMENTED | Explicitly listed in IMPLEMENTATION_PLAN.md | None | Remaining engineering plus qualified data |
| Money Graphify update | VERIFIED | Final `graphify update .`: 1512 nodes, 4348 edges, 106 communities; AST-only, no LLM needed | Local | Semantic docs not regenerated; upstream graphs untouched |

## Release blockers

1. Verified ISA/ethical/source/licensing data, original-publication history, complete
   corporate actions and dated historical cost assumptions.
2. Live market/filing/inference credentials and successful genuine-provider tests.
3. Pinned native dependency images, approved Qlib artifact, isolated LEAN engine
   and independently verified host egress/resource controls.
4. Real PostgreSQL, browser smoke, Docker, dependency audit, GitHub and existing
   Netlify/Deploy Preview acceptance, plus an actual backup restore drill.
5. Remaining engineering explicitly listed in IMPLEMENTATION_PLAN.md. These are
   not relabelled as credential blockers or hidden behind demo fallbacks.

Detailed workstream evidence: WORK_BACKEND.md, WORK_NATIVE.md, WORK_WEB.md,
WORK_DEPLOY.md, WORK_PROVIDERS_REVIEW.md, WORK_BUDGET_RECOVERY.md and WORK_TRAINING.md.
The final consolidated counts above supersede intermediate workstream counts.
Graphify's pre-existing
installed Claude-skill/package version warning does not authorize global changes.
