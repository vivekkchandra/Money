# Production qualification runner

From the repository root, run this single command. Railway injects the existing
Money service environment into a **local process**; it does not run inside the
Railway worker or make Railway's private PostgreSQL hostname reachable on a Mac.

```bash
railway run --service Money --environment production uv run python scripts/build_live_qualification.py
```

No keys are copied into configuration, passed as command arguments, printed, or
written to evidence. The runner does not change production settings, create a
Railway project/database, trade, or contact broker account/order endpoints. It
does make bounded requests to the explicitly selected inference provider (paid
OpenAI by default) and read-only data-provider requests. Explicit
manual model approval can authorize registry writes in the existing database.

For local Ollama functionality evidence, select the separate configuration;
do not change the production/OpenAI file:

```bash
MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/build_live_qualification.py
```

`MONEY_INFERENCE_CONFIG` takes precedence over the default
`data/configuration/live-inference.json`; an invalid explicit file is an error,
not a fallback. Local inference needs no credential and cannot qualify a hosted
production manifest. **LOCAL OLLAMA != RAILWAY PRODUCTION INFERENCE.** See the
[local Ollama guide](OLLAMA_INFERENCE.md) for exact service/model/probe commands
and the review/native/egress gates that remain in force.

## Resuming

By default, public inference resumes `data/qualified/live/` and a selected local
endpoint resumes `data/qualified/local-inference/`. `--output` explicitly selects
another qualification directory. It creates missing templates,
preserves operator edits, validates linked bytes and freshness again, and reuses
applicable observations/training/runtime receipts. Changed inputs invalidate
dependent work. A failed/stale observation cannot become a PASS through caching.
Only one process can operate on a qualification directory at a time.

`status.json` contains the current exact blockers. `inputs/` and `reviews/` contain
non-secret discovered facts, unresolved fields, instructions and contract schemas.
`outputs/` contains measured results; `artifacts/` contains actual content-addressed
evidence. `state/` contains resume checkpoints, not qualification authority.

Fill only review facts you can independently verify and attach their original
non-secret evidence. Leave unknowns unresolved. Then run the **same command**.
You do not write a manifest or calculate proof hashes yourself.

| Input | Required operator decision/evidence |
| --- | --- |
| `inputs/instruments/*.json` | Fresh independent ISA availability, buy availability, ethical activities, exact identifiers and provider/company mappings. Broker metadata is only discovered fact. |
| `inputs/instrument-evidence/` (generated per-candidate path) | Observed spread, applicable costs, corporate-action coverage and any genuine archived evidence. Use the exact path in `status.json`. |
| `inputs/supplemental-sources.json` | Independently reviewed source identity, actual source bytes, dataset coverage and rights for supplied spread/archive records. Historical observations require original-publication evidence. |
| `inputs/provider-rights/*.json` | Actual permitted use, storage and redistribution terms plus the rights evidence bytes. API success is not a licence. |
| `inputs/financial-documents.json` | Actual accounts filing selection, accounting-currency/content-hash review, approved storage hosts and document rights. Filing history alone never qualifies financial extraction. |
| `reviews/inference.json` | Independent approval of the exact selected three-role inference configuration and sufficient bounded native invocation budgets. A local inference probe never supplies that approval or permits a local production manifest. Optional GBP prices need pricing/FX evidence. |
| `reviews/qlib-inputs.json` | Genuine archived evidence, historical universe/actions, explicit training cutoff and predeclared walk-forward/OOS acceptance criteria. |
| `reviews/qlib-source-*.json` | Independent source/PIT/untouched-holdout review bound to the actual unpromoted training result. |
| `reviews/qlib-approval-*.json` | Independent approval of all actual validation hashes and explicit manual promotion. A failed study stays failed. |
| `reviews/lean-inputs.json` and `reviews/lean-*.json` | Real digest-pinned LEAN image, historical eligibility/survivorship/action audits, observed cost/slippage and predeclared study. Approval must precede the frozen snapshot; never backdate it. |
| `reviews/native-egress.json` | Actual target Linux worker enforcement and independent policy/probe evidence, bound to its current network namespace and exact native runtime. |
| `reviews/release.json` | Final independent approval of the generated `outputs/release-inputs.json` hash, with real approval bytes. |

Review templates intentionally contain `null`/`UNRESOLVED` values. These are **not**
proof artifacts or placeholder manifest values. JSON schemas describe exact
contracts. The runner only creates eligibility/ethical proof hashes once it has
actual independently reviewed bytes and a matching current broker row.

## Automated work and boundaries

The runner discovers the current Trading 212 STOCK/GBP/GBX catalogue and joins it
to fresh reviews, reuses the `qualify_providers.py` probe/admission functions,
tests each exact inference selection, and inspects native source/dependency/security
status. EODHD must actually supply OHLCV, corporate actions and news. Companies
House company/filing access and machine-readable financial conversion are separate
observations. An empty event feed cannot attest a parser's successful conversion.

For Ollama the model catalogue and three final-content `OK` responses establish
only local endpoint functionality. The probe does not incorporate private
reasoning into reports. It cannot discharge reviewed selection, target worker,
OS egress, native-engine or release-approval requirements. Qlib and LEAN do not
become LLM workflows when the selected inference provider changes.

It freezes genuine admitted evidence using the same `LiveSnapshotBuilder` and
market-quality checks as production. Only provider circuit-breaker state uses an
ephemeral in-memory database during snapshot construction; this is neither the
production queue nor a model registry and does not establish database acceptance.
Current retrieval timestamps are not historical publication/PIT evidence.

For Qlib, `archive_path` is JSON mapping canonical ticker to original
`EvidenceRecord[]`. With `dataset_path` left `null`, the runner derives the numeric
dataset from those archived bars. A supplied dataset is also checked by
reconstructing every observation from the actual feature/label bars. The runner executes pinned native training,
keeps its first artifact unpromoted, evaluates numeric walk-forward/OOS/regression
criteria, requests independent review, calls `ModelRegistry.register()`, and only
calls manual promotion when explicitly approved. It rechecks active registry state
on resume and does not silently reactivate a withdrawn model.

Native execution preserves the independent TradingAgents and AI-Hedge-Fund
workflows and numeric Qlib first pass. Only their sealed reports go to LEAN, then
the real central CrewAI CIO evidence/contradiction assessment and Red Team. This
is not a majority vote. Existing bounded cross-examination controls remain intact.

Missing/incompatible native dependencies, failed source attestation, missing
security coverage, or ChromaDB advisories remain explicit blockers. Warnings are
not silenced to declare a runtime safe. See [native build constraints](NATIVE_WORKER_BUILD.md).

A Mac with Railway-injected credentials is **not** an enforced worker sandbox.
Native qualification must ultimately execute in the actual target Linux namespace
with independently reviewed, readable, enforced nftables egress policy. Uploaded PASS
booleans or Python socket hooks cannot establish that. Similarly, registry access
requires connectivity to the existing migrated PostgreSQL; the runner does not
create a replacement or automatically change production database networking.

## Completion

Once every component and final review passes, the runner inventories/rechecks
every artifact SHA256, assembles the exact current `LiveManifest`, stages it,
calls `load_manifest`, and only then publishes `manifest.json`. It invokes:

```bash
uv run python scripts/validate_live.py --manifest data/qualified/live/manifest.json --sha256 <actual-sha>
MONEY_RUN_PRODUCTION_INTEGRATION=1 uv run pytest tests/production -q
```

The second command includes an aggregate-only pytest reporting plugin internally.
Required legacy test credential aliases exist only in child-process memory. No
raw test report/traceback is persisted. All tests must actually execute and pass:
zero tests, skips, xfails, deselections and collection failures are blockers.

Success prints `QUALIFICATION COMPLETE` and the real `MONEY_LIVE_MANIFEST` /
`MONEY_LIVE_MANIFEST_SHA256` assignments. Otherwise exit status 2 prints
`QUALIFICATION BLOCKED` and exact review/infrastructure actions. A pre-existing
manifest is not evidence of success on a failed rerun; always check current status
and revalidate freshness before deploying.

Qualification completion does **not** claim hosted readiness. Deployment, worker
heartbeat, durable queue and restart-surviving hosted end-to-end acceptance are
separate. For SaaS, the existing commercial reference-catalogue rights gate must
also pass; this runner never automatically grants commercial/redistribution rights.

Generated operator inputs/checkpoints are gitignored. Even secret-free proof bytes
can contain licensed/confidential material: review the final bundle before any
commit or publication. No generated fixture is a production qualification artifact.
