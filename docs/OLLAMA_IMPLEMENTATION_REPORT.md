# Ollama implementation verification — 2026-09-17

The provider-neutral implementation is complete. The operator confirmed that the
new probe passed on the Mac for all three roles using Ollama `qwen3:14b` with
`authentication: none`. The agent sandbox could not independently reach the same
listener. No hosted inference, native research, production bundle or release has
been qualified by these changes.

## Implemented boundaries

- Shared, bounded inference supports explicit bearer/API-key authentication or
  credential-free local loopback inference. No dummy key is used.
- `MONEY_INFERENCE_CONFIG` selects an exact three-role file. The new
  `ollama-inference.json` selects `qwen3:14b`; production/OpenAI JSON is unchanged.
- Ollama uses final assistant `content`, never concatenated `reasoning`.
- The standalone probe and resumable runner share the same actual model-list
  and bounded `Reply with exactly OK` checks. Receipts bind selection, endpoint,
  actual returned model, scope and observation time to real artifact bytes.
- Local success cannot admit a `LiveManifest`, hosted settings, worker egress,
  native execution or release. Explicit runtime config cannot override the
  selections inside an admitted manifest.
- Native sessions remain separate; only their exact gateway port is propagated.
  Qlib, LEAN, immutable snapshots, first-pass sealing, ethical/ISA eligibility,
  Red Team and the two-round correspondence ceiling are unchanged.
- Local runner state defaults to `data/qualified/local-inference/`. Existing
  operator material under `data/qualified/` is preserved and is not committed.

## Checks executed

| Check | Observed result |
| --- | --- |
| Full Python suite | 1,386 passed, 157 skipped, 28 warnings; no failures |
| Web tests | 315 passed across 17 files |
| Ruff | Passed |
| Mypy | Passed, 123 source files |
| Web lint/typecheck | Passed |
| Web production build | Passed |
| Deployment boundary check | Passed |
| Reference data validation | Passed; exclusion table unchanged |
| Opt-in production integration | 11 skipped: `QUALIFICATION_MANIFEST_REQUIRED`; **not passes** |
| Standalone Ollama probe, normal Mac terminal | Operator reported `INFERENCE_PROBE_PASSED`, no failures, all three roles successful |
| Standalone Ollama probe, agent sandbox | Exit 2, `INFERENCE_PROBE_BLOCKED`; all three roles returned `PROVIDER_UNAVAILABLE` |
| Resumable runner, including rerun in agent sandbox | Exit 2, `QUALIFICATION BLOCKED`; local directory and review inputs preserved |

Python skips remain database/live prerequisites, not successful coverage. No
security advisory or warning was suppressed. The security scan's unavailability
is explicitly a blocker, not a clean bill of health.

Read-only inspection observed an Ollama process listening on
`127.0.0.1:11434`; the installed client reported `0.34.1`. Nevertheless, both
curl and Money's real bounded probe could not connect from this restricted
sandbox. This does not establish that the user's server is unavailable from
their normal terminal.

The operator subsequently ran the new `scripts/probe_inference.py` from the normal
Mac terminal and confirmed successful server reachability, model availability,
chat completions and final content verification for `tradingagents`,
`ai_hedge_fund` and `crewai`. The reported provider/model were `ollama` /
`qwen3:14b`, protocol `openai-compatible`, authentication `none`, with no failures.
Its scope remained `LOCAL_INFERENCE_ONLY`, `native_runtime_qualified: false` and
`production_qualified: false`. This is operator-reported execution evidence; no
receipt bytes or hashes were manufactured from the summary and no agent-side
failed checkpoint was rewritten as successful.

## Blocker changes and remaining actions

The OpenAI credential dependency and the OpenAI-only selection restriction are
removed in explicit Ollama mode. The Mac-side probe confirms local functionality
for all three roles. The last agent-side runner still contains its genuine
connection failures; running the same qualification command from the normal Mac
terminal lets it capture its own successful, resumable local receipts. See
[local operation](OLLAMA_INFERENCE.md). Local functionality does not clear any
hosted, native, review or release qualification requirement.

The last agent-side runner reports these evidence/action groups:

- `INFERENCE_PROBE_FAILED_TRADINGAGENTS`, `INFERENCE_PROBE_FAILED_AI_HEDGE_FUND`,
  `INFERENCE_PROBE_FAILED_CREWAI`: sandbox-only connection failures; the operator's
  normal-terminal probe succeeded. Rerun the runner there to record its receipts.
- `LOCAL_INFERENCE_NOT_HOSTED_QUALIFIED`: Railway requires a separately qualified,
  remotely reachable endpoint; the Mac's loopback cannot satisfy this.
- `REVIEWED_INFERENCE_SELECTIONS_REQUIRED`: independent selection/budget review.
- `PRODUCTION_CONFIGURATION_REQUIRED`: local probes do not constitute the
  existing hosted-production environment. No production settings were changed.
- Broker/provider credentials are absent from this local process, independently
  of their presence on Railway. Fresh reviewed ISA/ethical/identifier joins,
  provider rights, financial-document and supplemental evidence remain required.
- Exact native source qualification for TradingAgents, AI-Hedge-Fund, Qlib and
  CrewAI; native dependency resolution and a complete security audit.
- `HOST_EGRESS_REMOTE_INFERENCE_REQUIRED` and
  `HOST_EGRESS_TARGET_EVIDENCE_REQUIRED`: real target-worker enforcement.
- `QLIB_ARCHIVED_INPUTS_REQUIRED`: genuine archived training/validation evidence
  and subsequent independent manual promotion, not fabricated model IDs.
- `SNAPSHOT_REVIEWED_DATA_REQUIRED`, `FIRST_PASS_CURRENT_PREREQUISITES_REQUIRED`,
  `LEAN_FIRST_PASS_REQUIRED`, `CIO_LOCKED_REPORTS_AND_LEAN_REQUIRED`:
  genuine evidence, sealed independent reports and LEAN must precede CIO/Red Team.
- `RELEASE_APPROVAL_REQUIRED`: independent approval of a genuinely complete bundle.

The current machine-readable list is in
`data/qualified/local-inference/status.json`. No production manifest was created.

## Changed files

Configuration/documentation:

- `.gitignore`
- `README.md`
- `data/configuration/ollama-inference.json`
- `docs/LIVE_QUALIFICATION_RUNNER.md`
- `docs/OLLAMA_INFERENCE.md`
- `docs/OLLAMA_IMPLEMENTATION_REPORT.md`

Implementation:

- `scripts/probe_inference.py`
- `src/money/api/settings.py`
- `src/money/qualification/core.py`
- `src/money/qualification/native.py`
- `src/money/qualification/runner.py`
- `src/money/research/correspondence.py`
- `src/money/research/inference.py`
- `src/money/research/inference_config.py`
- `src/money/research/inference_probe.py`
- `src/money/research/live.py`
- `src/money/research/preflight.py`

Tests:

- `tests/production/test_native_live.py`
- `tests/unit/test_inference_deployment.py`
- `tests/unit/test_inference_probe.py`
- `tests/unit/test_ollama_inference.py`
- `tests/unit/test_ollama_native_integration.py`
- `tests/unit/test_qualification_native.py`
- `tests/unit/test_qualification_runner.py`

The production test helper now resolves the explicitly selected credential and
additionally rejects local endpoints; no existing production assertion or skip
policy was relaxed. Graphify was used for navigation and its source graph was
updated. Separate implementation ownership and independent cache/scope review
were used to check the shared boundaries before committing.
