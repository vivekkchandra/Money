# Deployment work evidence — 2026-09-16

| Requirement | Status | Evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Netlify control-plane boundary | VERIFIED | Existing base/publish/build preserved; explicit production/preview MONEY_ENV; checker rejects compute modules/process execution/client credentials | Local static checks | Actual Netlify build below |
| Forbidden brokerage capabilities | VERIFIED | Python AST checks imports, definitions, calls, aliases and dynamic attribute literals; eleven adversarial deployment tests pass | Local pytest | Static checks complement runtime isolation; not a proof against arbitrary malicious obfuscation |
| Web browser CI | VERIFIED | Existing workflow now installs pinned Playwright Chromium and executes real API/worker/browser smoke | Configuration inspection | Remote GitHub Actions not executed here |
| Dependency audit configuration | VERIFIED | npm production audit and pip-audit 2.10.1 against uv locked export; no automatic fixes/upgrades | Configuration inspection | Advisory checks below |
| Container hardening | VERIFIED | Locked non-dev non-editable install, UID 10001, SIGTERM, liveness; Compose init/read-only/tmpfs/memory/no-new-privileges/readiness/worker health | Static checker | Image build below |
| Compose configuration | VERIFIED | `POSTGRES_PASSWORD=... RESEARCH_API_TOKEN=... docker compose config --quiet` exit 0 with ephemeral nonproduction values | Local Docker CLI | None |
| Docker build | BLOCKED_ENVIRONMENT | `docker build -t money-production-check .` failed to connect to Docker socket: permission denied | Local sandbox | Accessible Docker daemon |
| Netlify CLI build | BLOCKED_ENVIRONMENT | `npx --yes netlify-cli@latest build` failed registry.npmjs.org DNS (ENOTFOUND), bounded retries/timeouts | Local sandbox | Registry/network access and existing linked Netlify context |
| npm vulnerability audit | BLOCKED_ENVIRONMENT | `npm audit --prefix apps/web --omit=dev --audit-level=high` failed advisory endpoint DNS | Local sandbox | Advisory network access |
| Python vulnerability audit | BLOCKED_ENVIRONMENT | Locked requirements export succeeded; pip-audit install failed pypi.org DNS after redirecting cache/tools into writable temporary directories | Local sandbox | Package/advisory network access |
| Backup/restore/rollout/pooling guidance | VERIFIED | OPERATIONS.md with isolated restore procedure, release ordering, rollback limitations and resource guidance | Documentation | Actual backups, restore drill and host policies are external |

No deployment, push, Netlify initialization, upstream edit or credential commit
was performed. Native runner dependency/image packaging remains a qualification
blocker; the base image does not silently claim to contain pinned upstream firms.

Graphify scoped deployment query used a 600-token budget before source inspection.

## Isolated backend acceptance drill

`python scripts/backend_acceptance.py` creates its own private temporary PostgreSQL
cluster and Unix socket. It does not accept an existing database URL or a target
data directory, strips inherited database/cloud/provider credentials, and never
connects to the configured deployment database. Native PostgreSQL executables
(`initdb`, `pg_ctl`, `createdb`, `pg_dump`, `pg_restore`) must be installed and on PATH.

The drill starts three concurrent migration processes against its fresh database
to test PostgreSQL advisory release locking, checks schema drift, runs PostgreSQL integration
and budget-recovery tests, enqueues synthetic `DEMO.L` through the authenticated
control API, kills a separate process after sealing one first-pass report, waits
for real lease expiry, proves the stale claim is fenced, and starts the normal
worker CLI to finish the same durable job without replacing that report. It then
creates a custom-format `pg_dump`, restarts PostgreSQL, restores into a separate
empty database, and compares packet/report hashes, durable sessions and database
immutability. API checks use the in-process ASGI transport; actual web/network and
Netlify acceptance remain separate checks.

Successful shutdown removes only the generated cluster/backup directory. If server
shutdown cannot be confirmed, the runner preserves the directory and reports its
exact location. No production backup or user data is removed. The runner emits a
bounded JSON result and exits `0` for verified checks, `1` for failure, or `2` for an
environment blocker. `--skip-tests` explicitly records the integration suite as
`NOT_RUN`; it cannot provide full release acceptance. Synthetic operational success
never claims qualified production research.

Latest local attempt: **BLOCKED_ENVIRONMENT** at `postgres_init` with
`ENVIRONMENT_PERMISSION_DENIED`; PostgreSQL 17.10 bootstrap was denied `shmget`
(shared-memory creation). Temporary generated data was removed. Docker build also
remains socket-permission blocked locally; Compose configuration passed again with
generated ephemeral values. Eight runner safety tests cover environment separation,
timeouts, diagnostic redaction, safe targets and retained data after uncertain
shutdown. These tests do not substitute for running PostgreSQL.

Suggested CI addition (repository owner wires workflow): expose the installed
PostgreSQL binary directory on PATH, then run
`uv run --no-sync python scripts/backend_acceptance.py` as an ordinary non-root
user. Always upload pytest JUnit output on failures so PostgreSQL-specific failures
remain inspectable even when Actions log retrieval is unavailable. Do not downgrade
a blocked drill into a successful release gate.

Command behavior was checked against official PostgreSQL documentation for
[initdb](https://www.postgresql.org/docs/current/app-initdb.html),
[pg_ctl](https://www.postgresql.org/docs/current/app-pg-ctl.html),
[pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html), and
[pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html).
