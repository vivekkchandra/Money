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
| Lint / TypeScript / tests / build | VERIFIED | Initial 53-test baseline passed; deployment-routing follow-up expands this to 68 tests across 6 files. Exact latest command results below | Local sandbox | None for completed commands |
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

## Deployment-routing follow-up

The clean pre-change baseline passed `npm ci`, lint, typecheck, 53 tests and the
production build. Inspection of `.next/routes-manifest.json` confirmed that
`/[[...view]]` matches `/` and nested paths. The server app-path manifest includes
the SSR page and all API handlers. There is no requirement for an exported
`index.html`: [Next.js optional catch-all routes](https://nextjs.org/docs/app/api-reference/file-conventions/dynamic-routes)
include the base path, and [Netlify's Next.js adapter](https://docs.netlify.com/build/frameworks/framework-setup-guides/nextjs/overview/)
provides SSR and API runtime functions. These facts do **not** establish the cause
of a generic 404 on a remote deployment whose deployed artifacts/logs are unavailable.

Concrete application gaps found and fixed: `/dashboard` now opens the existing
overview, bare `/research` opens research jobs, and `/system` opens System Health.
Research detail IDs and `/jobs` / `/health` remain unchanged. Authentication and
dynamic rendering are preserved. No static export, catch-all SPA rewrite, fake
index page, new Netlify project or Netlify configuration change was introduced.

Read-only acceptance command for the deployment owner:

```sh
npm run check:deployment --prefix apps/web -- https://EXISTING-SITE.netlify.app --production
```

Omit `--production` for preview/local checks, where HSTS is not required by Money.
The checker never authenticates, creates research or modifies the target. It
verifies `/`, `/dashboard`, `/research`, `/system`, Money-specific screen markers,
referenced same-origin JavaScript/CSS, HTTP/MIME correctness, security headers,
per-request hydration nonces, session JSON and fail-closed unauthenticated control
routes. It detects generic host 404 content and static assets incorrectly served
as HTML. Bodies, redirects, asset count and request deadlines are bounded.
Passing this check establishes only web delivery, not backend or research readiness.

The existing cross-process smoke now runs these HTTP checks before login and after
stopping its synthetic-test backend, checking that the web survives the outage.
It includes safe stage labels and redacts generated credentials from diagnostics.
Local execution was attempted again and remains blocked at
`listen EPERM 127.0.0.1` before any service starts. The remote CI smoke failure
reported by the coordinating agent cannot be diagnosed from this local permission
error; its failure trace remains required. No HTTP/browser success is claimed.

Regression tests cover supported route shells without a backend, alias/detail
preservation, missing backend configuration, generic host 404, wrong catch-all
screen, missing assets, HTML fallback assets, missing headers, unauthenticated
data exposure, redirects, response bounds and credential-bearing origins.

Final local results after the routing/checker changes: `npm run lint`,
`npm run typecheck`, `npm test` (68 passed / 6 files), `npm run build` and
`python scripts/check_deployment.py` all passed. The clean `npm ci` at the start
also passed (384 installed packages); no dependency version was changed. The
existing ESLint unsupported-version warning remains recorded, not concealed.
