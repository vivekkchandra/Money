# Data flow

1. Authenticated web request validates ticker/mandate and forwards bounded JSON to the research API.
2. API commits mandate and QUEUED job to PostgreSQL and returns HTTP 202 with `id`; no research runs inside that request.
3. A separate worker claims durable work. Eligibility fails closed on unknown/stale live broker membership, prohibited activities or foreign currency.
4. Technical, quantitative, catalyst and fundamental discovery form a union with per-channel provenance. Data providers declare coverage. Money freezes eligible objective evidence into one snapshot.
5. TradingAgents, AI-HF and Qlib independently consume identical facts; store locks all first-pass reports before downstream access.
6. LEAN attempts falsification. CIO audits, independently verifies and activates relevant specialists; Red Team and bounded cross-examination feed evidence-based gates.
7. Store publishes immutable packet and, only when eligible, a time-limited research signal. Browser polls durable status and reads independent reports and evidence.
8. Offline/scheduled workers evaluate outcomes and expiration. Netlify scheduled endpoints, if added, only enqueue.

Fixture mode is an explicit local demonstration. Its synthetic sources and runtime labels survive into the packet and UI; it cannot be enabled in production.
