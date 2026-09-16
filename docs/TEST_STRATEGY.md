# Test strategy

Unit tests exercise mandate limits, currency normalization, timestamp/PIT rejection, evidence hashes, discovery union, opinion-free inputs, independence overlap, deterministic vetoes and expiration boundaries.

Integration tests exercise migration, enqueue/HTTP 202, durable reload across API/store instances, worker claims and fencing, immutable reports/packets, barrier access before/after the complete set, firm failure, and finished packet/evidence retrieval. PostgreSQL CI tests must cover production queue semantics; SQLite tests cannot establish SKIP LOCKED correctness.

Web checks include lint, TypeScript, auth/secret separation/status/expiry tests and
a production build without research credentials. Deployment checks verify Netlify
boundaries, forbidden broker capabilities, Compose separation and image settings.
Ordinary tests need no provider credentials. Separate `tests/production` opt-in
checks make genuine calls only with explicit authorization/configuration and
report missing credentials/external runtimes as skips, never successful evidence.

Before production: qualify pinned upstream executions and real provider coverage, run a real Postgres end-to-end request/worker/read, validate Netlify Deploy Preview, test authentication and migration backup/recovery, then enable main-branch production deploys. Local success does not claim remote CI/deployment success.

Adversarial coverage includes native tool/file/provider escape, malicious evidence,
SSRF/redirect/DNS/decompression/archive boundaries, strict inference/usage JSON,
session revocation, CSRF, ownership, concurrent rate/token admission, stale fences,
checkpoint manifest drift, forged packet/target/challenge hashes and provider
circuit races. A scripted real native lifecycle remains a test—not a paid live run.

CI uses actual PostgreSQL 17 for queue/migrations and parameterized token recovery.
Browser smoke covers login → 202 → distinct worker → sealed packet → refresh/read.
Local listener/Docker/PostgreSQL restrictions are recorded in VERIFICATION.md.
