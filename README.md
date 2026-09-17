# Money

Independent multi-firm investment research, with a Next.js web/control plane for
Netlify and a separate Python compute plane backed by PostgreSQL. Money never
accesses a broker portfolio or creates executable orders. Investment decisions
remain manual.

## Implemented product

Commercial customer mode (`MONEY_AUTH_MODE=saas`) adds accounts and email
verification, password reset, workspaces/roles/invitations, session-derived tenant
access, subscription entitlements, transactional usage admission, Stripe checkout
and durable webhook reconciliation, encrypted transactional email, watchlists,
paginated history and notifications. The web root is a product website; customer
onboarding and independent research records are separate application screens.

Git reference tables remain Git assets. Schema/provenance/checksum validation runs
at startup and in CI; mutable accounts, billing and research stay in PostgreSQL.
Commercial rights are denied until reviewed, including when reading saved evidence.
See [commercial deployment](docs/COMMERCIAL_DEPLOYMENT.md),
[environment](docs/ENVIRONMENT.md), [data tables](docs/DATA_TABLES.md) and
[security](SECURITY.md). These implemented paths are **not a commercial launch
qualification**: live web/backend, SMTP, payment lifecycle, PostgreSQL recovery,
monitoring, legal approval and licensed native research must pass acceptance.

- Immutable mandates, objective snapshots and provider provenance with point-in-time checks.
- ISA/ethics/currency gates, isolated first-pass inputs, write-once reports and a durable three-firm barrier.
- Workspace-scoped submission, durable sessions/rate limits/idempotency, bounded retries, leased/fenced workers, checkpoint recovery and immutable decision packets.
- Bounded UK data fetchers, provider qualification, explicit identifiers/GBP/GBX, TA-Lib/catalyst/fundamental/qualified-Qlib discovery and archived-publication PIT controls.
- Native snapshot-only firm assemblies, isolated LEAN runner, actual CrewAI Flow, independent verification, bounded cross-examination and deterministic signal gates.
- Durable token budgets, provider-neutral inference, manual model registry, immutable replay, informational alerts and research-reference outcomes.
- Responsive research workspace: mandate drafts, discovery, jobs, separate firm reports, audits, evidence, active/expired signals, outcomes and health.
- PostgreSQL migrations, Compose, GitHub CI and Netlify configuration.

The credential-free development demonstration uses the fictional `DEMO.L` ticker.
It is clearly labelled synthetic, does not run the upstream firms, and finishes
with **INSUFFICIENT_EVIDENCE** because real LEAN validation is absent. Production
forbids demo mode. Unconfigured eligibility rejects a candidate; missing evidence
or a runner never becomes an investment signal.

Development/test commercial mode can enable synthetic-only `DEMO.L` admission with
`MONEY_ENABLE_SYNTHETIC_DEMO=true`, through the durable worker. This cannot enable
live research, consume inference tokens, or replace a failed real integration.
Both production and preview reject this flag, including access to synthetic results.

**PRODUCTION BLOCKED.** Implemented adapters are not qualified live integrations.
Credentials/licences, verified ISA/business evidence, historical PIT archives,
approved model, pinned native runtimes, LEAN image and external host verification
remain required. See the [verification matrix](docs/VERIFICATION.md),
[implementation plan](docs/IMPLEMENTATION_PLAN.md), [live configuration](docs/LIVE_CONFIGURATION.md)
and [Graphify audit](docs/GRAPHIFY_DISCOVERY.md). No synthetic result is a live signal.

## Local start

Use Python 3.12+, uv 0.12.5 and Node 22. Install with:

```sh
uv sync --locked
npm ci --prefix apps/web
```

Create `.env` from `.env.example`, setting a URL-safe PostgreSQL password and a
random API token of at least 32 characters. Development demonstration mode is
explicit in the example. Terminal 1:

```sh
docker compose up --build
```

Compose starts PostgreSQL, applies migrations, and runs the API and worker as
separate services. Create `apps/web/.env.local` with the four web variables in
[local development](docs/LOCAL_DEVELOPMENT.md). Terminal 2:

```sh
npm run dev --prefix apps/web
```

Open `http://localhost:3000`, sign in with your configured workspace password and
request research for `DEMO.L`. All completed reports remain available after refresh.

For local **real inference** with the already-running Ollama server, see
[Ollama inference](docs/OLLAMA_INFERENCE.md). The explicit local selection is:

```sh
MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/probe_inference.py
```

No OpenAI key or fake credential is needed. This probes each LLM role without
converting the demonstration into qualified native research. **Local Ollama is
not Railway production inference**; existing live evidence, native-runtime,
first-pass, review and release gates remain mandatory.

## Verification and deployment

For live production admission, use the [resumable qualification runner](docs/LIVE_QUALIFICATION_RUNNER.md):

```sh
railway run --service Money --environment production uv run python scripts/build_live_qualification.py
```

It generates review inputs and only assembles a manifest from genuinely qualified
evidence. Rerun the same command after completing required independent reviews.

```sh
uv run pytest
uv run ruff check .
uv run mypy src/money
uv run python scripts/check_deployment.py
uv run python scripts/validate_data_tables.py
npm run lint --prefix apps/web
npm run typecheck --prefix apps/web
npm test --prefix apps/web
npm run build --prefix apps/web
```

Preserve the existing GitHub → Netlify connection: `main` is the production branch
and pull requests use Deploy Previews. Do not reinitialize Netlify. Host the API
and worker separately with PostgreSQL; Netlify
routes only authenticate, validate, enqueue and read. Cloud accounts and credentials
are not provisioned by this repository.

Read [architecture](docs/ARCHITECTURE_ANALYSIS.md), [deployment](docs/DEPLOYMENT.md),
[Netlify setup](docs/NETLIFY.md), [governance](docs/RESEARCH_GOVERNANCE.md), and
[test strategy](docs/TEST_STRATEGY.md). `knowledge/STACK.md` remains authoritative.
