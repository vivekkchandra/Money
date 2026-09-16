# Test strategy

Unit tests exercise mandate limits, currency normalization, timestamp/PIT rejection, evidence hashes, discovery union, opinion-free inputs, independence overlap, deterministic vetoes and expiration boundaries.

Integration tests exercise migration, enqueue/HTTP 202, durable reload across API/store instances, worker claims and fencing, immutable reports/packets, barrier access before/after the complete set, firm failure, and finished packet/evidence retrieval. PostgreSQL CI tests must cover production queue semantics; SQLite tests cannot establish SKIP LOCKED correctness.

Web checks include lint, TypeScript, focused tests for auth/secret separation/status/expiry and a production build with no research credentials. Deployment checks verify Netlify paths, lightweight routes, Compose process separation, migrations and Docker construction. No test makes live investment decisions or needs provider credentials.

Before production: qualify pinned upstream executions and real provider coverage, run a real Postgres end-to-end request/worker/read, validate Netlify Deploy Preview, test authentication and migration backup/recovery, then enable main-branch production deploys. Local success does not claim remote CI/deployment success.
