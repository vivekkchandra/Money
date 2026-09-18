# Research admission is not production approval

The personal research/testing workflow admits a current authenticated Trading 212
`STOCK` quoted in `GBP` or `GBX` with a present ticker/ID and non-conflicting basic
identity. It does not establish buyability, ISA availability, licensing, ethical
approval, investment suitability or release approval.

The stages are `DISCOVERED`, `RESEARCH_ELIGIBLE`, `RESEARCHED`, `BACKTEST_READY`,
then `LEAN_VALIDATED`. Qlib is disabled. LEAN is mandatory for the last state.
Universe policy: `money-t212-gbp-gbx-research-universe-v7`. Historical raw bytes
and review files survive automatic migration; old classification decisions do not.
The legacy `trading212-gbx-stock-universe.json` filename now includes both GBP
and GBX and is retained only for artifact-reader compatibility.

Company profile, Companies House, jurisdiction, financial filings, fundamentals,
news and ethics are optional enrichment for research. `AVAILABLE`, `UNAVAILABLE`,
`NOT_CONFIGURED`, `ACCESS_DENIED` and `STALE` describe coverage, not admission.
Ethics is recorded as `NOT_SCREENED`, `PASS`, `FAIL` or `UNKNOWN`; no missing
evidence becomes PASS. Commercial/public release protections remain separate.

## Run locally

Start the local inference service and obtain the configured model:

```sh
brew services start ollama
ollama pull qwen3:14b
```

From the normal Mac terminal, with the existing Railway credentials injected
only into the child process:

```sh
railway run --service Money --environment production -- sh -c 'MONEY_USAGE_MODE=personal_research MONEY_QLIB_ENABLED=false MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/run_research_testing.py --instrument NICLl_EQ'
```

Keep the quoted command on one logical line. Do not run bare `env` or print
environment variables. This command does not change hosted service settings.
Nichols is an explicit pipeline test, not an investment ranking or a special
case in admission. Other eligible identifiers use the same workflow.

The same command automatically provisions and resumes the two pinned native
environments, `.venv-tradingagents` and `.venv-ai-hedge-fund`. No manual activation
is needed. Each uses Python 3.12.14, its complete upstream dependency graph,
hash-locked installation and an unsuppressed dependency audit. Initial setup
requires package-index access. Failed installation, import or security checks
stop research; they do not fall back to generic prompts. See
[native runtime setup](NATIVE_WORKER_BUILD.md) and
`outputs/research-native-preflight.json` for exact pins and current results.

**LOCAL OLLAMA != RAILWAY PRODUCTION INFERENCE.** `railway run` launches the
local command with service variables; Railway-hosted workers cannot reach the
Mac through `127.0.0.1`. This workflow does not expose Ollama or certify hosted
inference, native worker egress, release or production readiness.

## Evidence and execution boundaries

Freeze the complete admitted universe before the bounded shortlist. Native
TradingAgents and AI Hedge Fund receive the same immutable snapshot independently,
including explicit missing data. Exact source pins, dependency/security audit,
bounded inference and report validation still apply. Native or security failures
remain actionable blockers; generic replacement prompts are not native reports.

Both original reports are retained before `FIRST_PASS_LOCKED`. The deterministic
comparison identifies matching claims and different interpretations of shared
evidence; it is explicitly a limited comparison, not completed CIO/Red Team work.
It does not average confidence or invent semantic agreement.

Historical market data is mandatory when technical calculations/backtests use it.
Backtests require genuine price history, exact security mapping, correct units,
appropriate corporate-action handling, PIT-safe strategy inputs, OOS/walk-forward
setup and documented cost/slippage assumptions. Assumed costs are not observed
broker costs. No fabricated prices or simulated LEAN completion are admitted.

`inputs/research-market-data.json` chooses provider order (default EODHD then
Yahoo). Set `yfinance_symbols` to an exact Trading 212 ID → actual Yahoo symbol
mapping to enable the supported fallback. Money corroborates the returned symbol,
ticker, name and currency; configuration alone is not mapping approval. It never
guesses `.L`. Cheap screening reads caches only; only shortlisted stocks may fetch.
Yahoo does not establish an ISIN or original historical publication/PIT evidence.

`inputs/research-backtest.json` records assumed spread/slippage/fees and a real
digest-pinned LEAN image. No reviewer signature is required for an experimental
cost scenario. Missing price availability or complete adjustment evidence remains
a backtest blocker, even when current research on the prices is permitted.

Inspect `outputs/research-testing-result.json` under the selected qualification
root. Its counts and per-candidate outcomes are authoritative for that local run;
saved replay is never a substitute for current authenticated membership.

The personal R&D detail page also offers a local JSON viewer. Choose that output
file to inspect whitelisted counts/statuses in the browser. Nothing is uploaded;
raw provider data and report prose are not displayed. An imported file is not
server-verified evidence and never replaces the strict production-universe API.
