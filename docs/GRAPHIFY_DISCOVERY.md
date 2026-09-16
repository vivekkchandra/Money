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

Final Money-only `graphify update .` refreshed the AST graph to 1512 nodes, 4348
edges and 106 communities without an LLM call. Document semantic extraction was
not run. The existing skill/package warning and community-label suggestions were
left untouched; no upstream/global skill refresh was performed.
