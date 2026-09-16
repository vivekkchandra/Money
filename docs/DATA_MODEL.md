# Durable data model

PostgreSQL is authoritative. SQLAlchemy repositories and Alembic migrations own the schema; SQLite is a local-test substitute only.

Research jobs identify a ticker, immutable mandate version, snapshot and lifecycle timestamps. Queue state is committed before HTTP 202. Worker claims carry an ownership token and deadline; stale work cannot overwrite a newer owner. Stage changes and failures append audit events.

Snapshots freeze instrument/eligibility metadata and typed objective evidence with hashes and temporal cutoffs. Evidence records retain source, provider, source identifier, observation/publication/retrieval times, PIT status and freshness. Repeated articles retain a canonical source identity across providers.

First-pass reports have a unique `(job_id, firm)` key and immutable content hash. The barrier requires exactly TradingAgents, AI-HF and Qlib against the same snapshot. Read access to the combined content requires the durable lock, not merely three in-memory return values.

Decision packets contain all reports and provenance, quality gates, validation, audit/red team, independence, versions, costs and resulting research state. Packets are insert-only. Signals expire by time on every read; an expiry sweep is housekeeping, not the authority for validity. Research outcomes are separate from any later manually entered actual trades.

Migrations `0002_security_recovery` and `0003_production_services` add workspaces,
durable sessions, idempotency/retry scheduling, budget reservations, model
registry/promotion events, provider circuits, alert outbox and replay runs. Resource
lookups enforce workspace ownership. Identity remains private; this does not claim
completed multi-user onboarding or database row-level security.

Cross-examination, signal design, manifest provenance and CIO runtime usage are
insert-only artifacts. Later signal invalidation is an evidence-linked audit event,
never an edit to its packet. Token/circuit/session rows are mutable operational
state, not immutable research evidence. Performance aggregates and a durable
maintenance scheduler remain future work.
