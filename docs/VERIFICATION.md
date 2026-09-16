# Vertical-slice verification — 2026-09-16

This records local evidence, not a claim that GitHub CI or Netlify has run.

| Check | Result |
| --- | --- |
| `uv sync --locked --offline` | Passed against existing installed/cached dependencies; Python lock retained |
| `pytest` | 94 passed; 1 PostgreSQL-specific test skipped because TEST_DATABASE_URL was unavailable |
| Ruff / mypy | Passed; 45 Python source files checked by mypy |
| SQLite migrations and Alembic schema drift | Passed in integration tests |
| API enqueue → request completion → separate worker subprocess → fresh API retrieval | Passed in integration tests |
| Report barrier, immutable DB records, worker fencing, consensus recheck | Passed in integration tests |
| `npm ci --offline` | Reproduced the committed web lock using existing dependency cache |
| Web lint / typecheck / Vitest | Passed; 33 web tests |
| Next.js production build | Passed without provider or research-service credentials |
| Deployment boundary checker and Compose configuration | Passed |
| Native QuantStats smoke | Finite Sharpe and correct first-loss drawdown verified |
| Money `graphify update .` | AST-only refresh completed; upstream indexes untouched |

## Checks requiring another environment

The local sandbox blocks PostgreSQL shared-memory initialization, the Docker
daemon socket, and localhost listeners. Consequently the production PostgreSQL
integration test, Docker image build, and real Next.js-to-API HTTP smoke are
configured in CI but **not verified locally**. No browser screenshot/interactive
visual test was possible here. The web HTTP smoke command is
`npm run test:smoke --prefix apps/web`; it creates and cleans an isolated test
database and ephemeral credentials.

GitHub/Netlify connection, branch protection, Deploy Preview and production
deployment remain external setup. Changes are local and uncommitted. Live
provider and research-firm qualification remains open as described in
IMPLEMENTATION_PLAN.md. The runnable demonstration only publishes a synthetic
INSUFFICIENT_EVIDENCE packet, never an investment signal.

Two existing FastAPI/Starlette test-client deprecation warnings are non-failing.
Graphify reports a pre-existing installed Claude skill/package version mismatch;
the AST update succeeds and no global skill installation was changed.
