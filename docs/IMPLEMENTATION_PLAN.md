# Commercial implementation plan — 2026-09-16

Status: **PRODUCTION BLOCKED**. The commercial work begins from `fc5ddeb` on
`main`. Older production-only notes below are historical, not current acceptance.

## DONE — commercial implementation, locally exercised

- Release follow-up: production/preview reject all synthetic flags; API/worker/read
  barriers defend against stale configuration. Plans alias and public commercial
  routes are included in read-only deployment acceptance.
- Durable reviewed offboarding/ownership succession and eventual mutable-PII
  fulfilment, with forward-only migration 0006 and immutable research preserved.
- Immutable fenced per-call metering (0007), safe native-child receipt transport,
  separate actual/estimated/unknown costs and content-free admin aggregation.
- Separate Docker control-plane/research dependency sets without changing locked
  versions; Chroma capability restrictions in research subprocesses. Separate Node,
  control-plane and research audits remain enforced; research advisories unresolved.

- Git-owned schema/hash/provenance-validated plans, exclusions and provider rights;
  startup/CI/wheel loading checks. No customer state or secrets moved into Git.
- SaaS signup, verification, reset, sessions/rotation/revocation, workspaces,
  invitations, seats and OWNER/ADMIN/MEMBER/VIEWER authorization; additive frozen
  migrations 0004/0005 preserve durable research and append-only audit records.
- Server-owned subscriptions, atomic request/token/worker-time allowances, usage
  attribution, and idempotent enqueue in the existing durable queue.
- Stripe checkout/portal adapter and authenticated durable webhook reconciliation;
  persisted retry parameters, customer identity verification, per-workspace locking,
  bounded retries and fail-closed unreconciled billing. Transport tests are not a
  real payment or subscription lifecycle qualification.
- Encrypted asynchronous SMTP outbox, account/reset/invitation messages and opted-in
  research/subscription notifications; separate commercial and email workers.
- Marketing/legal-draft pages, account/onboarding/billing/team screens, customer
  dashboard, bounded history/search/date/sort, watchlist, preferences and notifications.
- Internal allowlisted support diagnostics, bounded privacy-conscious product/error
  event hooks, sensitive-operation audit, account export and deletion-request flow.
- Commercial HTTP/browser E2E automation wired into existing CI, data-table CI,
  OCI process/environment contract and preserved Netlify SSR artifact guard.

## IN PROGRESS — release acceptance

No live customer onboarding or research completion has been demonstrated. Local
SQLite TestClient + separate-worker acceptance passes; actual PostgreSQL and real
browser tests remain blocked by this shell's process/network permissions. Netlify
status/build/public HTTP remain unreachable. Current totals and failures are in
VERIFICATION.md; neither generated documentation nor mocks qualify a deployment.

## CREDENTIAL / LICENCE BLOCKED

- Approved SMTP sender credentials and real email delivery acceptance.
- Stripe account, reviewed products/prices, signing secret, configured portal and
  real checkout/renewal/failure/cancel/reconciliation acceptance.
- Commercial provider rights, actual current ISA source, UK evidence credentials
  and approved inference credentials/budgets. Unknown redistribution rights deny
  exposure, including previously persisted non-demo records.

## EXTERNAL INFRASTRUCTURE BLOCKED

- Authorized OCI API/worker host and TLS PostgreSQL; no guessed account provisioning.
- Allowed Docker, PostgreSQL shared memory/listeners and browser execution for CI
  migration/concurrency/restart/restore/E2E acceptance.
- Network-enabled GitHub/Netlify publication and actual root/auth/assets/header checks.
- Configured production monitoring/alert destination, automated backup retention/PITR
  and a successful isolated restore drill.

## REMAINING ENGINEERING / REVIEW

- Resolve four distinct locked ChromaDB advisories through CrewAI with a reviewed
  runtime/dependency change; no fixed version established and no audit suppression.
- Complete native/data/model commercial qualification; broader corporate-action and
  scheduled outcome/calibration engineering below remains incomplete.
- Execute offboarding against real Stripe/PostgreSQL, approve retention exceptions
  and schedule reviewed fulfilment; the implemented CLI cannot invent legal approval.
- Qualify provider subcall/invoice accounting where the provider exposes receipts;
  current dataset-operation counts do not masquerade as individual HTTP calls and
  unknown actual financial cost remains unknown.
- Complete hosted monitoring, backup and paid lifecycle drills; release requires the
  public end-to-end new-customer journey, not only available hooks and contracts.

## Historical production-only plan (before commercial conversion)

Status: **PRODUCTION BLOCKED**. The existing foundation was preserved. The initial
gap matrix is in PRODUCTION_GAPS.md; observed acceptance evidence is in
VERIFICATION.md. A tested adapter is not a passed live integration.

## DONE — implemented and locally exercised

- Explicit environment/security settings; PostgreSQL-owned private workspace
  sessions, rotation/logout, CSRF, durable login/enqueue limits and ownership checks.
- Atomic queue claims, leases/heartbeats/fences, classified retries/backoff,
  deadlines, final failure, idempotency, staged checkpoint recovery and audit logs.
- Qualified-provider/identifier contracts; bounded public HTTPS for Trading 212
  metadata, Companies House filing index, EODHD raw OHLCV/news/actions; streaming
  XBRL seam; archive/PIT/source-conflict/GBP/GBX/liquidity controls.
- Explicit reviewed Companies House document selections now fetch machine-readable
  accounts into live snapshot facts and immutable safe provenance. Content-bound
  GBP units proof, exact redirect-host review and qualification artifacts are
  mandatory; no raw redistribution or historical publication inference.
- TA-Lib indicators, versioned catalyst/financial-change discovery, qualified Qlib
  numerical discovery, union/deduplication and per-firm snapshot-only inputs.
- Native TradingAgents research/risk factories, AI-HF persona lifecycle, Qlib
  inference, source fingerprints and bounded isolated process execution.
- Real LEAN OCI runner: fixed order-free study, reproducible dataset/config,
  chronological held-out cases, sensitivities, costs, regimes and timeout limits.
- Real CrewAI Flow/tasks, conditional auditors, independent numeric recomputation,
  Red Team and immutable post-lock challenges capped at two rounds.
- Evidence-based veto consensus, deterministic illustrative signal construction,
  read-time expiry and evidence-backed invalidation sidecars.
- Durable token admission/usage/recovery, provider circuits, manual model registry,
  immutable replay, research-reference outcomes and informational web alerts.
- Offline interpretable ridge baseline training with purged/embargoed walk-forward
  and untouched OOS evaluation. Artifacts remain UNPROMOTED/PIT-unqualified until
  independent review; no automatic registry activation exists.
- LEAN scenario-policy hash binds the actual entry/ATR/target/invalidation and
  calendar-day horizon to signal generation; mismatch suppresses publication.
- Responsive Next.js screens with separate organisations/provenance, polling,
  unavailable/empty/loading/error/expiry states, nonce CSP and accessible controls.
- Additive migrations, structured health/metrics/logs, hardened base container,
  PostgreSQL/browser/dependency CI checks and backup/restore/rollout runbooks.

## IN PROGRESS — release acceptance

Current deployment audit begins from pushed `acb38f1`, not the historical starting
foundation. Local project ID matches; the reported Netlify generic 404 remains
unresolved because CLI/public HTTP are unreachable here. The subsequently supplied
production log proves correct paths and successful Next compilation, but no Next
adapter lifecycle and zero uploaded functions. An explicit locked adapter and
post-build artifact guard address that packaging failure locally; no repaired
production deploy has been verified. Connected GitHub reads
prove CI run `35106931767` failed in pytest, web HTTP smoke and Python dependency
audit; its Next build and Docker build passed. No Netlify commit status exists on
that SHA. Missing status is not proof of a missing webhook. The supplied deploy
ID is `6aaaa3c8a1d24e0009cc631b`; its built commit SHA is not shown.

Implemented in this follow-up: intended route aliases and HTTP deployment checker;
CI bounded failure summaries and read-only live-site checks; isolated PostgreSQL
recovery/backup/restore drill and migration advisory locking; native independent
challenge responses/verifiers with immutable per-call recovery and token charging;
inspectable cross-examination UI and bounded smoke-process shutdown; reviewed
filing-document-to-snapshot assembly with adversarial security/qualification tests.
These changes cannot yet be published: shell DNS/Git filesystem restrictions and
the GitHub write tool's unavailable approval gate block commit/push/deploy.

Local implementation checks and the Money-only AST graph refresh are recorded in
VERIFICATION.md. The production acceptance gate is still open for the specific
external blockers and remaining engineering below; this is not a release approval.

## CREDENTIAL BLOCKED

- EODHD key and UK entitlement/licence review; Companies House API key; selected
  inference endpoint/provider/exact model credentials and approved budgets.
- Optional metadata-only Trading 212 key/secret only where required. Its metadata
  endpoint does **not** establish Stocks & Shares ISA eligibility.
- Paid TradingAgents, AI-HF and CrewAI qualification with genuine evidence. Opt-in
  tests report missing inputs; ordinary CI performs no paid calls.

## EXTERNAL INFRASTRUCTURE / DATA BLOCKED

- Real PostgreSQL concurrency/migrations/restarts, accessible Docker engine,
  allowed localhost/browser smoke and package/advisory network access.
- Pinned TradingAgents/AI-HF/Qlib packages and transitive runtime dependency lock;
  installed CrewAI source differs from its pinned checkout and is rejected live.
- Digest-pinned LEAN image and host egress/resource qualification.
- Reviewed current ISA eligibility, business classifications, corporate-action
  coverage, identifier/raw-unit mapping, spread evidence and source licensing.
- Original-publication UK market/fundamental archives, historical eligibility and
  survivorship proof; independent Qlib OOS review and manual promotion.
- GitHub CI failures require full logs and regression rerun; existing Netlify
  production/Deploy Preview needs actual deploy logs, published SHA and live HTTP
  acceptance. Backup restore, TLS and host monitoring remain unverified. No new
  commit, push, deployment or cloud provisioning occurred in this follow-up.

## NOT STARTED / remaining engineering

- Broad automated ISA-universe refresh and automatic filing selection/accounting
  units qualification; current live assembly uses reviewed instruments, optional
  exact document selections and supplemental facts. Selected document retrieval
  is implemented, but PDF/OCR and historical publication reconstruction are not.
- Qualified rights/spin-off/delisting adjustments and adjusted outcome
  reconciliation; uncertain/action-bearing datasets fail closed.
- Persistent scheduled provider/outcome/calibration refresh and component-attributed
  empirical calibration. Strong research candidate state stays disabled.
- Native correspondence paid-runtime qualification and any independently reviewed
  effective-audit policy for resolving earlier soft findings. Current original
  audit vetoes remain conservative even when later correspondence adds evidence.
- General provider-response cache and richer adaptive quant-only shortlist policy.
- Optional external email/Telegram transports and multi-user identity onboarding.
  Initial product is private; repository ownership predicates are tested.

None of these remaining items is represented as completed live functionality.
