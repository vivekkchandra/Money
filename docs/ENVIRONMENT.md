# Server configuration

Examples in `.env.example` are placeholders only. Secrets are never Git assets.

| Process | Configuration |
| --- | --- |
| Netlify web | `MONEY_AUTH_MODE=saas`, `MONEY_ENV`, `MONEY_DEPLOYMENT_CONTEXT`, `RESEARCH_API_URL` (real HTTPS API), `RESEARCH_API_TOKEN` (server-only) |
| API/research worker | `DATABASE_URL` (PostgreSQL, TLS configured by host), `RESEARCH_API_TOKEN` (32+ random characters), `MONEY_AUTH_MODE=saas`, `MONEY_ENV`, `MONEY_PUBLIC_WEB_URL`, `MONEY_EMAIL_ENCRYPTION_KEY` (Fernet key), `MONEY_ACCOUNT_SESSION_HOURS` |
| Research | `MONEY_RESEARCH_MODE=unconfigured` or qualified `live`; `MONEY_ENABLE_SYNTHETIC_DEMO=false` required in production/preview (opt-in only in development/test); `MONEY_LIVE_MANIFEST` and `MONEY_LIVE_MANIFEST_SHA256` for live |
| Versioned assets | `MONEY_REFERENCE_DATA_DIR` absolute release data directory (`/app/data` in image); never a customer-writeable directory |
| Email worker | database/environment, `MONEY_EMAIL_ENCRYPTION_KEY`, `MONEY_SMTP_HOST`, `MONEY_SMTP_PORT` (465 default), `MONEY_SMTP_TLS` (`implicit` or `starttls`), `MONEY_SMTP_USERNAME`, `MONEY_SMTP_PASSWORD`, `MONEY_EMAIL_FROM` |
| Billing API/worker | `MONEY_BILLING_ENABLED`, `MONEY_STRIPE_SECRET_KEY`, `MONEY_STRIPE_WEBHOOK_SECRET`, `MONEY_STRIPE_PRICES` JSON plan→Price ID, `MONEY_STRIPE_API_VERSION`, `MONEY_STRIPE_LIVE`, trusted `MONEY_PUBLIC_WEB_URL` |
| Internal support | `MONEY_INTERNAL_ADMIN_USER_IDS` JSON array of actual user UUIDs; never a browser role claim |
| Cost controls | `MONEY_USER_JOBS_PER_MONTH` (default 300, range 1–10000) is an account-wide UTC calendar-month operational ceiling, in addition to Git-versioned workspace plan allowances; existing provider/daily/job token limits still apply |
| Release | `MONEY_VERSION`, `MONEY_GIT_SHA`; worker/pool/timeouts retain typed Settings defaults |
| Reviewed offboarding | `MONEY_RETENTION_APPROVED=false` until approval; `MONEY_RETENTION_POLICY_VERSION`, `MONEY_RETENTION_APPROVAL_REFERENCE`, explicit `MONEY_PII_RETENTION_DAYS`; no default legal duration. Operator review also attests billing and retained-record resolution per request |

The default Docker image excludes native CrewAI/ChromaDB. Research workers must use
the explicit `research` build target (`uv sync --locked --no-dev --extra research`).
Do not use that image to bypass the unresolved research dependency security gate.

SaaS does not use the private shared password. Keep legacy `MONEY_WEB_PASSWORD`
and `SESSION_SECRET` only when explicitly deploying private authentication mode.
Neither the backend service token nor account session is returned to browser JS.

Preview environment secrets, database, SMTP sink and Stripe test account must be
separate from production. Never enable production data or payment collection in
Deploy Previews. Set production Stripe live mode deliberately only after test-mode
lifecycle acceptance. The repository does not invent an API URL or account keys.

Generate an encryption key using your secret manager or
`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
in a trusted operator shell, not CI logs. Preserve the key in backup/restore plans;
losing it makes queued transactional email undecryptable.
