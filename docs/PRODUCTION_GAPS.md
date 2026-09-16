# Production gap matrix

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
