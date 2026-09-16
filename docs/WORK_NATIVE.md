# Native research integration work — 2026-09-16

Status: **PRODUCTION BLOCKED**. Native execution seams are implemented; live
provider/model/engine/host qualification is not claimed. No upstream was edited,
cloned, upgraded or reindexed. Parent implementation merges these findings into
the canonical implementation and verification documents.

## Implemented

| Component | Actual implementation | Remaining qualification |
| --- | --- | --- |
| TradingAgents | Native technical/fundamental/news/sentiment, bull/bear, research manager and three risk specialists; Money inference bridge replaces native tool binding; no native transaction-planning node is assembled; strict evidence-cited final Money report | Pinned native package/dependencies absent; paid native run and host egress qualification not performed |
| AI-HF | Executes pinned `LLMAgent.predict` and Buffett/Lynch persona machinery against Money snapshot override, private in-memory prompt cache and strict cited output; no native Financial Datasets/TTM access or numeric confidence | Actual native lifecycle tested with scripted inference; paid provider execution not performed |
| Qlib | Native `LinearModel.predict(DatasetH(...))` using a static in-memory DataHandlerLP; fixed versioned PIT numeric features; no pickle, expression data provider, fitting during inference or invented cross-sectional rank | Qlib runtime absent; no independently promoted production model, qualified OOS dataset, or cross-sectional universe |
| LEAN | Fixed order-free PythonData event consumer in a digest-pinned non-root OCI image, network none, read-only mounts/root, CPU/memory/PID limits, timeout cleanup; delayed-publication exclusions; held-out non-overlapping cases, chronological folds, two slow-trend regimes, MAE/MFE/drawdown/threshold observations, spread/slippage/dated costs and six parameter sensitivities | Docker engine/image and true historical eligibility/survivorship/corporate-action qualification artifacts unavailable; current retrieval timestamps cannot establish old historical availability |
| CrewAI | Actual native Flow + conditional Agent/Task crews + structured outputs; deterministic relative-volume/cash comparisons; independent Qlib model/feature recalculation when registry artifact supplied; strict citation/claim coverage; adversarial Red Team; no tools, delegation, code execution, shared memory or platform authentication | Installed CrewAI source differs from pinned checkout despite matching nominal version; live mode rejects it; paid full audit and independent review qualification pending |
| Cross-examination | Immutable hash-sealed challenge/response/verification artifacts; locked three-firm barrier and completed audit required; at most two actual rounds; native TradingAgents bear-role and AI-HF Buffett lifecycle responses receive only their own sealed report; independent CrewAI source checks and deterministic Qlib/numeric rechecks; explicitly permitted citations, actual invocation usage and frozen source quotations retained | Live feature remains opt-in; pinned native runtime, paid response/verification qualification and production budget/host controls still required; absent evidence/responders remain unresolved |
| Isolation | Spawned process receives only runner + frozen contracts, removes unrelated environment credentials, disables dotenv, uses private cwd/storage/locks, bounds IPC output, terminates/kills on timeout; deny-by-default Python filesystem/database/process/network capabilities except configured inference gateway; preserves bounded retryable provider codes | OS/container network policy still required: Python audit hooks do not sandbox arbitrary native machine code |
| Source pins | SHA256 manifest of sorted Python package file hashes is compared against actual installed source before importing native engines; changed/missing packages rejected | Binary dependencies require separately locked qualified runtime images; no package installation performed |

`NativeRunSettings.verify_source_pin=False` is an explicit test seam: resulting
qualitative/CIO reports are marked `demo` and rejected by live adapters. It is not
a production escape hatch. No model/provider is switched within a research run.
Unknown inference cost remains `None`/unknown.

Qualitative context uses explicit policy `money-qualitative-latest80-v1`: all
non-price evidence plus exactly the latest 80 available price bars (or all bars
when fewer exist), sorted deterministically. The untrusted evidence envelope
preserves the original immutable snapshot hash and declares original/selected/
omitted record and bar counts, selected evidence IDs, and the historical coverage
limitation. Every record in the **full** snapshot must pass PIT/conflict checks
before selection, including omitted archive bars. Qualitative report citations
and native CIO findings are restricted to context-admitted evidence IDs; the
independent deterministic CIO calculations, Qlib and LEAN still receive full
numeric history. Documents are never silently discarded to fit: retained evidence
over the unchanged input budget fails explicitly. No inference budget or default
was increased. Tests cover a 1,500-bar archive, exact latest-80 admission, retained
critical documents, original snapshot immutability, citations to omitted bars,
hidden historical PIT/conflict failures and oversized retained documents.

## Verification evidence

| Requirement | Status | Evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Existing adapter regressions plus new native checks | VERIFIED | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q tests/unit/test_native_qualification.py tests/unit/test_upstream_adapters.py tests/production/test_native_live.py`: 74 passed, 5 explicit opt-in skips | Local Python 3.12 | No live qualification implied |
| Real AI-HF persona lifecycle with snapshot override | VERIFIED | Native pinned checkout's `predict` executed with scripted test inference; no native data client capability | Local read-only checkout | Live inference credential absent |
| Real CrewAI Flow/task lifecycle | VERIFIED | Actual installed CrewAI ran both directly and inside spawned capability boundary with scripted test inference | Local Python 3.12 | Installed source does not equal pinned source; test output is explicitly demo |
| Process crash/deadline/secret/provider/filesystem isolation | VERIFIED | Tests prove deadline termination, parent secret removal, forbidden DNS/file access and transient retry preservation | Local spawned subprocess | Host OS egress policy unqualified |
| Source manifest integrity | VERIFIED | All four manifests match current pinned read-only checkouts; modified package is rejected | Local filesystem | TradingAgents/AI-HF/Qlib not installed in base environment; CrewAI installed source mismatch |
| Qlib inference/model qualification contracts | VERIFIED | Tampered hash, symlink, missing manual promotion, future validation and duplicate feature timestamps rejected | Unit tests | Native Qlib execution and approved model unavailable |
| LEAN PIT and order-free study | VERIFIED | Tests prove same-bar ambiguity resolves conservatively, costs are included, current retrieval cannot backdate historical availability, and fixed algorithm has no order/portfolio operations | Unit tests of study/runner configuration | Real engine/image unexecuted |
| Stable LEAN historical dataset qualification | VERIFIED | Snapshot/evidence-ID rebinding, new retrieval/freshness and record order preserve economic dataset hash; raw price/currency/volume/source/publication/PIT changes invalidate it | Unit tests | Authoritative historical publication archive still required |
| Bounded cross-examination and sealed originals | VERIFIED | Tests reject pre-lock/incomplete-audit access, self-certification, more than two rounds, replacement of sealed claims and rehashed attempts to hide unresolved challenges | Unit tests | Optional native challenge responders not qualified |
| Explicit bounded qualitative context | VERIFIED | Latest native/upstream suite: 79 passed; full-snapshot checks before latest-80 selection, context-only citations and retained-document overflow tested; Ruff/mypy clean | Local Python 3.12 | Production provider/runtime qualification unchanged |
| Native correspondence, independent verification and durable regressions | VERIFIED | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q tests/unit/test_native_correspondence.py tests/unit/test_native_qualification.py tests/unit/test_upstream_adapters.py tests/integration/test_durable_research.py tests/integration/test_signal_persistence.py`: 118 passed, 28 existing CrewAI/Starlette warnings | Local Python 3.12; inference is explicitly scripted in tests | No paid execution or native source qualification implied |
| Five live native tests | BLOCKED_CREDENTIAL | `tests/production/test_native_live.py` collects five genuine execution checks; each currently skips with `SKIPPED_MISSING_CREDENTIAL` | Local | Explicit paid opt-in and live inputs absent |

The focused source Ruff/mypy checks pass after the recorded implementation fixes.
CrewAI's installed library emits 26 deprecation warnings in direct scripted tests;
they are not hidden as a successful production qualification.

## Runtime and qualification inputs

Root factory can assemble:

```python
TradingAgentsAdapter(BoundedNativeRunner(
    TradingAgentsNativeRunner(inference, settings), TradingAgentsResearchReport, process_policy))
AIHedgeFundAdapter(BoundedNativeRunner(
    AIHedgeFundNativeRunner(inference, settings), AIHedgeFundResearchReport, process_policy))
QlibAdapter(BoundedNativeRunner(QlibNativeRunner(qualified_model), QlibQuantResearchReport, offline_policy))
LeanAdapter(LeanContainerRunner(container_settings, costs, study_parameters, qualification))
CrewAICioAdapter(BoundedNativeRunner(
    CrewAINativeRunner(inference, settings, qualified_model), CIOResult, process_policy))
```

Each qualitative/CIO runner needs its own inference instance. Only the exact
gateway hostname(s) belong in `NativeProcessPolicy.gateway_hosts`. Parent-side
budget reservations/database handles must never be passed into the child.
Persist `CrewAICioAdapter.last_result` as the `cio_runtime` artifact for actual
provider, model, prompt version, upstream SHA, token usage and unknown/known cost.

LEAN `LeanCostAssumptions` fields: `version`, `source`, `effective_from`,
`effective_to`, `round_trip_cost_bps`, `spread_bps`, `slippage_bps`,
`applicability_reasons`. Cost coverage must include the full historical interval.
`LeanContainerSettings.image` must be a digest, not a mutable tag. The prebuilt
image must expose `/Lean/Launcher/bin/Release/QuantConnect.Lean.Launcher.dll`,
Python.NET and `/Lean/Data`, and carry label
`org.money.lean.sha=f9107abdf26121c5ce159f561bd27fead01d30e1`.
No arbitrary algorithm or command is accepted from API callers.

`historical_dataset_hash(records)` in `money.backtest.lean` seals stable economic
history and original publication/source identities, excluding job-specific IDs,
retrieval and freshness. Independent archive qualification should use this helper,
not the enclosing snapshot/evidence hash. It never grants earlier availability.

`run_cross_examination(snapshot, reports, lean, audit, first_pass_locked=True)` in
`money.crews.cross_examination` produces the immutable downstream artifact. Persist
it before consensus; bind `snapshot_hash`, `report_hashes`, `lean_hash` and
`initial_audit_hash` to the already-sealed inputs. The packet's `hash` is the
canonical content hash excluding itself. Use `len(rounds)` and OR its
`material_disagreement` into final consensus. Default unavailable responders do
not fabricate agreement. Supplying responders additionally requires a separate
independent evidence verifier; an agent cannot self-certify its response.

The concrete optional capabilities in `money.crews.native_cross_examination` are:

```python
NativeFirmChallengeRunner(firm, inference, settings=None)
# (snapshot, own_report, challenge, round_number) -> ChallengeResponse
DeterministicChallengeResponder(qualified_model, lean)
# Same signature; only Qlib/LEAN, no peer reports and no inference calls.
CrewAIChallengeVerifier(inference, settings, qualified_model, reports, lean)
# (snapshot, challenge, response) -> ChallengeVerification
```

Network-bearing capabilities must be wrapped in `BoundedNativeRunner` with the
corresponding output contract. Each invocation needs a fresh inference instance:
TradingAgents permits at most two calls, AI-HF one, independent CrewAI verification
one. The coordinator permits at most 24 challenges and two rounds; production
admission must reserve that worst-case budget before enabling the optional feature.
Root-owned durable correspondence stores per-invocation input hashes, usage and
reservation identities; these native capabilities do not receive database handles.

Challenge permission is the explicit union of the initial finding's evidence IDs
and the sealed original claim's IDs. Respondents, corrected claims, verifier
findings and frozen source checks cannot expand it; an empty set grants nothing.
Qualitative outputs must additionally stay within the latest-80 context policy.
An independent numeric check requiring additional evidence stays unsupported
unless that evidence was already permitted. Optional metadata is omitted when
absent, preserving hashes of legacy artifacts. Live packets reject demo traces.

The separate verifier first recalculates deterministic measurements, preserving
contradictions and missing controls. Only complete narrowly supported numeric
assertions can be resolved through arithmetic. Native CrewAI source verification
must return exact quotations present in admitted frozen records; the original
unchanged claim must itself equal a checked factual quotation. Broader inference,
future outcomes and agent agreement cannot be marked independently verified.
Quoted facts and source hashes are persisted with each exchange. A frozen LEAN
failure cannot be repaired by correspondence. Every verifier invocation emits
metadata, including deterministic short-circuits with explicit zero calls/tokens
and unknown cost, so the control plane need not infer accounting from absence.

Tests cover native AI-HF lifecycle and actual installed CrewAI Agent/Task execution
with scripted inference (explicitly demo), TradingAgents role-contract dispatch,
own-report-only capability input, immutable originals, two-round bounds, exact
citation permission, fabricated/markup-only quotations, unsupported complete-claim
verification, adverse deterministic vetoes, and subprocess trace transport.
Actual read-only checkout tests skip `SKIPPED_EXTERNAL_UNAVAILABLE` when the
checkout is absent; ordinary contract tests do not require upstream checkouts.

Opt-in genuine execution tests use:

- `MONEY_RUN_PRODUCTION_INTEGRATION=1` (authorizes paid inference).
- `MONEY_NATIVE_QUALIFICATION_SNAPSHOT`: bounded Money snapshot JSON with actual evidence.
- `MONEY_NATIVE_INFERENCE_CONFIG`: `InferenceConfiguration` JSON excluding its key.
- `MONEY_NATIVE_INFERENCE_API_KEY`: corresponding inference credential.
- `MONEY_QLIB_QUALIFIED_MODEL` and `MONEY_QLIB_ARTIFACT_HASH`: independently promoted JSON model/registry hash.
- `MONEY_NATIVE_QUALIFICATION_REPORTS`: real sealed report tuple JSON for downstream execution.
- `MONEY_LEAN_QUALIFICATION_CONFIG`: container, versioned costs, parameters and qualified dataset controls.
- `MONEY_NATIVE_QUALIFICATION_LEAN_REPORT`: real LEAN output for standalone CIO qualification.

Missing credentials/configuration and absent runtimes are explicit skips. Genuine
execution/schema violations are failures. A passing native smoke is insufficient
to declare the entire production acceptance gate passed.

## Graphify audit

All queries used existing upstream `graphify-out/graph.json`, initially 600 tokens,
then 500–600 tokens for narrower seams. No upstream graph was rebuilt. Graphify
warned of a pre-existing installed skill/package version mismatch.

| Question / query | Important symbols and exact files inspected | Money seam / decision |
| --- | --- | --- |
| `GraphSetup setup_graph analyst tool nodes llm memory` then `create_market_analyst create_research_manager create_portfolio_manager Propagator` | `tradingagents/graph/setup.py`, `graph/propagation.py`, `graph/conditional_logic.py`, `agents/__init__.py`, `agents/analysts/market_analyst.py`, `agents/managers/research_manager.py`, `agents/managers/portfolio_manager.py`, `agents/utils/structured.py` | GraphSetup includes native transaction-planning nodes; assemble the useful exact native research factories in Money instead, replacing tool binding with frozen facts |
| `create_bull_researcher create_bear_researcher create_neutral_debator` | `agents/researchers/bull_researcher.py`, `agents/risk_mgmt/neutral_debator.py` | Firm-private debate/risk state; one bounded round, no cross-firm memory |
| `LLMAgent DataClient create_snapshot analyze`, narrowed `BuffettAgent LynchAgent DamodaranAgent PromptCache LLMClient` | `hedge_fund/signals/llm_agent.py`, `features/snapshot.py`, `signals/buffett.py`, `llm/client.py` | Native overridable snapshot/render/predict lifecycle; Money strict reports replace native confidence/US-TTM assumptions |
| `DatasetH DataHandlerLP StaticDataLoader Model predict LinearModel` | `qlib/contrib/model/linear.py`, `qlib/data/dataset/loader.py`, `qlib/data/dataset/__init__.py`, `qlib/data/dataset/handler.py` | Exact native linear model with Money-owned static PIT frame and immutable JSON coefficients |
| `config algorithm-type-name algorithm-location live-mode data-folder results-destination`, narrowed `CustomDataAlgorithm PythonData GetSource Reader` | `Launcher/config.json` targeted sections; `Algorithm.Python/CustomDataRegressionAlgorithm.py` custom data reader seam | Fixed Money PythonData event consumer, offline engine config, no copied execution operations |
| `Flow start listen Agent Task output_pydantic Crew kickoff` | `lib/crewai/src/crewai/flow/flow.py`, selected declarations in `agent/core.py`, `task.py`, `crew.py`, `llms/base_llm.py` | Real Flow and structured tasks with no tools/delegation/memory and Money-owned provider adapter |
| `TokenManager get_auth_token token_manager`, `TraceBatchManager get_auth_token`, `lock_store FileLockStore locks_dir` | `lib/crewai-core/src/crewai_core/auth/token.py`, `crewai_core/__init__.py`, `crewai_core/lock_store.py`, `lib/crewai/src/crewai/events/listeners/tracing/trace_batch_manager.py`; installed equivalents only for traceback/version reconciliation | Disable optional Platform token lookup without reading user tokens; private per-process temp locks and dotenv disabled |
| Correspondence: `create_research_manager create_bear_researcher` (600 tokens), narrowed `get_instrument_context_from_state opponent_argument_or_opening` (400 tokens) | `tradingagents/agents/researchers/bear_researcher.py` role body; `agents/utils/agent_utils.py` lines 176–218 | Actual bear-role factory with closed Money inference bridge; bare ticker state does not fetch metadata; respondent receives only its own report and explicit permitted facts |
| Correspondence: `LLMAgent get_system_prompt build_snapshot predict _parse` (600 tokens) | `hedge_fund/signals/llm_agent.py` lines 35–155; `signals/buffett.py` lines 1–110 | Actual Buffett persona/native predict lifecycle with Money snapshot builder and strict response parser, private prompt cache and inert data client |
| Correspondence: `Agent Task output_pydantic kickoff` (600 tokens) | Graph confirms Agent/Task/Crew symbols; reused already-inspected Money/native seam rather than reading more upstream source | Real structured independent source verifier, max one inference call; [official CrewAI task documentation](https://docs.crewai.com/en/concepts/tasks) consulted for output and retry contract |

Source-manifest generation read hashes, not source contents into model context;
the complete Python package trees were hashed solely to verify pinned identity.

## Remaining engineering

- Qualified pinned native runtime image/dependency lock; actual TradingAgents and
  Qlib execution contract qualification (packages absent locally).
- Real paid inference/evidence/provider-escape qualification with the deployed
  inference gateway and host egress policy.
- A trained, independently validated and manually promoted Qlib baseline with
  authoritative PIT training/out-of-sample data; optional cross-sectional ranking.
- Actual LEAN image execution and independent archived validation of eligibility,
  survivorship, corporate-action adjustment policy and predeclared study suitability.
- A broader library of deterministic auditors; unrecognized qualitative claims
  use source-grounded CIO review and cannot gain unsupported numerical precision.
- Paid qualification of native firm challenge responses and independent source
  verification in the pinned runtime, with deployed budgets/egress controls.
  Capabilities and fail-closed tests are implemented; optional runtime enablement
  is not itself production qualification. Within-firm debate is never counted as
  independent evidence.
