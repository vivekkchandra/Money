# Commercial deployment

## Current release target: qualified live GBP/GBX ISA research

Primary application settings are now `MONEY_ENV=production` and
`MONEY_RESEARCH_MODE=live`, with the existing qualified manifest/provider/model
startup gates. The personal R&D deployment instructions below describe a separate
noncommercial mode, not a route around these gates. Existing project IDs, Git
reference tables, Netlify SSR and API/private-worker boundaries are unchanged.

Published `3ab2ff9` passed the release-SHA public web check in GitHub run
35156964352. The same release's broader CI (35156964337) failed PostgreSQL, backend
drill, LOGIN-stage HTTP smoke and research dependency checks. Do not promote this local follow-up
until those failures and the hosted live research acceptance are resolved.

For Railway, the API keeps `deploy/railway/api.toml` and the minimal default image;
the private worker now selects `Dockerfile.research` through
`deploy/railway/worker.toml`. No service was created or deployed by this follow-up.
Use the read-only `python -m money.research.preflight --role api|worker` with one
role selected to diagnose missing production configuration; its blocked exit is
intentional and it is not a readiness probe. See LIVE_CONFIGURATION.md for the
unresolved current-ISA-source and genuine qualification bundle requirements.

## Personal R&D deployment target (2026-09-16; retained separately)

This is separate from commercial launch. Use the existing owner-specified Railway
**incredible-flexibility / production / Money + Postgres**, not a new project.
This session cannot inspect/mutate it because connector access is administrator-disabled.
No actual Railway URL, PostgreSQL migration or healthy worker is claimed.

Checked-in configuration: `deploy/railway/api.toml` and `deploy/railway/worker.toml`,
selected as per-service config paths with the repository root build context.
They reuse the existing default non-root locked Docker image; no native research
extra is installed or falsely qualified. The worker has no public domain or HTTP
health path. Use `python -m money.worker --healthcheck` and the API's worker
heartbeat status for a read-only worker probe. The default image's API Docker
HEALTHCHECK is not a worker probe; Railway worker monitoring must use the heartbeat.
These files follow the [official config contract](https://docs.railway.com/config-as-code/reference).

Before enabling the API: back up existing state, run the API release migration
`alembic upgrade head` (now0008), start one private worker using the matching
`live_rnd` hosted settings, then verify `/health/ready`. Revision0008 is additive:
old jobs default to `standard`, new personal jobs use `live_rnd`; claims are isolated.
Do not downgrade to a pre0008 worker: old worker code lacks the kind filter.
Rollback by stopping personal submissions/workers and retaining the schema/data.

Only after actual readiness set Netlify's genuine HTTPS `RESEARCH_API_URL` and
matching service token. Existing `neon-griffin-08e616` and SSR settings are unchanged.
See ENVIRONMENT.md for the hosted personal/private login option and official sources.

Acceptance automation: `MONEY_ENV=development MONEY_RESEARCH_MODE=live_rnd uv run
python scripts/check_live_rnd.py --providers-only` performs real bounded provider
requests, never test fixtures. `--run-jobs --workspace rnd-acceptance-<unique-suffix>`
instead uses the configured migrated database and supervised worker, keeps its
diagnostic results, and does not claim unrelated workspace jobs. It distinguishes
an evidence-only study from a complete native research run. Missing data is failure,
not demo substitution. This is not browser/hosted acceptance.

**Commercial launch blocked.** Local implementation and synthetic tests do not
qualify a paid research product. GitHub run 35146196319 verified public root HTTP
200/routes/assets/security after commercial commit 6846bdc; it did not establish
the deployed SHA, real email, payment, PostgreSQL restore or live research.

The owner selected **Railway** as the compute/database target. The current session
cannot inspect it: connector discovery is administrator-disabled and CLI status
and identity checks fail API DNS. This is not a verified deployment or database;
no remote Railway changes have been made. Reuse the existing approved Money
project once accessible, retaining the separate processes and migration ordering
below. Netlify remains the existing web project.

## Release ordering

1. Validate Git assets with `uv run python scripts/validate_data_tables.py` and run CI.
2. Back up the database, then run `alembic upgrade head` as a release process.
   Revisions 0004/0005 add customer state; 0006 adds reviewed offboarding and
   recipient indexing; 0007 adds immutable call metering. Existing research is retained.
   Downgrades deliberately refuse destructive customer/audit removal; roll back
   application images or use a reviewed forward migration.
3. Build the default control-plane image with `--build-arg MONEY_GIT_SHA=<release SHA>`.
   Build the separate research worker image with `--target research` and the same SHA.
   Only the research target includes CrewAI/ChromaDB. Its unresolved advisory gate
   remains blocking; the default API/email/billing target excludes those packages.
   `/app/data` is the read-only versioned asset directory. Runtime caches and
   customer state must never be written into it. Set `MONEY_REFERENCE_DATA_DIR`
   explicitly in a wheel-only installation.
4. Run separate API, research worker, email worker and commercial worker processes:

   ```sh
   uvicorn money.api.app:app --host 0.0.0.0 --port 8000 --no-server-header
   python -m money.worker
   python -m money.accounts.email_worker
   python -m money.product.worker
   ```

5. Configure the existing Netlify project `neon-griffin-08e616` with the real
   HTTPS API URL and matching service token. Root `netlify.toml` keeps base
   `apps/web`, build `npm run build`, publish `.next`, explicit Next.js Runtime
   and a post-build SSR artifact guard. Do not initialize another project.
6. Run `npm run check:deployment --prefix apps/web -- https://neon-griffin-08e616.netlify.app --production --expected-sha <full-release-SHA>`.
   Inspect actual Netlify logs, deploy SHA, SSR function bundle and production URL.

`docker compose --profile commercial up --build` is the local topology. It is not
a managed-host deployment, backup service or public TLS endpoint.

## Reviewed offboarding

Customers can transfer ownership to an existing verified member using their current
password. A last owner with other members must do so before deletion. Deletion
immediately revokes access, sessions, actions and outstanding invitations; sole-owner
workspaces close to new research/checkout. Immutable research/audit records remain.

Use `python -m money.accounts.offboarding --help` for the operator workflow:
`inspect --user-id <UUID>`, `close-billing`, recipient-index backfill, policy review
and eligible fulfilment. The commercial worker also drains one eligible billing
closure each cycle; its health probe performs no processing. Billing cancellation is durable/idempotent and verified
against Stripe identity; cancellation alone never proves outstanding invoices settled.
Review requires a named reviewer, approved versioned retention policy, billing
resolution and review of retained records/backups. `fulfil --user-id <UUID>` only
pseudonymises eligible mutable identity after the configured period, with no active
jobs/email delivery. It does not erase immutable packets or provider billing records.
Schedule reviewed fulfilment with your approved host scheduler; no automatic legal
retention duration or production schedule is invented by this repository.
`retry-billing --workspace-id <UUID> --reviewer <reference>` records approval to
retry a final failure without changing its cancellation identity. An abandoned
checkout with no subscription requires `resolve-unlinked-billing`, a reviewer and
`--provider-review-reference`; it cannot bypass cancellation of a known subscription
or pending checkout/reconciliation. Review actual provider records before approval.
A recipient suppression hash and immutable identifiers remain retained to prevent
late invitations/notifications from reintroducing erased contact details. This is
local mutable-PII fulfilment, not a claim that all pseudonymous/audit/billing records
or external backups were erased. Per-request review must cover those obligations.

## Call accounting

Migration 0007 stores immutable, fenced, tenant-attributed call receipts. Admin
diagnostics distinguish native invocation duration from individual inference calls
(their durations overlap). Actual cost remains null unless explicitly reported;
configured token rates produce estimated cost, never invoice cost. Unknown usage,
unfinished calls and errors remain visible. No prompts, evidence or credentials
are included in receipts. Provider integrations without per-call telemetry remain
explicitly unknown rather than being assigned fabricated call/cost totals.

## Modes and onboarding

Both web and API use `MONEY_AUTH_MODE=saas`. The service token authenticates the web
server, not the customer. A second opaque session authenticates each customer;
workspace membership is checked on every research/product request. Tokens remain
HttpOnly cookies and hashed database records. Private mode remains only for an
explicit legacy installation, not multi-customer service.

SaaS may start with research unconfigured so accounts remain usable during a
provider outage. Such startup **does not qualify live research**. Non-DEMO requests
return unavailable until the real live manifest and commercial rights pass.
In development/test only, `MONEY_ENABLE_SYNTHETIC_DEMO=true` permits explicitly
labelled `DEMO.L` through the real queue/worker/storage, with zero LLM entitlement.
Production/preview reject this flag and `MONEY_RESEARCH_MODE=demo`; neither may
serve synthetic results as a substitute for unavailable live research.

The intended journey is signup → email verification → login → workspace → plan →
research preferences → request. SMTP must be configured for a customer to complete
verification without developer involvement. The FREE allowance is a versioned
product primitive, not published final pricing. Paid prices are environment-mapped
Stripe Price IDs; browser claims never grant subscriptions.

## Billing

Configure a Stripe endpoint at the **API host** `/v1/product/billing/webhook`.
Subscribe to `customer.subscription.created/updated/deleted`,
`checkout.session.completed`, `invoice.paid`, `invoice.payment_failed`.
Only this endpoint bypasses the web service token; it verifies the raw-body HMAC
and five-minute timestamp window, then persists an event ID/hash before HTTP202.
No raw invoice/customer payload is stored. The commercial worker retrieves current
subscription state, verifies customer/workspace/price/mode, and serializes updates.
Unknown mappings or duplicates never grant entitlements; five failed attempts enter
FAILED. Inspect failed event IDs through internal diagnostics.

Checkout and portal use fixed HTTPS Stripe endpoints, bounded responses, server
price IDs, trusted return URLs and scoped idempotency. Pending checkout is reused
to avoid duplicate subscriptions. Upgrade/downgrade/cancellation are handled by
the configured Stripe billing portal, with entitlements reconciled from Stripe.
Enable and test those actions in your Stripe portal configuration before launch.
[Stripe checkout](https://docs.stripe.com/api/checkout/sessions/create),
[webhook verification](https://docs.stripe.com/webhooks/signature),
[portal](https://docs.stripe.com/api/customer_portal/sessions/create).

## Unmet commercial release gates

- Unresolved ChromaDB transitive dependency advisories documented in SECURITY.md;
  no safe fixed version was established. Dependency CI must pass after a reviewed
  remediation; do not bypass it to deploy the research image.
- Public Netlify SSR/route/auth/asset acceptance and green release CI.
- Authorized OCI host, TLS PostgreSQL, deployed API/workers, actual restore/recovery.
- SMTP verified sender and end-to-end delivery/reset/invitation acceptance.
- Stripe account/products/prices/webhook/portal and paid lifecycle acceptance.
- Production monitoring/alert destination and tested backup retention/PITR.
- Operator identity, reviewed legal/privacy/retention/pricing terms and commercial
  data rights. Public legal pages are editable drafts, not legal approval.
- Qualified native firms/models/LEAN and real data; existing integration gaps
  remain listed in VERIFICATION.md. No synthetic result counts as live performance.
