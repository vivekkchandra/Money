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
- GitHub CI, existing Netlify production/Deploy Preview, backup restore drill,
  TLS and monitoring acceptance. Nothing has been pushed, deployed or provisioned.

## NOT STARTED / remaining engineering

- Broad automated ISA-universe refresh and authoritative filing-document download
  to streaming XBRL ingestion; current live assembly uses reviewed instruments and
  supplemental facts plus a real Companies House filing index.
- Qualified rights/spin-off/delisting adjustments and adjusted outcome
  reconciliation; uncertain/action-bearing datasets fail closed.
- Persistent scheduled provider/outcome/calibration refresh and component-attributed
  empirical calibration. Strong research candidate state stays disabled.
- Native firm challenge-response capabilities wired to independent verifiers;
  missing capabilities currently leave auditable unresolved disagreement.
- General provider-response cache and richer adaptive quant-only shortlist policy.
- Optional external email/Telegram transports and multi-user identity onboarding.
  Initial product is private; repository ownership predicates are tested.

None of these remaining items is represented as completed live functionality.
