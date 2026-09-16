# Durable data model

PostgreSQL is authoritative. SQLAlchemy repositories and Alembic migrations own the schema; SQLite is a local-test substitute only.

Research jobs identify a ticker, immutable mandate version, snapshot and lifecycle timestamps. Queue state is committed before HTTP 202. Worker claims carry an ownership token and deadline; stale work cannot overwrite a newer owner. Stage changes and failures append audit events.

Snapshots freeze instrument/eligibility metadata and typed objective evidence with hashes and temporal cutoffs. Evidence records retain source, provider, source identifier, observation/publication/retrieval times, PIT status and freshness. Repeated articles retain a canonical source identity across providers.

First-pass reports have a unique `(job_id, firm)` key and immutable content hash. The barrier requires exactly TradingAgents, AI-HF and Qlib against the same snapshot. Read access to the combined content requires the durable lock, not merely three in-memory return values.

Decision packets contain all reports and provenance, quality gates, validation, audit/red team, independence, versions, costs and resulting research state. Packets are insert-only. Signals expire by time on every read; an expiry sweep is housekeeping, not the authority for validity. Research outcomes are separate from any later manually entered actual trades.

The initial workspace is single-user. Multi-user profiles/row-level ownership, full provider-call metrics and component-performance aggregates require additional migrations before offering multi-user tenancy.
