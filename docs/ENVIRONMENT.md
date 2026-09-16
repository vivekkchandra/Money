# Server configuration

Examples in `.env.example` are placeholders only. Secrets are never Git assets.

## Explicit personal live R&D (not commercial production)

API, worker and web use `MONEY_ENV=development`, `MONEY_RESEARCH_MODE=live_rnd`,
`MONEY_ENABLE_SYNTHETIC_DEMO=false`. For the Railway/Netlify hosted deployment set
`MONEY_DEPLOYMENT_ENV=hosted` on all three: PostgreSQL, service authentication,
HTTPS, Secure cookies and strict CSP remain required despite the development label.
`MONEY_DEPLOYMENT_CONTEXT` is an existing web deployment-context setting; do not
confuse it with the new application security distinction `MONEY_DEPLOYMENT_ENV`.
Production/preview refuse `live_rnd`; qualified `live` still requires its manifest.

- Railway project **incredible-flexibility**, environment **production**, existing
  **Money** API and **Postgres**, are owner-specified targets, not verified remote state.
  Set API/worker `DATABASE_URL=${{Postgres.DATABASE_URL}}` as a service reference;
  never copy resolved credentials into docs or logs.
- `RESEARCH_API_TOKEN`: same strong server-only secret in API, worker and Netlify.
  `RESEARCH_API_URL`: actual healthy Railway HTTPS API URL, Netlify only.
- `MONEY_AUTH_MODE=private` explicitly reuses personal workspace/password login,
  not email-verified customer signup. Web needs `MONEY_WEB_PASSWORD` and
  `SESSION_SECRET` from the secret store. SaaS mode and real email verification
  remain available unchanged; no verification bypass was introduced.
- `COMPANIES_HOUSE_API_KEY` plus `MONEY_RND_COMPANY_NUMBERS` (JSON ticker→reviewed
  company-number mapping): required for UK official filing metadata. Prefer a
  reviewed versioned mapping, never guess a company number from its trading symbol.
- `MONEY_SEC_USER_AGENT`: real application name and operator contact email required
  for SEC. No invented contact, no key required. Global DB admission ≤5 requests
  per fixed one-second window plus process pacing bounds concurrent worker traffic.
- `MONEY_RND_PROVIDER_TIMEOUT_SECONDS`: 5–60, default30; company search has a
  separate4-second deadline. Yahoo quotes retain UNKNOWN freshness, source timestamps,
  raw currency and GBX normalization; feeds older than7 days fail explicitly.
- `FRED_API_KEY`: optional; configured FRED/ONS adapters are not default snapshot
  sources. Default macro source is keyless official BoE Bank Rate `IUDBEDR`.

Personal mode requires no commercial market-data licence. It does not grant
commercial redistribution rights. Native multi-firm R&D execution is not yet
implemented; persisted evidence studies explicitly remain INSUFFICIENT_EVIDENCE.

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

Company search uses the same hash-pinned `MONEY_LIVE_MANIFEST` as research, not a
second provider directory or customer-supplied catalogue. API and worker must mount
the same reviewed release. Expired identifiers/ISA verification/provider approval
stop new requests; updating reviewed assets requires a validated release/restart.
Market credentials default to `EODHD_API_KEY` (the manifest may explicitly choose
another environment-variable name); the HTTP query parameter is `api_token`.

Netlify settings needed by route handlers must include the **Functions** scope;
`netlify.toml` build environment alone does not supply runtime function secrets or
settings. Set the actual SaaS/environment/API settings in the existing project's
scoped environment store. Never bake tokens or backend/provider secrets into
Next.js `env`. Only the validated public release SHA is baked into this web build.
See [Netlify function environment scope](https://docs.netlify.com/build/functions/environment-variables/).
