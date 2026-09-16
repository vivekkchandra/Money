# Production implementation plan — 2026-09-16

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
