# Operating Money on an OCI container host

Money remains **PRODUCTION BLOCKED** until the live qualification and deployment
acceptance gates in VERIFICATION.md pass. This runbook does not provision a host
or deploy any changes.

## Processes and runtime limits

Build the image with `docker build --build-arg MONEY_GIT_SHA=<commit-sha> -t money:<release> .`.
The base image installs the committed Python lock without development dependencies
and runs as UID 10001. Run these as separate processes with the same database and
workspace configuration:

| Process | Command | Health |
| --- | --- | --- |
| Release migration | `alembic upgrade head` | Must complete successfully before rollout |
| Control API | `uvicorn money.api.app:app --host 0.0.0.0 --port 8000 --no-server-header` | `/health/live` for liveness; `/health/ready` for dependency readiness |
| Research worker | `python -m money.worker` | `python -m money.worker --healthcheck` |

Worker health checks report whether the workspace has a recent worker heartbeat;
they do not attest that each replica is healthy or that every research integration
is qualified. Readiness includes worker availability, so provision a worker before
requiring API readiness for public traffic. Keep an API liveness probe separate:
restarting an otherwise healthy API cannot repair a provider outage.

Compose provides non-root processes, read-only application filesystems, bounded
temporary filesystems, an init process, no privilege escalation and graceful
SIGTERM windows. API memory starts at 1 GiB and worker memory at 4 GiB. These are
initial limits, not measured capacity guarantees; benchmark qualified native models
before increasing concurrency. One worker executes one research job at a time.
Set host CPU/process limits and deny outbound destinations outside qualified
providers and approved model services. Do not mount a privileged Docker socket
into the public API or the Netlify app.

The default compute image does not bundle read-only upstream checkout trees or
unlocked native packages. TradingAgents, AI-HF and Qlib require a separately built,
qualified runtime containing their pinned SHA artifacts and transitive dependency
lock. An OCI LEAN runner requires a digest-pinned image, the expected upstream SHA
label, Python.NET support and the fixed Money algorithm. Native runtime packaging
and resource qualification must be completed before enabling live production.

## Environment and secret separation

The API/worker require `DATABASE_URL`, `RESEARCH_API_TOKEN` (32+ characters),
`MONEY_ENV=production`, `MONEY_AUTH_MODE=private` and `MONEY_WORKSPACE_ID` (default
`private`). Production accepts PostgreSQL only and rejects demo mode and unauthenticated
configuration. Production requires `MONEY_RESEARCH_MODE=live` and the pinned
qualification manifest described in LIVE_CONFIGURATION.md; `unconfigured` is
available only outside production and is not live qualification. Provider/model
credentials belong only in the compute environment.

Netlify requires `MONEY_ENV=production` (the configuration sets this),
`MONEY_AUTH_MODE=private`, HTTPS `RESEARCH_API_URL`, the independent server-side
service token, `MONEY_WEB_PASSWORD` (16+ characters) and independent `SESSION_SECRET`
(32+ characters). None may use a `NEXT_PUBLIC_` prefix. Deploy Previews have
`MONEY_ENV=preview` and must point to separate backend/database/secrets. Do not
duplicate the existing Netlify project or reinitialize its GitHub connection.

Browser sessions expire after eight hours. Each has a random nonce and signed
expiry; only its hash is stored in PostgreSQL. Logout revokes that record. Password
or session-secret rotation invalidates previous sessions. Login limits are also
PostgreSQL-backed. Security-service outages fail authentication closed.

Database configuration: `MONEY_DB_POOL_SIZE=5`,
`MONEY_DB_POOL_TIMEOUT_SECONDS=10`, `MONEY_DB_STATEMENT_TIMEOUT_MS=15000`.
The connection pool permits five overflow connections per process. Size PostgreSQL
for `(pool size + 5) × simultaneous processes`, including worker child/heartbeat
processes and rolling-deployment overlap, plus an administrative reserve. Use TLS
with server verification through the database URL and host-managed certificates.

Job defaults: `MONEY_JOB_TIMEOUT_SECONDS=1800`, `MONEY_JOB_MAX_ATTEMPTS=3`,
`MONEY_WORKER_LEASE_SECONDS=120`. Provider/native deadlines must be shorter than
the total job deadline. Leases and fencing prevent a superseded worker from
publishing. Configure container termination grace periods of at least 45 seconds
for workers so their supervisor can terminate child processes safely.

API body receive uses `MONEY_REQUEST_TIMEOUT_SECONDS=10` and
`MONEY_REQUEST_MAX_BYTES=16384`; both total receive time and actual streamed bytes
are bounded before parsing or enqueue. Also configure host/proxy TLS handshake,
header, connection-count and idle timeouts; application middleware begins only
after HTTP headers have been parsed.

Authenticated `/research/system` exposes workspace-scoped job/token/cost/signal
aggregates and bounded shared-provider circuit telemetry. Unknown usage/costs and
provider qualification remain explicitly unknown. Public health is a smaller
readiness projection, not a claim of production research qualification.

## Migrations, backups and deployment ordering

1. Record the running image digest, Git SHA and `alembic current` revision. Verify
   recent backup and successful restore-drill evidence before changing production.
2. Export a consistent encrypted backup using the PostgreSQL service's native
   snapshot/PITR facilities or `pg_dump --format=custom --file=money-backup.dump`.
   Configure the destination, database connection and credentials through the
   host's secret mechanism; never place credentials in command logs or Git.
3. Run `alembic upgrade head` once using a dedicated migration process. PostgreSQL
   migrations additionally take a transaction-scoped advisory lock keyed by the
   database/schema, with 10-second connect, 30-second lock and 300-second statement
   deadlines. Rollback/disconnection releases the lock. Still serialize releases
   at the host; migrations are not API-replica startup work. Validate `alembic
   current` before starting the new processes.
4. Start compatible workers and API replicas, verify readiness, and run a scoped
   enqueue/retrieve smoke. Preserve the immutable packets and existing job rows.
5. Let GitHub drive the existing Netlify production/Deploy Preview builds. Verify
   the preview against isolated services before accepting production traffic.

Use backwards-compatible staged schema changes. Keep the preceding application
image available; roll it back only if it understands the installed schema. Never
automatically run `alembic downgrade` or delete PostgreSQL volumes to solve a
deployment problem. A schema rollback that discards data needs a reviewed recovery
plan and explicit authorization.

Restore drills use an isolated, empty database and least-privileged credentials.
Restore the encrypted backup with `pg_restore --no-owner --exit-on-error --dbname=<isolated-database> money-backup.dump`,
validate its schema revision, verify report/packet hashes and read several historic
jobs, then exercise the worker on separate synthetic test records. Test PITR to a
known timestamp if the host provides WAL archives. Set recovery objectives,
backup cadence/retention and a restore-drill owner before production; the repository
does not supply managed backups or attest an unrun restore.

Run `uv run python scripts/backend_acceptance.py` for a disposable local drill.
It accepts no existing database URL, strips inherited provider/database secrets,
creates its own Unix-socket cluster, runs three concurrent migration processes,
interrupts a real worker after one sealed report, verifies lease/fence recovery,
backs up, restarts PostgreSQL and restores into a separate empty database. It
checks immutable packet/report hashes and session state. It removes only its own
temporary data after confirmed shutdown; an uncertain shutdown retains it.
The synthetic research is never live qualification. The present local run is
blocked at `initdb` by shared-memory permission denial; no restore success is
claimed. CI includes this separate drill in addition to PostgreSQL 17 tests.

Lock semantics: [PostgreSQL advisory locks](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS).

## Monitoring and incidents

Collect structured stdout/stderr with request ID, research/job ID, worker, stage,
retry and duration. Do not log passwords, session cookies, provider keys, raw
provider documents or database URLs. Alert on readiness failures, stale worker
heartbeats, queue age, exhausted retries, provider circuit state, token limits and
unexpected positive signal publication. Compare `version`, `git_sha`,
`schema_revision` and environment in authenticated system information with the
release record. Rate-limit, session and research data remain durable across
Netlify/API restarts.

Dependency audit CI uses npm audit and a pinned pip-audit tool against exported
locked requirements. Registry/advisory outages are failed checks, not clean
security results. Upstream SHA upgrades remain a separate explicit process.

Sources checked during implementation: [Next.js nonce CSP](https://nextjs.org/docs/app/guides/content-security-policy),
[Playwright CI](https://playwright.dev/docs/ci-intro),
[Netlify file configuration](https://docs.netlify.com/build/configure-builds/file-based-configuration/),
[pip-audit usage](https://github.com/pypa/pip-audit).
