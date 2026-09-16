# Production gap matrix

## Personal R&D scope — supersedes commercial licensing as this task's gate

| Requirement | Status | Actual remaining gap |
| --- | --- | --- |
| Personal real-data adapters, queue and evidence UI | VERIFIED | Local contracts/fixture persistence tests only; no fake live result |
| BoE macro fetch | FAILED |21 actual IUDBEDR observations fetched2026-09-16T21:11:19Z; latest repeated probes fail PROVIDER_UNAVAILABLE |
| BARC.L / AAPL Yahoo fetch | BLOCKED_EXTERNAL_INFRA | Actual bounded calls failed Yahoo DNS; no quote/snapshot accepted |
| Railway API/Postgres/private worker | BLOCKED_EXTERNAL_INFRA | Existing project/service names supplied, connector administrator-disabled; no remote state verified |
| UK / US official filings | BLOCKED_CREDENTIAL | CH key + reviewed company number; SEC contact User-Agent |
| Native multi-firm R&D | NOT_IMPLEMENTED | Safe evidence/citation bridge and R&D report barrier still needed; commercial PIT/currency contracts deliberately unchanged |
| Hosted login→result→return | BLOCKED_EXTERNAL_INFRA | No healthy connected backend; browser listeners denied locally |
| Commercial isolation | VERIFIED | Personal mode does not claim commercial rights; production qualification/rights gates remain unchanged |

`live_rnd` stores evidence studies, not qualified recommendations. Native components
remain NOT_CONFIGURED and final state INSUFFICIENT_EVIDENCE. No synthetic fallback.

## Final commercial release follow-up — 2026-09-16

These supersede the historical starting gaps below. **PRODUCTION BLOCKED.**

| Requirement | Status | Actual state / remaining gate |
| --- | --- | --- |
| Publish preserved commercial work | VERIFIED | Local and GitHub main match 6846bdc; clean starting tree and commercial implementation preserved. This follow-up needs its own publication |
| Publish company-search/acceptance follow-up | BLOCKED_EXTERNAL_INFRA | Targeted git add denied creating index.lock; no stale lock. All modified/untracked work preserved, no new release SHA |
| Existing Netlify public web | VERIFIED | Current-SHA live-web run 35146196319 records root HTTP 200 and route/asset/auth-closure/security checks. Deployed SHA/authenticated journey remain unverified; direct Netlify access blocked |
| Current CI | FAILED | Run 35146196196: one PG test, backend drill, legacy HTTP smoke and research audit fail. No full trace available; additional safe diagnostics do not constitute a repair |
| Company search/live admission | VERIFIED | Reviewed catalogue, bounded identifier/name search, per-use freshness/ethics/rights checks and durable rate limits. Real live catalogue still requires a qualified manifest |
| Production demo prohibition | VERIFIED | Startup rejects both switches; enqueue, worker and read-time tests deny synthetic production research |
| Offboarding/retention | VERIFIED | Durable closure, fresh-password ownership transfer, reviewed billing/retention workflow and local erasure drill; immutable evidence retained. Real Stripe closure, approved legal retention and PostgreSQL races remain launch gates |
| Per-call accounting | VERIFIED | Fenced immutable receipts, individual inference attempts and native/provider-operation timing; actual and estimated costs separated. Unobserved provider subcalls/vendor invoices remain unknown |
| ChromaDB dependency | FAILED | API/email/billing dependency set now excludes it and audits clean; research set retains four distinct unresolved advisories. Native capability containment is not a package patch; research CI still fails |
| Node advisory scan | VERIFIED | Current 6846bdc CI production audit passes high-severity threshold; local fresh retrieval remains DNS-blocked |
| PostgreSQL / backup / recovery | BLOCKED_EXTERNAL_INFRA | Isolated local drill cannot initialize shared memory; real-PG tests skipped without URL. No production restore proof |
| Railway deployment access | BLOCKED_EXTERNAL_INFRA | Owner selected Railway; connector is DISABLED_BY_ADMIN / NOT_AVAILABLE and CLI 5.57.5 status/whoami fail API DNS. No project/service/database state verified; no alternate account or access workaround used |
| Container / browser acceptance | BLOCKED_EXTERNAL_INFRA | Current CI builds both images and verifies control-plane dependency exclusion/Compose. Local socket/listener denied; CI HTTP failure skips commercial/browser checks; no hosted-process acceptance |
| SMTP / Stripe / monitoring | BLOCKED_CREDENTIAL | No available credentials/destination; real delivery, payment lifecycle and production alerts unqualified |
| Live research / commercial data rights | BLOCKED_CREDENTIAL | Eleven explicit live checks skip without manifest/hash and genuine native qualification snapshot; rights, ISA evidence, model promotion and runtime qualification remain mandatory |

Exact final commands/counts are recorded in VERIFICATION.md. The locally verified
rows are not production acceptance or legal approval.

Audit started 2026-09-16 from `6607d76`. Baseline: 94 Python tests passed,
one PostgreSQL test skipped without `TEST_DATABASE_URL`. This matrix describes
the starting gaps; completion evidence belongs in VERIFICATION.md.

| Requirement | Existing implementation | Gap / severity | Implementation target | Verification |
| --- | --- | --- | --- | --- |
| Research-only boundary | Canonical contracts, no execution API | Extend automated capability checks / critical | Static deployment/security checks | Forbidden-capability test |
| Durable jobs | SQL queue, leases, fencing, immutable barrier | Classified retries, idempotency, timeouts / critical | Store, worker, migrations | Concurrency, recovery, real PostgreSQL CI |
| Authentication | Signed private workspace cookie | Durable sessions/logout/login limits / critical | Web + authenticated session service | CSRF, expiry, revocation, limits |
| Ownership | Explicit single user | Persist workspace ownership / high | Store/API predicates | Resource isolation |
| UK providers | Coverage contracts and injected XBRL converter | Actual fetchers, identifiers, licensing and qualification / critical | Money-owned UK/provider adapters | Provider contracts + opt-in live checks |
| Source security | No production fetchers | SSRF, redirect/decompression/markup limits / critical | Bounded allowlisted transport | Adversarial URL/document tests |
| Discovery | Evidence-presence channels | TA-Lib calculations and explicit thresholds / high | Deterministic scanners | Native calculation tests |
| Native firms | Validated injected runners | Native assembly, snapshot tool replacement, deadlines / critical | Money-owned upstream assemblies | Escape/isolation + opt-in native tests |
| Qlib models | Predict seam and promotion assessment | Durable approved registry/PIT metadata / critical | Model registry + artifact admission | Unapproved/hash/PIT rejection |
| LEAN | Injected validation interface | Isolated real runner + versioned costs / critical | Runner and cost contracts | Timeout/resource/cost validation |
| CrewAI CIO | Demo evidence existence audit | Real tasks, independent checks, bounded challenges / critical | Native CIO adapter | Unsupported claims/veto/isolation |
| Compute budgets | Documentation only | Provider-neutral settings, durable reservation/accounting / high | Budget and inference contracts | Concurrent reservation/cost unknown |
| Evidence/PIT | Frozen hashes, timestamps, strict payloads | Availability, identifier/action/quality coverage / critical | Additive contracts and quality gates | Future/ambiguous/action tests |
| Outcomes/replay | Pure outcome metrics, QuantStats | Durable evaluations, replay artifacts, scheduling / high | Offline/scheduled Money services | Immutable replay and outcome tests |
| Product | Existing responsive workspace | Full states, separate provenance/report exploration / high | Next.js product | Unit/build/HTTP and browser smoke |
| Operations | Compose/CI/basic health | Structured metrics, backup/restore, dependency checks / high | Health, CI, operations docs | Required commands, explicit blockers |
| Live acceptance | No qualified real critical path | Credentials, licensed data, model, runtime/host acceptance / critical | Opt-in production qualification | Never inferred from mock tests |

## Deployment follow-up at `acb38f1`

| Requirement | Observed implementation | Gap / severity | Implementation target | Verification |
| --- | --- | --- | --- | --- |
| Live root | Correct paths/dynamic routes; supplied log/API confirms absent adapter/functions | Raw `.next` uploaded without SSR runtime / critical | Explicit locked Next adapter plus fail-closed post-build artifact guard | 19 artifact tests and HTTP checker; local latest CLI/live HTTP blocked, repaired deployment not observed |
| Route aliases | Dashboard/jobs/health existed under other paths | `/dashboard`, `/research`, `/system` had incorrect screen selection / high | Aliases mapped without changing SSR | Included in final 129 web tests and successful Next build |
| Remote CI | `acb38f1` run completed | pytest, HTTP smoke, pip-audit failed / critical | Bounded failure diagnostics + actual traceback/root-cause repair | Remote job metadata observed; full logs unavailable |
| Restore/restart evidence | Operations runbook only | Actual PostgreSQL drill missing / critical | Isolated real-PG kill/recover/backup/restore runner, migration lock | Runner safety tests pass; shared-memory permission blocks real drill |
| Native correspondence | Default unavailable responders | Actual firm responses and independent checking / high | Native TA/AI-HF/CrewAI plus deterministic quant/validation rechecks, durable paid-call journal | Scripted native and recovery tests; paid qualification remains blocked |
| Filing documents | Filing index plus separate converter | Authoritative selected document-to-snapshot path missing / high | Reviewed content/units/hosts, bounded official fetch, converter and safe immutable provenance | Adversarial transport/assembly tests; live key/parser/source qualification still required |
| Correspondence UX | Initial audit and raw packet only | Challenges/responses/independent checks not separately inspectable / high | Separate accessible cross-examination tab, expandable rounds and source checks | Component tests and build; real browser blocked |
| Smoke lifetime | Some child waits/shutdowns unbounded | Hung processes can obscure original failure / high | Finite operation waits, termination escalation, retain data until safe | Eight lifecycle tests; not a diagnosis of the earlier remote failure |
