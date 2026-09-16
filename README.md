# Money

Independent multi-firm investment research, with a Next.js web/control plane for
Netlify and a separate Python compute plane backed by PostgreSQL. Money never
accesses a broker portfolio or creates executable orders. Investment decisions
remain manual.

## What works in this slice

- Immutable mandates, objective snapshots and provider provenance with point-in-time checks.
- ISA/ethics/currency gates, isolated first-pass inputs, write-once reports and a durable three-firm barrier.
- Authenticated job submission, a separate worker with database leases, immutable decision packets and evidence retrieval after process restart.
- Responsive research workspace: mandate drafts, discovery, jobs, separate firm reports, audits, evidence, active/expired signals, outcomes and health.
- PostgreSQL migrations, Compose, GitHub CI and Netlify configuration.

The credential-free development demonstration uses the fictional `DEMO.L` ticker.
It is clearly labelled synthetic, does not run the upstream firms, and finishes
with **INSUFFICIENT_EVIDENCE** because real LEAN validation is absent. Production
forbids demo mode. Unconfigured eligibility rejects a candidate; missing evidence
or a runner never becomes an investment signal.

Snapshot-backed adapter boundaries for TradingAgents, AI-HF, Qlib and LEAN are
implemented and tested. Fully qualified native firm workflows, UK provider
connections, CrewAI execution and calibrated live signals remain deployment
gates. See [implementation plan](docs/IMPLEMENTATION_PLAN.md) and the actual
[Graphify discovery audit](docs/GRAPHIFY_DISCOVERY.md).

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

## Verification and deployment

```sh
uv run pytest
uv run ruff check .
uv run mypy src/money
uv run python scripts/check_deployment.py
npm run lint --prefix apps/web
npm run typecheck --prefix apps/web
npm test --prefix apps/web
npm run build --prefix apps/web
```

Connect GitHub to Netlify with `main` as production branch and pull-request Deploy
Previews. Host the container API and worker separately with PostgreSQL; Netlify
routes only authenticate, validate, enqueue and read. Cloud accounts and credentials
are not provisioned by this repository.

Read [architecture](docs/ARCHITECTURE_ANALYSIS.md), [deployment](docs/DEPLOYMENT.md),
[Netlify setup](docs/NETLIFY.md), [governance](docs/RESEARCH_GOVERNANCE.md), and
[test strategy](docs/TEST_STRATEGY.md). `knowledge/STACK.md` remains authoritative.
