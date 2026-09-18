# Local Ollama inference

Money's TradingAgents, AI-Hedge-Fund and CrewAI/CIO adapters use the same bounded
Money inference transport. They still have separate sessions, prompts, budgets
and reports. Qlib remains numeric; LEAN remains a historical validation engine.

**LOCAL OLLAMA != RAILWAY PRODUCTION INFERENCE.** The loopback endpoint belongs to
the Mac running Ollama. Railway cannot reach that Mac through `127.0.0.1`.
Local inference evidence cannot qualify a hosted worker or a production manifest.
These commands do not expose Ollama publicly or change Railway configuration.

## Start and test on the Mac

From the repository root, using the already installed Ollama and Money environment:

```bash
brew services start ollama
ollama pull qwen3:14b
MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/probe_inference.py
```

The probe loads all three configured roles, verifies the expected model in
`/v1/models`, then makes a bounded real `/v1/chat/completions` request for each
role: `Reply with exactly OK`. A successful response must have the exact selected
model, one completed assistant response and final `content` equal to `OK`.
Missing models, malformed responses, timeouts, tool output and model changes
fail closed. The output is machine-readable JSON containing non-secret selection,
actual response model and usage evidence. It is a functionality smoke test, not
an independent review or qualification of native research quality.

No API key is needed or read for these selections. Do not set `OPENAI_API_KEY`
for Ollama and do not insert `ollama`, `none`, `dummy` or any other fake key.
`authentication: "none"` means no Authorization header and no credential field.
Existing OpenAI credentials do not need to be changed or removed.

The checked-in local configuration uses `qwen3:14b` and
`http://127.0.0.1:11434/v1/chat/completions` for all three roles. Its limits are
100,000 prompt bytes, 8,000 output tokens and a 180-second request deadline;
the deterministic probe applies its own smaller output bound. Cold model loading
or insufficient RAM can still exceed a deadline, which is a failure rather than
a fabricated success.

## Resume local qualification

The runner automatically keeps local inference evidence in
`data/qualified/local-inference/`, separate from the production bundle:

```bash
MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/build_live_qualification.py
```

This runs locally and can record genuine inference receipts for TradingAgents,
AI-Hedge-Fund and CrewAI even when unrelated production prerequisites are absent.
The provider steps still need their own real environment credentials. Nothing
copies secrets from Railway or invents market/eligibility evidence. Rerun the
same command to reuse still-valid evidence and continue reviewed work.

Successful local requests clear only local inference functionality failures.
The runner still reports `QUALIFICATION BLOCKED` until the production contract
is met. In particular, they do not clear independently reviewed inference
selections/budgets, exact pinned native runtimes, dependency/security checks,
target-worker OS egress enforcement, fresh eligibility/evidence, Qlib manual
promotion, LEAN qualification or release approval. Local selections cannot enter
`LiveManifest`, even if an operator supplies a review. ChromaDB advisories remain
blockers where applicable; changing the inference provider does not fix them.

See the [qualification runner guide](LIVE_QUALIFICATION_RUNNER.md) for review
inputs and resume semantics. Do not hand-write a manifest or mark unknown review
facts as approved to get past these checks.

## Configuration selection and production boundaries

`MONEY_INFERENCE_CONFIG` explicitly selects the non-secret three-role JSON file.
If it is absent, the existing `data/configuration/live-inference.json` OpenAI
configuration is selected. Relative paths resolve from the repository root;
absolute paths are also supported. An empty, missing, malformed or unsafe
explicit selection is an error: there is no fallback to OpenAI or another model.
Changing the configuration invalidates dependent inference receipts/reviews.

Each `InferenceSelection` declares provider, model, endpoint, protocol,
authentication, endpoint scope and bounded generation limits. Authenticated
OpenAI-compatible providers use `authentication: "bearer"` and the name of a
credential environment variable; no credential value belongs in JSON. Existing
OpenAI selections that omit the authentication field retain bearer behavior.
The existing Anthropic protocol remains supported with explicit API-key mode.

The local file explicitly declares `endpoint_scope: "local"` and
`authentication: "none"`. Local access is restricted to loopback; it is not a
general exception to the public endpoint protections. Hosted production rejects
local-only endpoint selections, including `localhost`, `127.0.0.1` and `::1`.
The production/OpenAI file is not overwritten by choosing the local file.

An admitted live runtime continues to use the selections sealed inside its
qualified manifest. `MONEY_INFERENCE_CONFIG` does not hot-swap a qualified
production manifest's model; a different hosted selection requires a new
independently reviewed and qualified bundle. A future hosted open-source model
can use this transport with an explicit, remotely reachable HTTPS endpoint,
exact model identity, authentication and its own qualification/egress evidence.

## Reasoning, tools and research boundaries

Money consumes the final assistant `content`; a separate Qwen `reasoning` field
is never concatenated into research reports. All three checked-in Ollama roles
explicitly set `reasoning_effort: "none"` to use Qwen3 non-thinking inference.
Money validates this supported Ollama control and sends it unchanged in the
`/v1/chat/completions` JSON alongside `model`, `messages`, `temperature: 0` and
`max_tokens`. No `/api/chat` request or placeholder credential is used. An absent
control stays absent; other providers do not inherit this setting. The inference
timeout remains 180 seconds. The smoke probe records the selected reasoning
control, actual returned model, visible-content check and `finish_reason`.
Ollama/OpenAI-compatible token bounds
use the transport's supported request format; a provider-specific unsupported
parameter is not blindly forwarded. The model name is checked against the actual
returned identity. A local mutable tag is not an immutable hosted model attestation.

Native diagnostics in `outputs/research-first-pass.json` include `firm_runs`:
wall-clock duration, cache reuse, safe error code, recorded LLM calls and their
timings. `PROVIDER_TIMEOUT` identifies a model request timeout;
`NATIVE_AGENT_TIMEOUT` identifies the complete native workflow deadline.
The personal `run_research_testing.py` workflow has a separate **900-second
per-firm budget** (including child startup/integrity checks, imports, every
specialist/persona call and report validation inside the child). Override it
with `MONEY_RESEARCH_AGENT_TIMEOUT_SECONDS`, an integer from 1 to 1800; this does
not alter the selected model's 180-second individual request timeout or the
existing 24-call maximum. `execution_limits` and each `firm_runs` entry record
both deadlines. The two firms run independently and sequentially, so the default
allows up to 30 minutes across the two child processes, plus preflight/data work.
This is a bounded opportunity to complete the native workflows, not a guarantee
that this model/hardware will finish within the budget. The hosted manifest's
reviewed timeouts and release/LEAN gates are unchanged.
Empty visible responses, malformed structured reports, rejected tool calls,
adapter exceptions and subprocess failures have distinct codes. A killed child
may lose in-flight receipts: `call_accounting_complete: false` means a recorded
zero must not be interpreted as zero calls actually attempted. No raw prompts,
reasoning text, exception text or credentials are included in these diagnostics.

TradingAgents retains its specialist, research-debate and risk-review workflow;
AI-Hedge-Fund retains its independent investor/persona lifecycle. Both consume
the same immutable Money `ResearchSnapshot`, not unrestricted upstream data
tools. Money seals their reports and Qlib's independently generated numeric
report at `FIRST_PASS_LOCKED`. LEAN then validates/falsifies the historical study.
CrewAI remains the central assessor above those sealed reports, with independent
calculations, contradiction checks, Red Team and at most two cross-examination
rounds. It is not a majority vote or a single combined firm prompt.

The normal API/worker local-start commands are unchanged, but merely setting this
environment variable does not turn the synthetic demonstration into native
research or bypass live startup gates. The probe and runner above are the local
inference entry points while genuine snapshots, native dependencies and remaining
qualification evidence are being prepared. A missing qualified manifest must
remain a startup blocker for a live research worker.

Money stays research-only. It does not place orders, fetch broker balances or
positions, or add trading execution. Individual STOCK, GBP/GBX, current buy
live broker membership and ethical exclusions still need
fresh verified evidence; unknown/stale entries are rejected. Defence, weapons,
military and oil exclusions are unchanged.

Protocol references: [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
and [Ollama model catalogue](https://docs.ollama.com/api/tags). Money uses direct
HTTP without an SDK placeholder API key.
