# Graphify integration discovery

Audit date: 2026-09-16. The versions in `UPSTREAM_LOCK.txt` are authoritative.
Every source inspection below followed a query against that repository's existing
`upstreams/<component>/graphify-out/graph.json`. No upstream source, graph,
reflection, or cache was modified; no index was rebuilt. The Graphify skill's
write-back steps are intentionally omitted because upstreams are read-only.

Queries used `--budget 600`, with 400-token follow-ups. Vocabulary was checked
against graph node labels in memory before querying. Broad results were narrowed
to a discovered symbol and/or `--context call`, without increasing the budget.
Graphify 0.9.62 can exceed its requested budget when emitting all edges for a
small complete subgraph; this is a tool limitation, not an expanded discovery
budget. Only identified source ranges were read. These are **selected integration
seams**, not claims that live provider/model integration has been validated.

| Component / graph | Implementation question and queries actually run | Discovered symbols and inspected files | Money-owned seam and reason |
| --- | --- | --- | --- |
| TradingAgents / `upstreams/tradingagents/graphify-out/graph.json` | How can native specialist research consume only frozen Money evidence? `propagate`; `TradingAgentsGraph`; `propagate --context call` | `TradingAgentsGraph`, `propagate`, `_run_graph`: `tradingagents/graph/trading_graph.py:79`, `:404`, `:509`; `GraphSetup`: `tradingagents/graph/setup.py:45` | `TradingAgentsAdapter` assembles the native specialist graph with Money snapshot tools supplied through `GraphSetup.tool_nodes`. Pass only mandate/objective evidence. Convert its first-pass state to a Money report. |
| ai-hedge-fund / `upstreams/ai-hedge-fund/graphify-out/graph.json` | Can investment philosophies use Money facts without the native data provider? `fundamentals agent state`; `LLMAgent`; `LLMAgent --context call`; `Price FinancialMetrics CompanyFacts --context call` | `LLMAgent.predict`, `build_snapshot`: `hedge_fund/signals/llm_agent.py:39`; `DataClient`: `hedge_fund/data/protocol.py:34`; `FundamentalsSnapshot`, `build_snapshot`: `hedge_fund/features/snapshot.py:52`, `:118`; `Price`, `FinancialMetrics`, `CompanyFacts`: `hedge_fund/data/models.py:19`, `:36`, `:154` | `AIHedgeFundAdapter` supplies a snapshot-backed `DataClient` to selected native `LLMAgent` specialisms. This checkout uses the `hedge_fund` v2 package, not the older `src/agents` layout. Provider conversion stays within Money. |
| Qlib / `upstreams/qlib/graphify-out/graph.json` | What numeric dataset/model interface supports independent inference? `predict dataset`; `DatasetH`; `DatasetH --context call` | `Model.predict`: `qlib/model/base.py:63`; `DatasetH`: `qlib/data/dataset/__init__.py:72`; graph also identifies `DataHandlerLP` | `QlibAdapter` converts snapshot-only numeric features to a dataset/handler and invokes an approved model's `predict(dataset, segment)`. Return model version, feature provenance, score/rank, uncertainty, and validation metadata. Training belongs offline. |
| LEAN / `upstreams/lean/graphify-out/graph.json` | What structured result can a fixed validation runner return? `backtest result`; `BacktestResultPacket`; `BacktestResultPacket --context call` | `BacktestResultPacket`, `BacktestResult`: `Common/Packets/BacktestResultPacket.cs:29`, `:204`; `AlgorithmPerformance`: `Common/Statistics/AlgorithmPerformance.cs:25` | `LeanAdapter -> LeanRunner` accepts a Money validation specification and parses allowlisted results into `LeanValidationReport`. Keep .NET, historical simulation, and runtime configuration behind the container boundary. |
| CrewAI / `upstreams/crewai/graphify-out/graph.json` | How can auditors receive explicit context, restricted tools, structured findings, and conditional work? `kickoff task`; `TaskOutput`; `TaskOutput --context call`; `ConditionalTask --context call` | `Task`: `lib/crewai/src/crewai/task.py:120`; `TaskOutput`: `lib/crewai/src/crewai/tasks/task_output.py:15`; `ConditionalTask`: `lib/crewai/src/crewai/tasks/conditional_task.py:14` | `CrewAIAuditAdapter` constructs selected native tasks after Money verifies the persisted report barrier. Set explicit task context, allowlisted read-only evidence tools, Money `output_pydantic`, and bounded retries. Conditional tasks activate specialist audits only when applicable. |
| TA-Lib / `upstreams/ta-lib-python/graphify-out/graph.json` | Which deterministic API exposes technical feature calculations? `rsi atr`; `rsi atr --context call`; `get_functions --context call` | Generated ATR/RSI wrappers identified but not read; `Function`: `talib/abstract.py:13`; `get_functions`: `talib/__init__.py:387` | Money technical feature adapter uses an allowlist of native functions over validated OHLCV arrays. Features inform one discovery channel; they never determine the final research state. |
| stream-read-xbrl / `upstreams/stream-read-xbrl/graphify-out/graph.json` | Which parser accepts a Companies House archive while retaining its source? `stream read xbrl`; `stream_read_xbrl_sync --context call`; `stream_read_xbrl_zip --context call` | `stream_read_xbrl_zip`: `stream_read_xbrl.py:619`; `stream_read_xbrl_sync`: `:667`; `_COLUMNS`: `:34` | `UKXBRLAdapter` supplies bounded archive byte streams and the source URL to `stream_read_xbrl_zip`, then maps rows into canonical Money financial evidence. Ingestion belongs in compute, not Netlify. |
| QuantStats / `upstreams/quantstats/graphify-out/graph.json` | Which deterministic functions summarize research outcomes? `sharpe drawdown`; `sharpe --context call`; `max_drawdown --context call` | `sharpe`: `quantstats/stats.py:841`; `max_drawdown`: `quantstats/stats.py:2451` | An outcome adapter passes clean, timestamped research return series with explicit frequency/risk-free assumptions. Persist metrics by component and discovery channel separately from any manually entered actual trades. |
| RD-Agent / `upstreams/rdagent/graphify-out/graph.json` | What offline Qlib factor artifact should Money evaluate before promotion? `experiment factor`; `QlibFactorExperiment --context call` | `QlibFactorExperiment`, `QlibFactorScenario`: `rdagent/scenarios/qlib/experiment/factor_experiment.py:19`; `FactorTask`: `rdagent/components/coder/factor_coder/factor.py:20` | An administrative offline artifact importer records factor formulation/code, experiment provenance, and hashes. A separate promotion gate requires PIT, walk-forward, out-of-sample, and regression results; experiments cannot promote themselves. |
| yfinance / `upstreams/yfinance/graphify-out/graph.json` | What historical price interface needs explicit normalization and fallback labeling? `history ticker`; `PriceHistory --context call` | `PriceHistory.history`: `yfinance/scrapers/history.py:105`; `TickerBase.history`: `yfinance/base.py:128` | A development/fallback `MarketDataAdapter` calls explicit date bounds and adjustment settings and emits provider provenance/coverage. Production critical evidence must have an independently configured production source. |

## Integration constraints discovered

- TradingAgents' top-level constructor creates directories, installs global data
  configuration, and creates memory; `propagate` resolves pending memory and
  `_run_graph` writes state/decisions. Do not invoke it with shared cross-firm
  memory or unrestricted native network tools. Money-owned tool dispatch must
  match the native specialists' tool schemas. The adapter assembly still needs
  runtime contract tests before live activation.
- ai-hedge-fund accepts an injected LLM client and prompt cache. Data-layer
  failures propagate, while model/parse failures can return `metadata.abstained`.
  Treat abstention as missing evidence, never as a neutral successful report.
  Its data contract specifically requires public filing availability by the
  decision date, not just the accounting period end. Its default snapshot
  builder assumes quarter-spaced TTM rows when annualizing book-value growth;
  UK annual or semiannual records must not be relabeled as quarterly data. Its
  documented latest-sector PIT approximation is unacceptable for strict Money
  historical snapshots: supply the metadata frozen at the historical cutoff.
- Qlib's dataset constructor accepts handler configuration capable of resolving
  classes. Accept only Money-configured approved artifacts and handlers; never
  deserialize public client-supplied models or arbitrary class configurations.
  Separate feature and label columns and prevent future labels entering inference.
- LEAN packets include run dates, progress, IDs, rolling windows, and performance
  statistics. Completion alone is not `PASS`. Money must validate PIT, market
  coverage, costs/spread/slippage, sample size, stress and sensitivity evidence.
  Backtest simulation is isolated from live brokers; no brokerage adapter is
  exposed through Money.
- CrewAI's task context defaults to `NOT_SPECIFIED`; explicitly supply allowed
  context instead of relying on implicit aggregation. `TaskOutput.tool_failures`
  can expose incomplete verification and must fail the corresponding audit.
  A `ConditionalTask` cannot be first/only in a crew. Money enforces the durable
  first-pass barrier before constructing any final audit context.
- `stream_read_xbrl_zip` uses a process pool and materializes each archive entry;
  bound archive/file sizes and worker resources. An archive date or accounting
  period is not proof of public filing availability. Attach filing publication
  evidence separately before admitting records into PIT-sensitive analysis.
- TA-Lib requires warm-up handling and finite numeric arrays. Missing indicators
  must remain unavailable; they are not zeros. QuantStats defaults to 252 periods
  per year, so the outcome adapter must state its calendar/frequency and reject
  insufficient or non-finite inputs.
- yfinance defaults to automatically adjusted OHLC and uses an exclusive end
  date. Keep raw/adjusted series distinguishable, normalize GBP/GBX explicitly,
  and surface missing/error data. Its `raise_errors` argument is deprecated in
  this pinned checkout; do not copy outdated integration examples.
- RD-Agent experiments carry executable factor code and create workspaces. Keep
  them offline and isolated, with no public arbitrary-code endpoint. Promotion
  must record an independent approval and validation artifact hashes.

## Foundation status (historical baseline; superseded by build audit below)

This audit establishes source-informed adapter boundaries for the first vertical
slice. `src/money/adapters/upstream.py` now implements validated injected runners
for TradingAgents, AI-HF, Qlib, and LEAN. Unconfigured runners raise
`UpstreamUnavailable`; configured research reports must match the snapshot hash,
firm, upstream pin, and cited evidence. Fixture reports cannot enter these native
boundaries. Qlib receives only normalized numeric OHLCV through a separate
`QuantResearchInput`; `NativeQlibRunner` invokes an explicitly supplied approved
model's `predict(dataset, segment="test")`.

The AI-HF snapshot client maps price, news, and frozen company metadata to native
models loaded only when requested. Financial/TTM, insider, and earnings datasets
fail explicitly because the current contracts cannot represent their native
semantics faithfully. It does not yet enable the default native fundamentals
workflow. CrewAI graph construction, TradingAgents snapshot tool assembly, live
provider coverage, licensed UK data, and real LEAN validation remain activation
gates; injected interfaces are not proof of live engine execution.

`tests/unit/test_upstream_adapters.py` checks malformed reports, snapshot identity,
unsupported data, numeric-only Qlib inputs, and the native inference seam without
loading heavy research dependencies. `tests/unit/test_research_independence.py`
checks parallel input isolation, persisted report seals, cross-connection lock
visibility, prevention of early audit, failure of one firm, and original-source
deduplication across different providers. The application fixture remains
explicitly synthetic and cannot satisfy production research gates.

OpenBB and ixbrl-parse remain optional references and were not inspected or
integrated. Trading 212 eligibility requires separately verified ISA-specific
coverage; a generic instrument listing alone does not establish ISA eligibility.

## Production-build integration audit — 2026-09-16

The following queries used the existing component graphs at the paths above,
normally `--budget 600`, narrowed to exact discovered symbols. Money queries also
located queue/security/deployment/discovery seams before local source inspection.
No unchanged upstream graph was rebuilt. Source fingerprints read package bytes
for hashing only, not a recursive source dump into model context.

| Question / actual scoped query | Important symbols / minimal files inspected | Implemented Money-owned decision |
| --- | --- | --- |
| TA snapshot-only assembly: `GraphSetup setup_graph analyst tool nodes llm memory`; `create_market_analyst create_research_manager create_portfolio_manager Propagator`; `create_bull_researcher create_bear_researcher create_neutral_debator` | `graph/setup.py`, `propagation.py`, `conditional_logic.py`; `agents/analysts/market_analyst.py`, `managers/research_manager.py`, `managers/portfolio_manager.py`, `researchers/bull_researcher.py`, `risk_mgmt/neutral_debator.py`, `utils/structured.py` | Native research/risk factories assembled without native transaction-planning nodes; Money inference binding supplies immutable evidence, no native data tools or shared peer memory |
| AI-HF overridden evidence lifecycle: `LLMAgent DataClient create_snapshot analyze`; `BuffettAgent LynchAgent DamodaranAgent PromptCache LLMClient` | `signals/llm_agent.py`, `signals/buffett.py`, `features/snapshot.py`, `llm/client.py` | Native `predict`/persona machinery with UK Money facts and private cache; no US-TTM relabelling, numeric confidence or native data client |
| Qlib static numeric inference: `DatasetH DataHandlerLP StaticDataLoader Model predict LinearModel` | `qlib/contrib/model/linear.py`, `data/dataset/loader.py`, `handler.py`, `__init__.py` | Native LinearModel over in-memory PIT frame and approved JSON coefficients; no pickle, arbitrary class lookup, live data provider or invented cross-sectional rank |
| LEAN fixed offline consumer: `config algorithm-type-name algorithm-location live-mode data-folder results-destination`; `CustomDataAlgorithm PythonData GetSource Reader` | Targeted `Launcher/config.json` and `Algorithm.Python/CustomDataRegressionAlgorithm.py` | Fixed Money PythonData study in isolated OCI boundary; no order/portfolio operations; stable economic/source/publication dataset identity |
| CrewAI real structured flow: `Flow start listen Agent Task output_pydantic Crew kickoff` | `flow/flow.py`; targeted `agent/core.py`, `task.py`, `crew.py`, `llms/base_llm.py` | Actual Flow + conditional tasks, independent deterministic auditors, structured Red Team, provider-neutral bridge |
| Remove optional platform-auth/filesystem escape: `TokenManager get_auth_token token_manager`; `TraceBatchManager get_auth_token`; `lock_store FileLockStore locks_dir` | `lib/crewai-core/src/crewai_core/auth/token.py`, `__init__.py`, `lock_store.py`; `events/listeners/tracing/trace_batch_manager.py`; installed counterparts only to reconcile traceback/source mismatch | No platform token lookup, dotenv disabled, private temporary locks/storage; production source fingerprint mismatch rejected |
| TA-Lib numeric seam: `Function RSI MACD ATR` | `talib/abstract.py` selected declaration; existing discovered native function API | Actual RSI/MACD/EMA/SMA/ATR/ADX/ROC and explicit volume/trend/breakout policy, warmup and finite checks |
| UK streaming facts: `stream_read_xbrl_zip _COLUMNS _xbrl_to_rows` | `stream_read_xbrl.py` column declaration and parser/zip seam | Bounded archive/XML prevalidation and selected GBP/company facts; historical publication proof stays mandatory |
| Money multi-channel composition: `discover_snapshot union_candidates` | `scanner/discovery.py`, `scanner/technical.py`, `adapters/native_qlib.py` relevant methods | Objective Qlib screening privately reused by its firm; catalyst title triggers and financial deltas replace evidence-presence triggers |

Real native execution qualification is still blocked by absent/mismatched pinned
packages, paid credentials, approved datasets/models, LEAN image and host controls.
Scripted native-lifecycle tests are explicitly test/demo evidence, not live runs.
Full per-workstream command evidence is in WORK_NATIVE.md and VERIFICATION.md.

The earlier production-build Money-only `graphify update .` refreshed the AST graph to 1512 nodes, 4348
edges and 106 communities without an LLM call. Document semantic extraction was
not run. The existing skill/package warning and community-label suggestions were
left untouched; no upstream/global skill refresh was performed.

## Deployment/live-acceptance follow-up — 2026-09-16

Existing graphs were queried before upstream source inspection. The five critical
firms retain their original pinned seams; this follow-up adds downstream native
correspondence, not a replacement first-pass organisation. No upstream code,
graphs or pins were edited.

| Exact question / query and graph | Symbols / minimal inspection | Money integration decision |
| --- | --- | --- |
| Money deployment/control boundaries: `How do Netlify root routing, LiveSnapshotBuilder, ResearchRuntime, and token budgets preserve deployment and sealed research boundaries?` / Money graph, 600 tokens | `ResearchRuntime`, `LiveSnapshotBuilder`, `ResearchStore`, deployment tests, token budget manager; only relevant local methods | Preserve existing monorepo/SSR topology; add HTTP acceptance, claimed-store binding and immutable post-lock paid-call journal |
| TradingAgents own-report challenge: `create_research_manager create_bear_researcher`, narrowed `get_instrument_context_from_state opponent_argument_or_opening` / TradingAgents graph, 600 then 400 tokens | `agents/researchers/bear_researcher.py` role body and `agents/utils/agent_utils.py` lines 176–218 | Actual native bear factory, frozen permitted evidence, closed inference; no peer repository or uncontrolled instrument fetch |
| AI-HF own-report challenge: `LLMAgent get_system_prompt build_snapshot predict _parse` / AI-HF graph, 600 tokens | `signals/llm_agent.py` lines 35–155 and `signals/buffett.py` lines 1–110 | Actual persona/predict lifecycle with Money response contract, private cache and inert data client |
| Qlib approved inference recheck: `DatasetH LinearModel predict training validation feature version` / Qlib graph, 600 tokens | `DatasetH`, `LinearModel`, `DataHandlerLP`; no additional upstream source read | Preserve pinned static numeric inference; deterministic challenge verification recalculates approved model inputs/score without qualitative peer opinions |
| LEAN fixed reader recheck: `CustomDataRegressionAlgorithm PythonData GetSource Reader` / LEAN graph, 600 tokens | `PythonData`, custom-data reader symbols including Python example; no additional upstream source read | Retain fixed isolated order-free study; correspondence cannot convert missing methodology or failed validation into a PASS |
| Independent CIO verifier: `Agent Task output_pydantic kickoff` / CrewAI graph, 600 tokens | Agent/Task/Crew symbols; reused earlier minimal native source seam | Actual separate source-verification task with strict allowed citations, exact quotation/hash proof and measured usage; deterministic adverse checks precede inference |
| Filing retrieval/provenance: `CompaniesHouseProvider SafeFetcher XBRL document publication availability` / Money graph, 650 tokens | `uk/live.py`, `uk/xbrl.py`, `data/security.py`, identity/qualification contracts | Authoritative document API transport, exact reviewed redirect hosts, credential separation and retrieval-only availability |
| Bounded XBRL conversion: `stream_read_xbrl parse stream_read_xbrl` / stream-read-xbrl graph, 600 tokens | `_xbrl_to_rows`, `_parse`, `stream_read_xbrl_zip`; upstream `stream_read_xbrl.py` lines 74–102 only | Keep Money single-member conversion and explicit content-bound GBP units proof; no native process-pool fanout or historical publication inference |

WORK_NATIVE.md and WORK_FILINGS.md record the official documentation, exact
capabilities, tests and outstanding real-runtime qualification. Final Money-only
AST refresh statistics are consolidated in VERIFICATION.md.

Follow-up Money UI query `DataValue RecordData workspace evidence` (600 tokens)
and `ResearchDetail DataValue tabs workspace research detail` (500 tokens) located
the existing primitives and detail tabs for the separately expandable
cross-examination view. No native research dependency enters the frontend.

The XBRL closure hashes only the exact already-discovered standalone module,
after verifying checkout SHA `b95b48bbf50727648cebcba56634b17dc9e60ad3` against
UPSTREAM_LOCK.txt. Its SHA-256 is
`afff17f5a281e2474dcf11d6c8d407c08522cd144d7d075631e6e09c6ebc8b33`.
Money now attests that bounded file before import rather than recursively hashing
site-packages. This source identity check is not qualification of live data.

The Netlify incident was diagnosed from the supplied production log and the
user's `plugins: []` / `available_functions: []` API findings, not a guessed
framework rewrite. Official Netlify documentation and minimal installed published
adapter/CLI artifact contracts informed the dependency-free build guard; exact
package files/version assumptions are listed in
`apps/web/netlify/plugins/money-ssr-guard/README.md`. No upstream repository was
edited or newly cloned for this investigation.

Final follow-up `graphify update .`: **1868 nodes, 5477 edges, 135 communities**.
The AST-only update required no API key/LLM. Graphify retained a curated backup;
its pre-existing installed skill/package warning and optional label-refresh
suggestion do not authorize global or upstream changes.
# Commercial product extension (2026-09-16)

- Question: where do private sessions become scoped research-store capabilities?
  Graph: Money `graphify-out/graph.json`; query vocabulary `session workspace
  settings store` (700 tokens), then narrowed auth/API lookups. Symbols:
  `create_app`, `Settings`, `ResearchStore.for_workspace`, `create_job`.
  Inspected only Money API/settings/store/models and web session/proxy seams.
  Decision: keep queue/report/worker contracts; add backend-verified account
  Principal and a second customer-session credential behind the web service token.
- Question: where should commercial quota admission and immutable Git reference
  data attach? Existing `create_job` transaction and token reservation seam.
  Decision: transactional admission callback after idempotency checks; reference
  catalog loaded read-only at startup and from explicitly mounted wheel assets.
- No upstream source or graphs were changed for this commercial extension.
# Release security investigation — 2026-09-16

- Question: does Money's CIO require Chroma persistence, and where can unused capabilities be denied without replacing CrewAI?
- Graphs/queries: Money `CrewAINativeRunner NativeProcessPolicy` (650 tokens); pinned CrewAI `ChromaDBClient KnowledgeStorage LanceDBStorage` (700 tokens).
- Minimal upstream inspection: `knowledge/storage/knowledge_storage.py:1–100`, `rag/chromadb/client.py:1–100`; upstreams unchanged.
- Seam/decision: keep native CrewAI Flow and memory-disabled tasks; Money-owned subprocess boundary denies listener/backend/client/embedding capabilities. Separate research dependency extra from API/email/billing. Containment is not advisory remediation; research audit remains failing.

- Accounting question: where do budget reservations, settlements and native calls connect? Money query `budget token usage reserve provider model` (700 tokens) identified `TokenBudgetManager`, `HTTPInference`, `BoundedNativeRunner`, `ProviderCircuit.call`, `run_first_pass`, `run_research`, `LiveCorrespondence.invoke`. Only those Money files inspected; no upstream source needed. Fenced accounting stays outside firm capabilities, with strict content-free receipts transported to the parent process.

- Offboarding question: how does deletion connect to billing, sessions and workspace ownership? Money queries `account deletion subscription session workspace` then `request_deletion` (700 tokens each) identified `AccountService.request_deletion` and `ProductService.subscription`. Minimal Money accounts service/models/API/email worker and product billing/worker inspected; extended those services plus migration0006, no upstream read. Final Money-only AST update:2609 nodes/7909 edges/152 communities.

## Customer company lookup — 2026-09-16

- Question: which reviewed identities can customers search before paid research?
  Money queries `InstrumentMetadata Trading212EligibilityService` (600 tokens),
  narrowed to `LiveManifest`, `eligibility_failures`, `create_app` and web request/
  proxy seams. Inspected Money contracts, identifiers, manifest loader, reference
  licence gate, API and request form only. Decision: reuse the immutable reviewed
  manifest; search is not a vendor directory or new mutable reference database.
- Question: where can worker failures be diagnosed without leaking provider data?
  Money query `SafeJsonFormatter classify_failure` (500 tokens), minimal worker,
  first-pass boundary and formatter inspection. Add bounded package-relative source
  location/type only; no exception text, locals or source expressions.
- No new upstream source inspection, graph rebuild, clone or upstream change.

## Personal live R&D — 2026-09-16

- Question: bounded yfinance search/history/news seam without uncontrolled upstream
  tools. Existing `upstreams/yfinance/graphify-out/graph.json`, query for Search,
  Ticker/history/news at650tokens; narrowed to exact nodes. Inspected only
  `yfinance/search.py:31–85`, `scrapers/history.py:105–185`,
  `base.py:275–295,591–655,808–816`. Pinned SHA
  `3d9d2f0cacb662bff689874cd6113bae3a30a885`. Money-owned `rnd_market.py` isolates
  genuine native requests with hard subprocess deadlines and sanitized child env;
  explicitly disables auto/back adjustment and repair, retains action metadata.
  No upstream modification or graph refresh. [Official provider documentation](https://ranaroussi.github.io/yfinance/)
  identifies personal use; no commercial qualification is inferred.
- Question: official source reuse without inventing ISA identity. Money query
  `SafeFetcher CompaniesHouse EvidenceRecord --budget700`, inspected only Money
  security/providers/UK live/resilience/identifiers/contracts and durable limiter.
  `rnd_official.py` reuses bounded fetch/CH credential transport and a reviewed
  company-number seam; SEC exact ticker→CIK, BoE/ONS/FRED preserve revisions/PIT limits.
- Question: can R&D feed the existing actual native organisations unchanged?
  Money query `native research adapter runtime first pass Money snapshot mandate`,
  narrowed to NativeInference, snapshot_payload, TradingAgentsNativeRunner and
  AIHedgeFundNativeRunner. Inspected Money `native.py`, `native_qualitative.py`,
  `native_process.py`, adapter/LEAN/CIO contracts only. Findings: USD and unverified
  public history cannot be coerced into GBP/PIT-qualified commercial contracts.
  Preserve those gates; record unavailable components instead of fabricated reports.
- Web navigation query: `ResearchDetail InstrumentPicker auth` at700tokens,
  inspect only Money request/proxy/result presentation. Separate personal evidence
  view, not a production signal or falsely locked first-pass barrier.

## Qualified GBP/GBX ISA mandate follow-up — 2026-09-16

- Question: where are hard eligibility, live catalogue and publication gates?
  Existing Money graph; scoped `eligibility universe mandate stretch provider live`
  query (750-token budget), then signal/DecisionPacket query (650). Important seams:
  Trading212EligibilityService, InstrumentCatalogue, consensus, generate_signal,
  Store publication/tenant filters. Inspected those Money modules and outcome/API
  contracts. Decision: strengthen existing gates and add a read-only objective
  projection after publication, not a parallel research engine.
- Question: can current reviewed ISA coverage be exposed without claiming a complete
  broker feed? Existing Money graph, Trading212EligibilityService/InstrumentCatalogue/
  eligibility/ISA-universe query (650). Inspected Money eligibility, identifiers,
  live manifest and instrument search. New ReviewedIsaUniverse retains proof hashes
  and expiry; public broker metadata is not taken as proof of current ISA availability.
- Question: which UI and proxy seams can show the new objective safely? Existing Money
  graph and targeted workspace/proxy inspection; existing catch-all SSR navigation,
  backend path allowlist and instrument parser reused. New views preserve historical
  R&D records separately and never manufacture an opportunity or total broker count.
- Question: is CrewAI's vulnerable vector dependency optional? Existing CrewAI graph
  queried before investigation; no upstream source inspection or changes were needed.
  Inspected Money dependency lock, subprocess restrictions and installed package
  metadata. CrewAI 1.15.21 requires chromadb~=1.1.0; no safe patched compatible release
  established. Preserve audit failure and isolation; do not suppress advisories or
  replace pinned source attestation to accept the locally mismatched installation.
- No upstream edits, clones or graph rebuilds; only Money's AST graph is updated.

## Production startup / qualification follow-up — 2026-09-16

- Money graph vocabulary was extracted first; queried `live manifest eligibility
  universe provider snapshot runtime` (750 tokens), then provider qualification /
  required dataset relationships (500). Scoped inspection covered Settings, live
  assembly, manifest validator, Docker/Railway config and qualification contracts.
- Exact upstream question: which pinned native execution seam and source/dependency
  attestation applies to qualification? Queried existing TradingAgents, AI-Hedge-Fund,
  Qlib, LEAN and CrewAI graphs at 500 tokens each. Only extra upstream source inspected:
  CrewAI `lib/crewai/src/crewai/rag/chromadb/client.py:1–90` and the Chroma dependency
  declaration in `lib/crewai/pyproject.toml`. No upstream files changed.
- Actual CrewAI import with Chroma blocked fails at `crewai.flow.flow`; disabling
  memory is therefore not removal. Local containment tests are not host-egress or
  dependency-remediation qualification. Pinned checkout SHAs still match lock files,
  but installed native runtime attestation has not passed.
- Official Trading 212 documentation was inspected separately from upstream source;
  instrument catalog metadata does not establish the required current ISA/buy-status
  proof. No undocumented endpoint or fake automatic eligibility adapter was added.

## CI continuation — 2026-09-17

- Queried Money graph using existing vocabulary `migration postgres schema claim
  immutable` (700 tokens), followed by focused metrics/store/migration inspection.
  The concrete defect was separate server-bound JSON key expressions in metrics
  SELECT/GROUP BY; one shared expression preserves PostgreSQL grouping semantics.
- Web graph lookup narrowed authentication/session/origin seams. Actual installed
  NextRequest and compiled handler tests exposed loopback hostname normalization;
  only smoke origins changed, not application security. No upstream repository
  source was inspected or modified. Money's AST graph was refreshed afterward.
