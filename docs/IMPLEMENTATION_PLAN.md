# Implementation plan

Implement a deployable foundation vertically before expanding upstream execution.

1. Define frozen Money contracts: restricted mandate, provenance/PIT evidence, snapshot, firm reports, job states, validation/audit, packet and expiring signals.
2. Enforce eligibility and ethical/data gates; union discovery provenance; give each first-pass adapter only mandate and snapshot. Persist each report exactly once and atomically lock the complete set.
3. Add PostgreSQL migrations, durable queue with worker ownership/leases, authenticated API, immutable packets and restart tests.
4. Connect a Next.js research workspace to enqueue/poll/read endpoints; expose reports individually, evidence classifications, audit, expiry and system health.
5. Provide explicit development fixture runtime and fail-closed production runtime. Record upstream Graphify discoveries and qualify each live runner separately.
6. Add CI, container/Compose, Netlify configuration, production environment documentation and deployment checks.

## Subsequent qualification gates

TradingAgents and AI-HF must receive Money evidence through restricted tools, without their default live data/portfolio paths. Qlib requires approved trained artefacts and PIT-safe features. LEAN requires an isolated runner and reproducible cost/stress/walk-forward results. CrewAI must use independently scoped verifier tools and structured findings. UK providers require coverage and source licensing/freshness rules. Outcome evaluation and offline factor promotion require separately validated market data. None of these gates can be represented by a fabricated successful report.

The 22 product phases remain the roadmap. Foundation contracts and deployment seams do not by themselves mean every live integration or production acceptance criterion is complete. `docs/DEPLOYMENT.md` records the release checklist and concrete verification.

## Delivered foundation

Contracts, deterministic eligibility/quality gates, provider coverage declarations, discovery union, persisted first-pass isolation, native-runner adapter boundaries, packet integrity, expiration, deterministic outcome calculations, and offline model-promotion assessment are implemented with focused tests. The Next.js control plane, authenticated API, separately running worker, PostgreSQL schema/migrations, local demonstration, CI and deployment files form one coherent vertical slice.

## Remaining production work

- Verified Trading 212 ISA universe refresh and licensed UK evidence ingestion, including qualified XBRL conversion and production provider coverage.
- Full native snapshot-only TradingAgents/AI-HF assembly; approved Qlib models/features; TA-Lib and Qlib discovery scanners; isolated real LEAN runner with execution deadlines and validation coverage.
- CrewAI task assembly with independent verification tools, conditional specialists, Red Team investigation and controlled cross-examination. The demonstration audit is deterministic and explicitly does not run CrewAI.
- Calibrated confidence/reliability, live signal construction, scheduled outcome ingestion/storage and component-performance attribution. Outcome calculators and QuantStats boundaries are implemented; a production evaluator is not yet scheduled.
- Multi-user identity/tenant isolation if required beyond the initial single-user workspace; durable edge login rate limiting, operational backups and live deployment qualification.

No unimplemented integration is substituted by a fabricated production result.
