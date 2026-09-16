# Deployment

Deploy `apps/web` from GitHub to Netlify, production branch `main`, with pull-request Deploy Previews. Run the Python API and separate worker from the Money container image against durable PostgreSQL. Run migrations once as a release step before starting processes. Do not use Netlify local files for state or run research in request handlers.

Required environment and commands are detailed in NETLIFY.md and LOCAL_DEVELOPMENT.md. API and web credentials must be separate from provider credentials. Restrict backend network access where possible. Deploy previews use a separate database/API and credentials. Upstreams are pinned by UPSTREAM_LOCK.txt and are not bundled accidentally into the lightweight foundation image; live firm runtimes require a separately qualified image.

Use [OPERATIONS.md](OPERATIONS.md) for migration/API/worker commands, graceful
shutdown, resource limits, pool sizing, backups, restore drills and rollout/rollback.
[LIVE_CONFIGURATION.md](LIVE_CONFIGURATION.md) describes the typed qualification
manifest and actual live-runtime constraints. Existing Netlify initialization and
GitHub connection must not be repeated.

## Release acceptance

- Python tests, Ruff, mypy and web lint/typecheck/tests/production build pass.
- Alembic migrations apply on PostgreSQL; a completed job survives service restart.
- Container builds and health endpoints report API/database/worker state.
- Netlify detects the app, preview build succeeds and browser can retrieve a finished packet.
- Authentication, environment separation and no client secrets are verified.
- Live data coverage, eligibility and all required firm/validation/audit runners are configured and qualified before publishing live signals.
- GitHub branch protection and passing remote CI are verified by the repository owner during connection.

This repository supplies configuration, not cloud accounts or evidence of a completed remote deployment. Implementation verification and remaining blockers are recorded with the delivery.

See [local verification evidence](VERIFICATION.md) for passed checks and sandbox-blocked checks. CI includes real PostgreSQL queue semantics, a web/API/worker HTTP smoke, and Docker construction; these must pass remotely before release.
