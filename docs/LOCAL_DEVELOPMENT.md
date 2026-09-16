# Local development

The Python package stays at `src/money`. Use Python 3.12+, uv 0.12.5 and Node 22.
Install locked base dependencies with `uv sync --locked` and
`npm ci --prefix apps/web`. The base environment does not contain every pinned
native firm package; separately qualified runtimes are required for live research.
They never enter the Netlify build.

Create `.env` from `.env.example`. Set a URL-safe `POSTGRES_PASSWORD` and random `RESEARCH_API_TOKEN` (32+ characters). Use `MONEY_ENV=development` and `MONEY_RESEARCH_MODE=demo` for the synthetic `DEMO.L` demonstration. Run `docker compose up --build`; Compose waits for PostgreSQL, applies `alembic upgrade head`, then starts separate API and worker processes. Database data lives in a named volume. Do not remove that volume unless intentionally deleting local research.

Create `apps/web/.env.local` with these **server-only** values:

```dotenv
RESEARCH_API_URL=http://127.0.0.1:8000
RESEARCH_API_TOKEN=<same token as backend, at least 32 characters>
MONEY_WEB_PASSWORD=<unique workspace password, at least 16 characters>
SESSION_SECRET=<independent random secret, at least 32 characters>
MONEY_ENV=development
```

Terminal 2: `npm run dev --prefix apps/web`, then open `http://localhost:3000`. Alternatively run `npx netlify dev` at the repository root; the configured proxy listens on port 8888. Sign in and request `DEMO.L`. API requests only enqueue; keep the worker running separately.

Without Docker, use an existing disposable/local PostgreSQL and export the required environment values, then run:

```sh
uv run alembic upgrade head
uv run uvicorn money.api.app:app --host 127.0.0.1 --port 8000
```

In another terminal with the same environment: `uv run python -m money.worker`. For one-job diagnostics use `--once`. `uv` does not implicitly load this project's `.env`; explicitly supply your shell/IDE environment (or its `--env-file .env` option). Do not point tests at a production database. `TEST_DATABASE_URL` enables the PostgreSQL integration test; it creates and removes its own random schema.

For offline local tests only, `MONEY_ENV=test` with a `sqlite:///...` URL is supported. Production settings reject SQLite and unauthenticated access. No broker/provider credential is necessary for the demo.

Use explicit development fixture mode for a credential-free demonstration. Synthetic evidence must remain labelled in every packet. Production mode requires PostgreSQL, authentication and verified eligibility/data/firm runners, and rejects fixture mode.

Verification: `uv run pytest`, `uv run ruff check .`, `uv run mypy src/money`, `uv run python scripts/check_deployment.py`, plus `npm run lint --prefix apps/web`, `npm run typecheck --prefix apps/web`, `npm test --prefix apps/web`, and `npm run build --prefix apps/web`.

After building the web app, `npm run test:smoke --prefix apps/web` starts a temporary API, Next.js server and separate worker, authenticates and completes `DEMO.L`, retrieves its sealed reports and packet, then removes the temporary database. It requires permission to listen on localhost. CI runs it in addition to the production PostgreSQL integration test. The committed `.npmrc` preserves npm's tested peer-resolution setting for `npm ci`.
