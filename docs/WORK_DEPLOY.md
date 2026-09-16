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
