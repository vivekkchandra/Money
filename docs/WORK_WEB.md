# Web production work — 2026-09-16

| Requirement | Status | Evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Durable session revocation, unique session rotation and login limiting | VERIFIED | Backend-backed session registration/validation/revocation and rate-limit contracts; security tests exercise revoked cookies, missing security service, limits, CSRF, malformed bodies and production configuration | Vitest + backend contract tests | Live PostgreSQL/Netlify verification remains separate |
| Lightweight control plane / idempotent enqueue | VERIFIED | Every request validates session against durable registry, then forwards bounded allowed paths; browser retains idempotency key across failed submissions; no compute imports | Unit tests/build/check_deployment | None for local contract |
| Production CSP and headers | VERIFIED | Per-request unpredictable nonce; no production unsafe-inline/unsafe-eval; dynamic pages; HSTS production; headers tests | Unit tests/production build | Browser runtime test blocked below |
| Product screens | VERIFIED | Overview, mandate, reviewed universe, discovery, jobs, independent reports/validation/CIO/Red Team, evidence/sources, signals/watch/rejected/expired, outcomes/performance, health/settings | Typecheck/build + component tests | Interactive responsive review blocked below |
| Evidence inspection | VERIFIED | Claim evidence-ID links to snapshot anchors, safe source URLs, escaped text, nested display bounds, raw-document suppression, separate quant/firm/audit classifications | Component tests | Provider source licensing still requires qualification |
| Honest availability | VERIFIED | Session-expiry handler, serialized bounded polling, safe gateway error, retry controls, explicit uncalibrated and integration-not-verified states | Unit tests | None for local contract |
| Measured operational health | VERIFIED | Authenticated system endpoint; bounded metrics whitelist; workspace job/token/cost totals; explicitly shared provider observations; unknown usage/cost, stale observations and qualification separated | Component/security tests | Browser runtime test blocked below |
| Web dependency install | VERIFIED | `npm_config_fetch_retries=0 npm_config_fetch_timeout=5000 npm ci --prefix apps/web` succeeded with 384 packages; offline installation also verified | Local sandbox | Install did not establish a fresh advisory scan; ESLint 9.39.4 emitted an unsupported-version deprecation warning |
| Lint / TypeScript / tests / build | VERIFIED | `npm run lint`, `typecheck`, `test`, `build` — 53 tests across 5 files passing, dynamic Next.js production build successful; deployment checker passed afterwards | Local sandbox | None |
| HTTP E2E / browser E2E | BLOCKED_ENVIRONMENT | `npm run test:smoke` and `npm run test:browser` both stop at `listen EPERM 127.0.0.1` | Local sandbox | Local socket permission; CI has browser install and real cross-process smoke |

The browser smoke exercises real Next.js, API, a separate Python worker and a
temporary SQLite database using explicitly synthetic DEMO.L. It asserts 202,
locked fixture reports, no signal, persisted packet retrieval, reload, mobile
horizontal layout, logout revocation, per-request CSP hydration nonce and no
browser console/CSP/hydration errors. It is implemented, **not locally verified**.
PostgreSQL concurrency verification remains in the backend CI job.

Private mode is explicit. Production/preview web settings are documented in
OPERATIONS.md; `MONEY_ENV` is required in production-built runtime requests. The
build itself requires no credentials. Universe displays previously researched
eligibility records and says so. Calibration has no approved record yet, so the
page exposes no invented probability. Provider/firm health shows "not verified by
this health check" rather than treating API uptime as qualification.

System Health also polls the authenticated `/research/system` control endpoint.
It displays durable job failure rate with sample counts, measured and reserved
tokens, unknown usage invocations, a known-cost subtotal with unknown-cost count,
research output counts, and bounded provider circuit/call/failure/latency records.
Provider records are explicitly shared compute-plane observations, not individual
workspace activity. Missing observations never become healthy state. A failed
refresh labels retained values as the last successful observation. Successful
calls and closed circuits never upgrade the separate qualification UNKNOWN label.

Graphify query before inspection: `graphify query "Next.js authentication session
research API proxy UI reports polling security headers" --budget 600` identified
auth.ts, backend.ts, workspace.tsx, contracts.ts and the security tests. No upstream
source was inspected or modified for this work. Parent performs the final Money
AST graph update after integrating all work streams.

CSP implementation follows [Next.js nonce guidance](https://nextjs.org/docs/app/guides/content-security-policy).
