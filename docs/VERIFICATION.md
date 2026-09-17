# CI blocker fixes — 2026-09-17

**PRODUCTION BLOCKED.** Continuing from the same unpublished tree and main
`3ab2ff9`. Railway/Netlify discovery still reports DISABLED_BY_ADMIN; Git metadata
remains read-only. No deploy, migration, model promotion or proof bundle was made.

| Requirement | Status | Actual evidence | Remaining acceptance |
| --- | --- | --- | --- |
| Login smoke regression | VERIFIED | Actual built Next16.3.5 handler + real FastAPI/TestClient/migrated SQLite transport bridge: loopback-IP request normalizes to localhost, mismatched Origin returns403 with zero backend calls; canonical localhost returns200 and durable session creation | Not actual HTTP/browser transport or hosted auth |
| Harness fix | VERIFIED | Both private/commercial (including browser variants) share canonical localhost web origin; explicit127.0.0.1 listener bindings and production CSRF/auth unchanged;12 new real-NextRequest regression cases | Full HTTP commands remain listener EPERM; new GitHub CI needed |
| PostgreSQL aggregation fix | VERIFIED | Actual metrics query previously compiled independent payload_1/payload_2 parameters in SELECT/GROUP BY. Regression failed before and passes after reusing one expression;3 focused metrics tests pass | Actual PostgreSQL regression skipped without TEST_DATABASE_URL; hosted failure not yet proven resolved |
| Frozen migration comparison | VERIFIED | All45 frozen tables match application columns/types/FKs/indexes under PostgreSQL dialect compilation | Offline comparison is not schema-drift or migration acceptance against PostgreSQL |
| Python suite | VERIFIED |1133 passed/0failed/157skipped,28warnings; uv sync --locked, ruff and mypy110files pass | Unit888/0/0, integration245/0/146, production0/0/11; skipped checks are not acceptance |
| Web suite | VERIFIED |315 passed/0failed/0skipped; lint/typecheck/build pass | Local test evidence only |
| Current published CI | FAILED | Fresh GitHub read: latest35156964337 remains failed;35156964352 public web passed. No artifacts; detailed annotation endpoint unavailable | These local fixes have not been published/retested in CI |
| Safety boundaries | VERIFIED | Settings/live mode, eligibility, qualification, billing/auth policies and reference-table bytes unchanged; deployment/data-table checks pass | Genuine live qualification and hosted E2E remain outstanding |

The grouping fix follows PostgreSQL's requirement that selected non-aggregate
expressions match grouped expressions; distinct server parameters are not the
same expression even when their supplied values match. See the
[official GROUP BY contract](https://www.postgresql.org/docs/current/sql-select.html#SQL-GROUPBY).
It is a concrete query defect, not a claim that the unavailable CI traceback has
been recovered. The live ISA-source/native/data/security blockers below remain.

## Earlier Railway production qualification incident — 2026-09-16

**PRODUCTION BLOCKED.** The reported Settings rejection was not bypassed. No genuine
live bundle was available or manufactured; no live provider, model, eligibility,
runtime or hosted result was falsely qualified. Starting local/tracking main was
clean at `3ab2ff93c6dfe5b06de70872803edd02fbb72e98`; this follow-up remains local.

| Requirement | Status | Actual command / evidence | Environment / blocker |
| --- | --- | --- | --- |
| Preserve production gate | VERIFIED | Settings and validate_live.py unchanged; no demo/R&D mode enabled, no qualification files created | Source/diff review |
| Locked install / lint / types | VERIFIED | uv sync --locked; ruff check .; mypy src/money:110 source files | Local Python3.12 |
| Complete Python regression | VERIFIED | pytest -q --junitxml=/private/tmp/money-qualified-final.xml: **1132 passed, 0 failed, 157 skipped**,28 warnings | Unit888/0/0; integration244/0/146; production0/0/11 (passed/failed/skipped). PG and live skips are not acceptance |
| Manifest hardening | VERIFIED | Bounded descriptor-relative reads, all path components no-follow, regular-file/size/hash checks, cyclic-parent safe errors; required EODHD/CH datasets | Synthetic unit bytes only, not production artifacts |
| Deployment preflight | VERIFIED | `uv run python -m money.research.preflight --role worker`: secret-safe blocked JSON and intentional exit 2 | Local missing configuration; not a health or qualification probe |
| Research image selection | VERIFIED | Railway worker chooses explicit Dockerfile.research; parity/locked-install/non-root/private-worker tests; CI builds that exact file | Offline contracts only; native package attestation still unqualified |
| Production integrations | BLOCKED_CREDENTIAL | `MONEY_RUN_PRODUCTION_INTEGRATION=1 uv run pytest tests/production -q`: **0 passed, 0 failed, 11 skipped**, each QUALIFICATION_MANIFEST_REQUIRED | No genuine manifest/hash; no paid call or live qualification |
| Current ISA universe | NOT_IMPLEMENTED | Actual runtime still uses manifest instruments; documented broker metadata lacks verified current ISA/buy-status semantics | Approved current source/coverage/usage contract required; GBP/GBX counts unknown |
| EODHD / Companies House | BLOCKED_CREDENTIAL | No available EODHD_API_KEY/COMPANIES_HOUSE_API_KEY or qualified provider bundle | Prices/actions/news/filings not live tested |
| Native firms / Qlib / LEAN | BLOCKED_EXTERNAL_INFRA | Pinned checkout SHAs match locks; TradingAgents/AI-HF/pyqlib absent, installed CrewAI attestation fails | No genuine inference configuration, promoted model, LEAN study or host-egress proof |
| Native boundary regression | VERIFIED | 106 native/correspondence/dependency tests passed during investigation; scripted inference only | Not real paid firm qualification, CIO acceptance or live report locking |
| Latest public Netlify | VERIFIED | [Live-web run 35156964352](https://github.com/vivekkchandra/Money/actions/runs/35156964352): root 200, routes/assets/headers/auth closure, served SHA 3ab2ff9 | Actual existing site; not signed-in research or this follow-up deployment |
| Latest broader CI | FAILED | [Run 35156964337](https://github.com/vivekkchandra/Money/actions/runs/35156964337): PG test, backend drill, LOGIN-stage HTTP smoke, research dependency audit fail | No raw traceback available; safe diagnostics improved, failures not claimed fixed |
| PostgreSQL / restore | BLOCKED_EXTERNAL_INFRA | Local isolated drill fails postgres_init with ENVIRONMENT_PERMISSION_DENIED; TEST_DATABASE_URL absent | No remote migration/current/schema/recovery/restore acceptance |
| Railway | BLOCKED_EXTERNAL_INFRA | Fresh plugin lookup: DISABLED_BY_ADMIN/NOT_AVAILABLE. Owner reports Postgres ONLINE and Money CRASHED; not independently verified | No API/worker/variables/migration mutation or alternate access route |
| Web regression | VERIFIED | npm ci, lint, typecheck, **303 passed / 0 failed / 0 skipped**, Next.js production build | Local only |
| Commercial HTTP/browser E2E | BLOCKED_EXTERNAL_INFRA | Both test:commercial commands fail at allocate local listeners: EPERM 127.0.0.1 | Zero journey steps accepted |
| Node advisory retrieval | BLOCKED_EXTERNAL_INFRA | npm audit --omit=dev: registry.npmjs.org ENOTFOUND | Previous 3ab2ff9 CI Node audit passes; fresh local audit not obtained |
| Python advisory retrieval | BLOCKED_EXTERNAL_INFRA | Locked research export succeeds; pip-audit 2.10.1 retrieval fails pypi.org DNS | Previous 3ab2ff9 research CI reports four ChromaDB advisories |
| ChromaDB remediation | FAILED | Installed1.1.1; two Critical/two High advisories remain. Real import with Chroma blocked fails CrewAI Flow import | Memory-disabled is not dependency removal; containment is not patch or host qualification |
| Docker execution | BLOCKED_EXTERNAL_INFRA | Both root and explicit research image builds fail Docker socket permission; Compose validation passes | No newly built/running image acceptance |
| Git data assets | VERIFIED | validate_data_tables.py: four schema1/data1.0.0 tables validate; checksums unchanged | No data/upstream/optional_reference diff |
| Deployment boundary | VERIFIED | check_deployment.py passes | No broker execution, heavy work stays outside Netlify |
| Publication | BLOCKED_EXTERNAL_INFRA | Git metadata explicitly read-only; no index.lock exists | No commit/push of this follow-up; existing main/tracking SHA retained |
| Graphify | VERIFIED | Scoped Money/upstream queries, then AST-only graphify update:3103 nodes/9511 edges/165 communities | No upstream modifications/rebuilds |

No live manifest path or SHA can be reported: `data/qualified/live/manifest.json`
does not exist. All requested proof categories remain unqualified, rather than
being represented by placeholder bytes. The current broker-universe replacement,
PG/HTTP failure fixes and native/security qualification remain engineering work,
not merely configuration or paperwork. No currently qualified candidate is claimed.

## Previous qualified live GBP/GBX ISA verification — historical

**PRODUCTION BLOCKED.** This is the current mandate. Preserve `live_rnd` as a
separate experiment, but production acceptance requires qualified `live`, current
ISA/ethical evidence and GBP/GBX individual stocks. No live research result or
eligible-universe count was fabricated. New work in this section is local,
unpublished; the previously published baseline is `cade435e0a58a754d15cf92c58173082c597acf3`.

| Requirement | Status | Actual command/test and evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Preserve published foundation | VERIFIED | Clean initial main, local/tracking/GitHub SHA `cade435`; previous commercial and R&D work retained | Git/read-only GitHub | This follow-up still needs publication |
| Public Netlify baseline | VERIFIED | [Live-web run 35153377355](https://github.com/vivekkchandra/Money/actions/runs/35153377355), job 104986898151 at 21:38:15 UTC: root HTTP 200; routes/assets/headers/auth closure passed; checker enforces `/api/health` web SHA equals `cade435` | Actual existing public site | Not a signed-in research journey or deployment of this follow-up |
| Final local public HTTP probe | BLOCKED_EXTERNAL_INFRA | `curl --connect-timeout 5 --max-time 15 ... https://neon-griffin-08e616.netlify.app/`: exit 6, hostname unresolved, HTTP 000 (no response) | Restricted local shell | Cannot independently repeat hosted baseline acceptance from this shell |
| Remote deployment management | BLOCKED_EXTERNAL_INFRA | Plugin discovery reports Railway and Netlify DISABLED_BY_ADMIN / NOT_AVAILABLE; no alternate access route attempted | Current session | Approved connector access must be enabled by administrator |
| Python install/lint/types | VERIFIED | `uv sync --locked` 191 resolved/188 checked; `ruff check .`; mypy source + ISA acceptance script: 110 files | Local | None |
| Full Python regression | VERIFIED | `uv run pytest -q --junitxml=/private/tmp/money-gbp-final.xml`: **1097 passed, 0 failed, 157 skipped**, 28 warnings | Local | Skips excluded from acceptance |
| Python breakdown | VERIFIED | Unit **853/0/0**; integration **244/0/146**; production **0/0/11**, passed/failed/skipped | JUnit | 144 missing PostgreSQL URL plus 2 PG-only variants; 11 explicit live opt-in skips |
| Opt-in production suite | BLOCKED_CREDENTIAL | `MONEY_RUN_PRODUCTION_INTEGRATION=1 uv run pytest tests/production -q`: **0 passed, 0 failed, 11 skipped**, all QUALIFICATION_MANIFEST_REQUIRED | Actual opt-in invocation | `MONEY_LIVE_MANIFEST` and matching hash with genuine qualifications |
| Current ISA acceptance selection | BLOCKED_CREDENTIAL | `check_live_isa.py --select-only` with explicit opt-in exits 2, no candidates, research_execution NOT_RUN | Read-only preflight | No current qualified manifest; no hard-coded acceptance ticker |
| Reviewed ISA service/API | VERIFIED | Local unit/API tests: stock and GBP/GBX hard gates; future/stale eligibility rejected including exact 24-hour expiry; deterministic ethics; source/proof hashes; bounded queries/rate limits | Fixtures/TestClient | Reviewed subset is not a complete broker-universe feed |
| Objective API and arithmetic | VERIFIED | Tests reproduce sealed signal design, first validated target, conservative entry, existing allocation/costs; reject expiry/invalidation/tampering/demo/R&D; tenant isolation and read quota | Fixtures/TestClient | No genuinely qualified live publication available |
| Outcome extension | VERIFIED | T+20 sampled only when available; allocation-based hypothetical GBP price changes persisted, explicitly excluding costs and actual trading assumptions | Fixtures/local persistence | No real production outcome/calibration qualification |
| Web clean install/lint/types/build | VERIFIED | `npm ci`, lint, typecheck, tests and production build; **303 passed, 0 failed, 0 skipped**, 16 files | Local Next.js | Not new hosted/browser acceptance |
| Objective/universe presentation | VERIFIED | Expiry hiding, bounded pagination, polling cancellation, no synthetic candidates, uncalibrated scenarios, reviewed-subset/historical distinction, proxy allowlists | Component/contract tests | Real browser journey unavailable |
| Git-managed data assets | VERIFIED | `scripts/validate_data_tables.py`: all four schema 1/data 1.0.0 assets pass unchanged checksums; no upstream/reference diff | Git assets | None |
| Deployment boundary | VERIFIED | `scripts/check_deployment.py` and regression tests retain SSR, thin proxy, private worker, no broker execution/client secrets | Local static/contracts | Does not prove hosted compute |
| Published real PostgreSQL CI | FAILED | [CI 35153377328](https://github.com/vivekkchandra/Money/actions/runs/35153377328): 1128 cases/1 failure `test_postgres_migration_worker_claims_and_immutability`; backend drill failed phase postgresql_integration_tests / COMMAND_FAILED | GitHub/PostgreSQL | Full trace unavailable; root cause not identified or repaired |
| Published HTTP/browser CI | FAILED | Same run: legacy HTTP smoke failed after lint/types/unit/build; later browser/commercial stages did not execute | GitHub Linux | Requires diagnosis and passing rerun |
| New CI diagnostics | VERIFIED | 30 diagnostics tests; combined diagnostic/deployment/dependency boundary suite 69/0/0; YAML parses | Local | Next authorized CI run needed; no failure suppression |
| Docker | BLOCKED_EXTERNAL_INFRA | Local build denied Docker socket access; Compose parses with disposable validation-only settings. Published baseline CI built default/research images | Local + baseline CI | No running hosted container acceptance |
| HTTP/browser local acceptance | BLOCKED_EXTERNAL_INFRA | Legacy and commercial HTTP/browser commands stop at listener allocation EPERM, before journey execution | Restricted local shell | Listener/browser-capable environment |
| Railway API/PostgreSQL/worker/migrations | BLOCKED_EXTERNAL_INFRA | Owner-selected incredible-flexibility / production / Money + Postgres unchanged, remote state not accessible | Current session | Enable approved access; then verify actual services, TLS, migrations, recovery and restore |
| Research dependency security | FAILED | CrewAI 1.15.21 requires ChromaDB ~=1.1.0; installed 1.1.1 retains four advisories. Current official advisories do not establish a patched compatible version | Lock/installed metadata/primary advisories | Exclusion from control-plane and native capability denial are containment, not remediation |
| Native research execution | BLOCKED_EXTERNAL_INFRA | TradingAgents/AI-HF/Qlib packages unavailable; model/LEAN/inference qualification inputs absent; CrewAI pinned-source attestation fails | Current runtime | Correct attested runtime/model artifacts plus controlled actual inference tests |
| Public data and commercial rights | BLOCKED_CREDENTIAL | No current qualified ISA universe, UK market/filing/news/action manifest or approved production display rights available | Current configuration | Provider credentials/evidence and rights approval, not yfinance substitution |
| New release publication | BLOCKED_EXTERNAL_INFRA | `.git` explicitly read-only; no stale lock deleted, no staging/commit/push attempted through alternate metadata | Current filesystem policy | Git-write-capable authorized session |
| Money knowledge graph | VERIFIED | `graphify update .`: AST-only, 3059 nodes / 9435 edges / 168 communities | Local Money graph | No upstream graph rebuild or model/API calls |

The live universe size, GBP/GBX counts, excluded count and last refresh remain
**unknown**, not zero. The new service exposes only current reviewed manifest
coverage. Trading 212's documented
[instrument metadata endpoint](https://docs.trading212.com/api/instruments/instruments)
does not establish current Stocks & Shares ISA buy availability. No balance,
positions or order endpoint was added.

The objective view is a paginated workspace publication comparison, not a complete
market ranking, portfolio construction engine or calibrated return forecast. No
qualified live candidate was evaluated here: upside/downside/£ contribution and
remaining target gap are not estimated from invented inputs. The fixed aspiration
remains £200 starting capital, £1,000 profit, £1,200 end value, +500%, 30 days.

Current transaction-cost sources were checked without changing risk gates:
[HMRC SDRT manual](https://www.gov.uk/hmrc-internal-manuals/stamp-taxes-shares-manual/stsm031030)
and [Takeover Panel levy](https://www.thetakeoverpanel.org.uk/disclosure/ptm-levy).
Existing versioned applicability/effective-date evidence remains required; the
£200 comparison never assumes every instrument is exempt or extrapolates historical rules.

Unresolved ChromaDB advisories:
[CVE-2026-45829](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c),
[CVE-2026-45833](https://github.com/advisories/GHSA-36p7-vc44-83pf),
[CVE-2026-45830](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr),
[CVE-2026-45831](https://osv.dev/vulnerability/PYSEC-2026-3815).
Published control-plane audit found zero findings across 75 dependency records;
published Node production audit passed its configured threshold. A fresh local
network advisory scan is blocked, not passed. No audit was suppressed.

Remaining engineering, separate from credentials: diagnose and repair real PG/HTTP
CI failures; qualify continuous full-universe discovery/scheduling, full-universe
ranking and native research on genuine inputs; remediate the research dependency
gate; execute hosted auth→queue→firms→validation→result→return, backup/restore and
operational alert acceptance. The current schema remains additive revision 0008;
this follow-up introduces no database migration or upstream change.

## Historical personal live R&D verification — 2026-09-16

**R&D HOSTED ACCEPTANCE BLOCKED (historical).** Commercial licensing was not a
blocker to that personal-use scope. Commercial production gates remain unchanged.
The implementation now supports real public-data evidence studies on the durable
queue, not a complete native multi-firm R&D workflow. No synthetic live result,
new deployment, new project, production migration, commit or push is claimed.

| Requirement | Status | Actual command/evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Locked Python install | VERIFIED | `uv sync --locked`:191 resolved/188 checked | Local Python3.12 | None |
| Ruff / types | VERIFIED | `uv run ruff check .`; mypy src/money + acceptance script:108 source files | Local | None |
| Complete Python suite | VERIFIED | `uv run pytest -q --junitxml=/private/tmp/money-rnd-final.xml`: **978 passed, 0 failed, 150 skipped** | Local | Skips explicitly excluded from acceptance |
| Suite breakdown | VERIFIED | Unit741/0/0; integration237/0/139; production0/0/11 (passed/failed/skipped) | JUnit |137 missing TEST_DATABASE_URL;2 PG-only variants;11 live opt-in skips |
| R&D durable workflow | VERIFIED | New R&D tests:27 passed/17 PG skips; AAPL/USD and BARC/GBp fixture search →202 →worker →sealed snapshot/result →fresh connection; stale-worker fencing; tenant HTTP isolation; cached provenance and free-plan quotas | Test fixtures, SQLite/TestClient | Not actual public equity data or PostgreSQL |
| Web | VERIFIED | lint/typecheck; **256 passed, 0 failed, 0 skipped**; production build | Local Next16.3.5 | Not deployed R&D code |
| Git reference tables | VERIFIED | `scripts/validate_data_tables.py`: four schema1/data1.0.0 tables, unchanged hashes; no upstream/reference changes | Git-owned assets | None |
| Deployment boundary | VERIFIED | `scripts/check_deployment.py`, new per-service Railway config tests, migration0008 dialect/schema tests | Offline | Remote target/config not verified |
| BARC.L / AAPL actual public calls | BLOCKED_EXTERNAL_INFRA | Genuine `check_live_rnd.py --providers-only`,2026-09-16T21:25:45Z: both FAILED/RND_PROVIDER_UNAVAILABLE, exit2; direct Yahoo hostname resolution fails | Actual provider attempts | Yahoo network/DNS access |
| Official macro live probe | FAILED | BoE returned21 real IUDBEDR observations at21:11:19Z; repeated actual probe at21:25:45Z and root follow-up returned PROVIDER_UNAVAILABLE/0 records | Actual BoE attempts | Latest availability is UNAVAILABLE, not persistently READY |
| UK/US official filings | BLOCKED_CREDENTIAL | CH/SEC contracts tested; actual equity workflow did not reach filing collection | Local/source configuration | CH API key + reviewed company numbers; real SEC contact User-Agent |
| ONS/FRED | BLOCKED_CREDENTIAL | Bounded adapters/contract tests present, not live-qualified or default snapshot macro | Local | Pinned ONS series dimensions; optional FRED API key |
| Native multi-firm R&D | NOT_IMPLEMENTED | Existing adapters require commercial GBP/PIT contracts; personal packet marks all five systems NOT_CONFIGURED and INSUFFICIENT_EVIDENCE, no signal | Current R&D path | Safe R&D native evidence/citation/barrier bridge plus actual model/runtime/inference qualification |
| Railway services | BLOCKED_EXTERNAL_INFRA | Owner target incredible-flexibility/production, Money/Postgres; Railway connector unavailable/administrator-disabled, no remote mutation | Restricted session | Enabled approved Railway access; actual IDs, DB/migrations/worker/API health then need verification |
| Netlify current acceptance | BLOCKED_EXTERNAL_INFRA | Final curl root: DNS failure, HTTP000 (no response); no Railway URL configured | Restricted shell | Enabled network/deployment access; previous public web evidence below is older baseline only |
| Docker | BLOCKED_EXTERNAL_INFRA | `docker build -t money-production-check .`: Docker socket permission denied; Compose config passes with disposable validation-only env | Local | Docker runtime access; config validation is not image/run acceptance |
| HTTP/browser E2E | BLOCKED_EXTERNAL_INFRA | Both `test:commercial` and `test:commercial:browser` exit1 before journey: localhost listen EPERM | Local | Listener/browser-capable environment; no journey steps passed |
| Publication | BLOCKED_EXTERNAL_INFRA | main6846bdc preserved; no index.lock exists; .git is read-only in this session; all modified/untracked work retained | Restricted filesystem | Repository metadata write + push access |
| Money Graphify update | VERIFIED | `graphify update .`: AST-only,2904 nodes/8763 edges/154 communities; no upstream rebuild | Local | None |

Actual R&D acceptance outcome:

| Instrument | Price/history | Official filings | Snapshot | Native research |
| --- | --- | --- | --- | --- |
| BARC.L | FAILED: provider unavailable | NOT_RUN; CH configuration missing | NOT_RUN | NOT_RUN |
| AAPL | FAILED: provider unavailable | NOT_RUN; SEC contact missing | NOT_RUN | NOT_RUN |

Remaining engineering is not disguised as a credential issue: the R&D native
organisation bridge and three-firm barrier are still unimplemented. Research-only
CrewAI dependency advisories also remain unresolved; the default personal data
image does not install that extra. SaaS email verification remains intact; an
explicit private personal workspace login is available but not hosted-verified.

## Earlier commercial follow-up (historical, not current R&D acceptance)

# Live-customer release verification — 2026-09-16

**PRODUCTION BLOCKED.** This section supersedes the historical records below.
Local and GitHub `main` were clean/aligned at
`6846bdc8de34bd68fabac616008d0847f390a139` when this follow-up started. The commercial
implementation is published; no stale index lock exists. This follow-up adds
reviewed company search/selection and live admission, plus safe diagnostics and
release-SHA acceptance. No upstream or reference table was replaced.

## Actual remote evidence

| Requirement | Status | Actual evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Published commercial baseline | VERIFIED | GitHub branch API and local log: 6846bdc | GitHub main | Follow-up changes need separate publication |
| Public Money site, routes, assets and security headers | VERIFIED | [Live web run 35146196319](https://github.com/vivekkchandra/Money/actions/runs/35146196319): root HTTP 200; route/asset/auth-closure/security step passed at 20:23:49 UTC | Actual existing production URL, workflow triggered by 6846bdc | Does not prove served SHA or authenticated customer journey |
| Netlify deploy SHA / build logs | BLOCKED_EXTERNAL_INFRA | Correct local site ID; npx status/build/logs fail registry DNS; direct curl has no HTTP response; Netlify connector unavailable | Restricted shell | Approved remote Netlify/network access |
| Python CI | FAILED | [CI 35146196196](https://github.com/vivekkchandra/Money/actions/runs/35146196196): 860 cases, one failure named test_postgres_migration_worker_claims_and_immutability | GitHub real PostgreSQL | Full failure trace unavailable; no guessed repair |
| Backend recovery/restore CI | FAILED | Same run: isolated backend drill failed | GitHub Linux/PostgreSQL | Failure phase not available in accessible job metadata; safe phase summary added for next run |
| Web HTTP/browser CI | FAILED | lint/types/unit/build pass; legacy HTTP smoke fails; later browser/commercial steps skipped | GitHub Linux | Actual HTTP failure diagnosis and green rerun required |
| OCI images | VERIFIED | Same run: default and research image builds, default dependency exclusion and Compose checks pass | GitHub Docker | Not a deployed API/worker/database |
| Dependency audits | FAILED | Control-plane: 75 dependency records, zero findings. Node production high-threshold check passes. Research: five entries/four distinct ChromaDB advisories | GitHub locked release | ChromaDB1.1.1 remains in research image; containment is not remediation |
| Railway deployment access | BLOCKED_EXTERNAL_INFRA | Owner selected Railway. Connector discovery reports DISABLED_BY_ADMIN / NOT_AVAILABLE; installed CLI 5.57.5 whoami and status fail DNS for backboard.railway.com | Restricted session | Administrator must make the approved Railway connection available; no project/services verified or remote writes attempted |
| Managed services | BLOCKED_CREDENTIAL | Railway selected, but no verified Money project, managed DB URL, API URL, SMTP, Stripe, provider or inference credentials available | Presence-only checks; no values exposed | Approved Railway access plus service credentials/licences |

Current shell also denies localhost listeners and Docker socket access. The
isolated PostgreSQL drill returned `BLOCKED_ENVIRONMENT`, phase `postgres_init`,
code `ENVIRONMENT_PERMISSION_DENIED`, zero completed steps; owned temporary data
was removed. Configured local database is loopback/non-qualified and a read-only
probe fails. No unrelated AWS profile was selected or provisioned.

## Current local checks

| Requirement | Status | Actual command/evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Locked Python install | VERIFIED | `uv sync --locked`: 191 resolved, 188 checked (workspace-safe UV cache) | Python3.12 | None |
| Python lint/types | VERIFIED | `uv run ruff check .`; `uv run mypy src/money`: 101 files | Local | None |
| Python unit suite | VERIFIED | JUnit `/private/tmp/money-live-final.xml`: **608 passed / 0 failed / 0 skipped** | Local | Not live qualification |
| Python integration suite | VERIFIED | Same JUnit: **210 passed / 0 failed / 122 skipped** | SQLite/local processes | Real PG variants unavailable |
| All Python tests | VERIFIED | `uv run pytest -q`: **818 passed / 0 failed / 133 skipped**, 28 warnings,95.16s | Local | Explicit skips retained |
| Explicit live integration | BLOCKED_CREDENTIAL | `MONEY_RUN_PRODUCTION_INTEGRATION=1 uv run pytest tests/production -q`: **0 passed / 0 failed / 11 skipped** | Real integration suite opted in | Six missing manifest/hash; five missing native snapshot |
| Separate-process recovery regression | VERIFIED | Eight isolated migrated SQLite DBs, eight actual worker processes, seeds 0/1/2/3/7/19/99/random all COMPLETE | Synthetic local processes | Does not reproduce or repair PG/HTTP CI failures |
| Reference tables and boundaries | VERIFIED | `validate_data_tables.py` validates all four unchanged assets; `check_deployment.py` passes | Local | Provider rights remain unapproved |
| Compose | VERIFIED | `docker compose --profile commercial config --quiet` with disposable validation values | CLI parse only | Not running containers |
| Current Docker build | BLOCKED_EXTERNAL_INFRA | `docker build -t money-production-check .`: Docker socket permission denied | Restricted shell | Current baseline CI image builds passed separately |
| Current Netlify CLI | BLOCKED_EXTERNAL_INFRA | `npx --yes netlify-cli@latest status`, `build`, `logs --source deploy --since 24h`: registry ENOTFOUND | Restricted shell | No remote writes/deploy triggered |
| Current Node audit | BLOCKED_EXTERNAL_INFRA | `npm audit --omit=dev --prefix apps/web`: registry ENOTFOUND | Restricted shell | Baseline 6846bdc CI high-threshold check passed separately |
| Web clean install/lint/types/build | VERIFIED | `npm ci`, lint, typecheck and `npm run build --prefix apps/web` succeed; SSR routes retained | Local Next16.3.5 | Not a new Netlify deploy |
| Web unit/component contracts | VERIFIED | **238 passed / 0 failed / 0 skipped**, 13 files; search selection, stale responses, canonical bounds, exact admission errors, health SHA and deploy mismatch tests | Local Vitest | Real browser acceptance separate |
| Commercial/legacy HTTP and browser | BLOCKED_EXTERNAL_INFRA | All four `test:commercial`, `test:commercial:browser`, `test:smoke`, `test:browser` commands stop at `listen EPERM 127.0.0.1`; zero completed journeys | Restricted shell | Listener/browser permission required; no mocked acceptance substituted |
| Publish this follow-up | BLOCKED_EXTERNAL_INFRA | Reviewed diff and targeted `git add` denied creating `.git/index.lock`; no lock exists, index remains unstaged; process listing denied | Read-only Git metadata | Git-write-capable approved session; changes preserved, no commit/push/deploy of this follow-up |

The Git data checksums are unchanged: exclusions
`34e8d352530a467b7a233e7e0ba248060bcfb1d2db38f0ea30a152814b2f9665`, plans
`58392f17d1d42b40b6e323d3951e1e3206bfcb6e372db14be8072f2272f6e04a`, provider_datasets
`a882a9267ff12a3e1157305b7bcfa5491c597d63b43df7c335f6e131a16f2a30`, provider_licences
`ed2576283633605b02f7f70a84e856dfa269e3f55c85b55e4cb85f3418311edd`.
Each remains schema1/data1.0.0. No customer state is written into these assets.

## Full customer acceptance matrix

VERIFIED below means only the exact stated scope. BLOCKED rows are not passes.

| Requirement | Status | Evidence / remaining release gate |
| --- | --- | --- |
| Public marketing website | VERIFIED | Current production root HTTP 200 and Money route/assets check in linked live-web run |
| Signup | BLOCKED_EXTERNAL_INFRA | Local API tests exist; no deployed commercial API |
| Verification email delivered | BLOCKED_CREDENTIAL | No SMTP credentials/verified sender or actual delivery |
| Login | BLOCKED_EXTERNAL_INFRA | Local verified flow only; hosted API/session acceptance missing |
| Logout | BLOCKED_EXTERNAL_INFRA | Local revocation tests only; hosted acceptance missing |
| Password reset | BLOCKED_CREDENTIAL | Local one-use/reset tests; actual email/hosted reset missing |
| Tenant isolation | BLOCKED_EXTERNAL_INFRA | Local guessed-UUID/forged-workspace tests pass; hosted PG/customer test missing |
| Managed PostgreSQL | BLOCKED_CREDENTIAL | No approved remote TLS database available |
| Production API | BLOCKED_EXTERNAL_INFRA | Railway selected; account/project inaccessible, no verified public API URL |
| Production worker | BLOCKED_EXTERNAL_INFRA | Railway selected; services inaccessible and research runtime unqualified |
| Durable queue | BLOCKED_EXTERNAL_INFRA | Local durable tests pass; PG CI failure unresolved, no hosted queue |
| Company search | BLOCKED_CREDENTIAL | Reviewed-catalogue API/UI implemented; real manifest/rights/eligibility absent |
| Live instrument identity | BLOCKED_CREDENTIAL | Reviewed identifiers required; no qualified real catalogue supplied |
| Live market data | BLOCKED_CREDENTIAL | EODHD adapter; commercial UK entitlement/key and qualification missing |
| Live historical prices | BLOCKED_CREDENTIAL | EODHD/archive path; licensed PIT dataset absent |
| Live fundamentals/filings | BLOCKED_CREDENTIAL | Companies House/document/XBRL path; key/reviewed financial data missing |
| Live news/catalysts | BLOCKED_CREDENTIAL | EODHD adapter; commercial news entitlement/key missing |
| Verified eligibility | BLOCKED_CREDENTIAL | Current ISA-specific availability/proof missing; generic ticker list not accepted |
| Immutable evidence snapshot | BLOCKED_CREDENTIAL | Local hash/PIT/immutability tests; no qualified live evidence snapshot |
| TradingAgents live path | BLOCKED_CREDENTIAL | Pinned adapter, no genuine qualified inference/runtime execution |
| ai-hedge-fund live path | BLOCKED_CREDENTIAL | Pinned adapter, no genuine qualified inference/runtime execution |
| Qlib production model | BLOCKED_CREDENTIAL | No independently validated manually promoted artifact |
| LEAN runtime | BLOCKED_EXTERNAL_INFRA | No qualified isolated image/host/dataset; no real OOS validation |
| CrewAI challenge | FAILED | No live qualification; unresolved research dependency advisories |
| Research persistence | BLOCKED_EXTERNAL_INFRA | Local separate-worker persistence passes; hosted PG acceptance missing |
| Research results UI | BLOCKED_EXTERNAL_INFRA | Local render/contracts; no customer live result/browser acceptance |
| History | BLOCKED_EXTERNAL_INFRA | Local scoped/paginated history; hosted return-later flow missing |
| Watchlist | BLOCKED_EXTERNAL_INFRA | Local persisted/scoped controls and reviewed picker; hosted acceptance missing |
| Account settings | BLOCKED_EXTERNAL_INFRA | Local implementation/tests; hosted authenticated acceptance missing |
| Billing test lifecycle | BLOCKED_CREDENTIAL | Stripe adapter tests are not a real test-account lifecycle |
| Usage limits | BLOCKED_EXTERNAL_INFRA | Local transactional/quota tests; live PG/admission acceptance missing |
| Transactional email | BLOCKED_CREDENTIAL | Encrypted durable outbox tested; actual SMTP delivery missing |
| Monitoring | BLOCKED_CREDENTIAL | Safe logs/metrics/heartbeats; destination and alert delivery unconfigured |
| Backups | BLOCKED_EXTERNAL_INFRA | No production managed backup schedule/retention configured |
| Restore drill | FAILED | Current remote drill fails; local isolated run blocked, not a restore pass |
| Git data tables intact | VERIFIED | Four existing schema1/data1.0.0 assets retained with original checksums |
| Commercial live data rights verified | BLOCKED_CREDENTIAL | Inventory remains UNAPPROVED; no licence invented |
| Public production E2E | BLOCKED_EXTERNAL_INFRA | No signup → delivered email → qualified live research → return-later journey |

## Historical verification before publication

# Final commercial release verification — 2026-09-16

**PRODUCTION BLOCKED.** Preserved `main` at local `be30431`; GitHub's read API
still reports `fc5ddeb`. Commercial changes remain modified/untracked because
`.git/index.lock` creation is denied. No lock exists, nothing was removed, and no
new commit, push, deployment or production migration was performed.

Statuses apply only to the stated environment. Synthetic/TestClient/SQLite success
is not a production customer journey. Earlier records below are historical.

| Requirement | Status | Actual command/test and evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Preserve/publish commercial work | BLOCKED_EXTERNAL_INFRA | Branch/status/log/diff inspected; `git add .` denied creating index.lock; no active lock file; process listing also denied. 78 modified/untracked status entries preserved, index empty | Restricted shell | Git metadata write access; no stale lock repair is applicable |
| Remote branch/CI | FAILED | GitHub API: main fc5ddeb; [CI 35115874795](https://github.com/vivekkchandra/Money/actions/runs/35115874795) fails one PG test, HTTP smoke, backend drill and Chroma audit; previous Docker and Node audit pass | Earlier published commit only | Full logs unavailable through connector; current commercial changes not pushed or CI-qualified |
| Earlier hosted web check | VERIFIED | [Live-web 35115874716](https://github.com/vivekkchandra/Money/actions/runs/35115874716) reports initial HTTP404, followed by successful route/asset/header verification step | Earlier fc5ddeb workflow, not current release | Does not establish current commercial deployment or deployed Netlify SHA |
| Current Netlify build/logs/public HTTP | BLOCKED_EXTERNAL_INFRA | Required `npx --yes netlify-cli@latest status`, `build`, `logs --source deploy --since 24h` fail npm DNS; curl cannot resolve public host, no HTTP status obtained. Connector discovery reports Netlify disabled by administrator | Restricted shell/connectors | Enabled approved Netlify/network access; current public URL acceptance and deploy logs/SHA |
| Netlify configuration | VERIFIED | Local site ID c66f8305-899f-4deb-92c8-b2ee55f5acab; apps/web, npm run build, .next, explicit Next adapter and SSR guard; expanded account/plans/history/login/signup acceptance | Local configuration/tests | No remote configuration mutation claimed |
| Locked dependencies/lint/types | VERIFIED | `uv sync --locked`: 191 resolved/188 checked; `uv run ruff check .`; `uv run mypy src/money`: 100 source files | Local Python3.12 | None for these checks |
| Final Python suite | VERIFIED | `uv run pytest -q --junitxml=/private/tmp/money-release-final.xml`: **752 passed, 0 failed, 108 skipped**, 28 warnings | Local | 95 missing-PG variants, 2 PG-only local variants, 11 live opt-in skips |
| Python suite breakdown | VERIFIED | Unit **567/0/0**; integration **185/0/97**; production **0/0/11** (passed/failed/skipped) | Local JUnit | Skips are not passes |
| Explicit live integration opt-in | BLOCKED_CREDENTIAL | `MONEY_RUN_PRODUCTION_INTEGRATION=1 .venv/bin/python -m pytest tests/production -q`: **0 passed, 0 failed, 11 skipped** with explicit missing manifest/hash or native snapshot reasons | Opted-in live suite | Genuine qualification manifest/hash, snapshot, provider credentials, rights, approved model/runtime |
| Production synthetic prohibition | VERIFIED | Settings reject both demo switches; real enqueue/worker/stored-result boundary tests deny synthetic production research, even mutated settings | Local settings/API/process tests | No live signal claimed |
| Offboarding and authority | VERIFIED | Last-owner succession, fresh password, stale-role billing/research rejection, revocation, durable closure, retry/recovery, approved-policy retention, erased-recipient notification/invitation regression, immutable preservation | SQLite + scripted Stripe; PG variants defined | Real PG concurrency, Stripe lifecycle, approved legal retention and scheduled fulfilment |
| Call/provider accounting | VERIFIED | Migration0007 immutable fenced receipts; per-inference attempts, tokens, estimated vs actual cost; native/provider-operation duration; safe parent transport and bounded admin aggregation | SQLite/native subprocess tests | Killed child may leave unresolved call; dataset operation HTTP subcall counts and provider invoices explicitly unknown |
| Migrations/schema drift | VERIFIED | Frozen0006/0007 schema/type/default/index/constraint/trigger tests for both dialects; actual SQLite upgrade/head/drift checks | SQLite execution + PG compilation | Not real PostgreSQL execution |
| Web clean install/lint/types/tests/build | VERIFIED | `npm ci --prefix apps/web`:385 packages; final lint/typecheck, **184 passed, 0 failed, 0 skipped** in11 files; Next16.3.5 production build with dynamic root/account/product/research routes | Local | Not hosted SSR/browser acceptance |
| Commercial HTTP/browser E2E | BLOCKED_EXTERNAL_INFRA | Both required npm scripts abort at local listener allocation: EPERM127.0.0.1; zero journeys executed | Restricted shell | Listener/browser-capable environment |
| Legacy HTTP/browser smoke | BLOCKED_EXTERNAL_INFRA | Both npm smoke scripts abort at listener allocation; no passing browser claim | Restricted shell | Same environment restriction |
| Git data/reference boundaries | VERIFIED | All4 assets remain tracked, unchanged, schema1/data1.0.0/checksums valid; `scripts/validate_data_tables.py` and `scripts/check_deployment.py` pass | Local files/build | Commercial provider rights remain unapproved, never inferred |
| Default control-plane dependency audit | VERIFIED | Locked no-dev export excludes CrewAI/Chroma; pip-audit2.10.1 reports0 known vulnerabilities; import/export regression tests | Actual advisory scan + dependency graph | Container execution still unverified |
| Research dependency audit | FAILED | Locked research-extra audit:5 entries / **4 distinct ChromaDB1.1.1 advisories**, no fixed version established. Native client/backend/embedding/listener restrictions tested; actual CrewAI Flow still runs with scripted inference | Actual scan + native tests | Containment is not a patch/security qualification; unsuppressed research CI remains failing |
| Node advisory scan | BLOCKED_EXTERNAL_INFRA | `npm audit --omit=dev --prefix apps/web` ENOTFOUND | Restricted shell | Current advisory retrieval; earlier fc5ddeb CI scan is historical only |
| PostgreSQL configuration/probe | BLOCKED_EXTERNAL_INFRA | Existing ignored .env contains loopback PG URL without required TLS, not managed production config. Bounded read-only probe raises OperationalError; no secret printed | Existing local configuration | Reachable isolated/staging and managed production PostgreSQL |
| Backup/restore/restart drill | BLOCKED_EXTERNAL_INFRA | `scripts/backend_acceptance.py`: ENVIRONMENT_PERMISSION_DENIED at postgres_init, zero completed steps, temporary data removed | Isolated local attempt | Shared-memory/listener permissions and actual restore evidence |
| Compose / container images | BLOCKED_EXTERNAL_INFRA | Commercial Compose config validates; default and --target research Docker builds both fail socket permission. Separate API/email/billing default image and explicit research image configured | CLI only | Accessible Docker/OCI runtime; no container readiness claimed |
| SMTP/Stripe/host/monitoring | BLOCKED_CREDENTIAL | Presence-only environment/.env inspection finds no real service URL, SMTP/Stripe/provider/live-manifest credentials. Outbox, closure/reconciliation and safe metrics interfaces tested locally | No production credentials/destination | Verified sender, test billing lifecycle, approved host/database, monitoring and backup scheduling |
| Legal/privacy/provider rights | BLOCKED_CREDENTIAL | Draft pages and Git licence inventory remain unapproved. Policy review and commercial rights are fail-closed | Product assets/configuration | Owner/legal/licensor approval; no invented jurisdictional retention period |
| Money Graphify refresh | VERIFIED | `graphify update .`:2609 nodes,7909 edges,152 communities; AST-only, no semantic API call | Money graph only | Upstream graphs unchanged |

New engineering in this follow-up: reviewed offboarding with automated billing
closure, durable safe per-call accounting, control-plane/research dependency
separation, Chroma capability containment, current-role rechecks, email/erasure
race protection, stricter demo boundaries and expanded deployment checks.
No upstream or Git-managed reference table was modified. Required external
configuration and operator workflow are in COMMERCIAL_DEPLOYMENT.md and ENVIRONMENT.md.

## Previous commercial verification (historical)

**PRODUCTION BLOCKED.** This commercial conversion starts from `main` / `fc5ddeb`.
The initial tree was clean. Versioned product tables were committed as `be30431`;
the commercial code is a separate local change set. No live commercial release,
customer signup, SMTP delivery, payment or research result is claimed. The older
production-only record below is retained as historical evidence, not current status.

## Current commercial acceptance

Allowed statuses: VERIFIED, FAILED, BLOCKED_CREDENTIAL, BLOCKED_EXTERNAL_INFRA,
NOT_IMPLEMENTED. VERIFIED is limited to the explicitly stated environment. A mock
provider transport, synthetic research or SQLite test is not live qualification.

| Requirement | Status | Actual command/test and evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Locked Python installation | VERIFIED | `uv sync --locked`: 191 resolved / 188 checked; direct cryptography dependency uses already locked 50.0.1 | Local Python 3.12 | None for this check |
| Python lint and types | VERIFIED | `uv run ruff check .`; `uv run mypy src/money`: 96 source files | Local | None for this check |
| Python regression suite | VERIFIED | `uv run pytest -q --junitxml=/private/tmp/money-commercial-final-tests.xml`: **684 passed, 0 failed, 75 skipped**, 28 deprecation warnings | Local | 63 missing-PG skips, one PG-only row-lock skip, 11 live opt-in skips |
| Python unit suite | VERIFIED | JUnit breakdown: **536 passed, 0 failed, 0 skipped** | Local | No live claim |
| Python integration suite | VERIFIED | JUnit breakdown: **148 passed, 0 failed, 64 skipped** | SQLite, in-process HTTP and separate processes | Real PostgreSQL variants unexecuted |
| Live production integration suite | BLOCKED_CREDENTIAL | Explicit `MONEY_RUN_PRODUCTION_INTEGRATION=1 .venv/bin/python -m pytest tests/production -q`: **0 passed, 0 failed, 11 skipped** | Live checks opted in | Qualification manifest/hash and genuine native qualification snapshot missing |
| Git-managed reference data | VERIFIED | `uv run python scripts/validate_data_tables.py`: four schema1/data1.0.0 tables VERIFIED; 78 table-specific tests | Local versioned files | External provider commercial rights remain unknown, not approved |
| Installed-wheel deterministic data loading | VERIFIED | Offline wheel build/install into isolated target; unrelated working directory plus explicit `MONEY_REFERENCE_DATA_DIR` validates all four assets | Local installed wheel | Host must mount exact read-only release assets |
| Research-only / client-secret / Netlify boundaries | VERIFIED | `uv run python scripts/check_deployment.py`; adversarial tests retained | Local code/build | Not a hosted penetration test |
| Signup, verification, login, reset, sessions | VERIFIED | Account suite: 27 passed / 0 failed / 1 PG skip; scrypt, one-use expiry, revoke, encrypted outbox and request-size/correlation tests | TestClient + SQLite | Real delivered emails, hosted cookies and PG reset/rotation race execution |
| Tenant and role isolation | VERIFIED | Forged workspace headers and guessed peer research UUID denied; invitation email/seat/owner and peer-session boundaries tested | Local API/transactions | Production PG/runtime test still required |
| Research customer journey | VERIFIED | Signup → test outbox verification → login → workspace → DEMO.L HTTP202 → durable job → separate worker → immutable result → fresh API/store retrieval → logout/login | Local TestClient, SQLite, synthetic evidence | Not a browser/live/provider acceptance; no SMTP delivery claimed |
| Subscription and usage enforcement | VERIFIED | Workspace and account-wide ceilings, concurrent multi-workspace admission, token/worker-time budget, atomic rollback and duplicate suppression | SQLite; PG variants configured | Paid Stripe lifecycle and PG concurrency required |
| Billing safety | VERIFIED | HMAC/time/mode/replay checks; durable events; lost-response retry uses persisted exact parameters; signed customer/workspace/subscription checks; unresolved billing denies only affected customer | Mock Stripe transport + real local transactions | Live keys/products/prices/portal/webhook and real lifecycle acceptance |
| Email and notifications | VERIFIED | TLS-only SMTP adapter, encrypted leased/fenced outbox; test transport delivery; opt-in verified workspace-owner subscription recipients, rollback and dedup; in-app notification ownership | Local fixtures/transactions | Verified sender/SMTP credentials and actual delivery |
| Customer website/application | VERIFIED | Marketing, onboarding, account, plans, billing, history date/sort/page, watchlist, settings and notification rendering tests | Server render/component tests | Public/browser accessibility and runtime acceptance |
| Web clean installation | VERIFIED | `npm ci --prefix apps/web`: 385 packages installed with lifecycle scripts enabled; bounded registry fetch retries/timeouts; audit attempted separately | Local cached dependencies | Current Node advisory retrieval unavailable |
| Web lint/types/unit/build | VERIFIED | lint and typecheck pass; **178 passed, 0 failed, 0 skipped** across 11 Vitest files; `npm run build --prefix apps/web` emits dynamic root/account/product/research routes | Local Node/Next16.3.5 | Not a Netlify-hosted build |
| Genuine commercial HTTP/browser E2E | BLOCKED_EXTERNAL_INFRA | `npm run test:commercial` and `npm run test:commercial:browser`: both exit before tests at listener allocation `listen EPERM 127.0.0.1` | Restricted shell | Allowed localhost listeners/Chromium environment; zero browser passes |
| Legacy HTTP/browser smoke | BLOCKED_EXTERNAL_INFRA | `test:smoke` and `test:browser` attempted; localhost listener permission denied | Restricted shell | Cannot infer passing from unit tests |
| Migration contracts | VERIFIED | Frozen 0004/0005 additive migrations, immutable audit/usage triggers and exact index/type/column tests; Alembic drift check reports no operations | SQLite execution + PostgreSQL DDL compilation | Actual PostgreSQL migration execution |
| PostgreSQL/restart/backup/restore | BLOCKED_EXTERNAL_INFRA | `.venv/bin/python scripts/backend_acceptance.py`: ENVIRONMENT_PERMISSION_DENIED at postgres_init, zero steps; isolated temporary data removed | Attempted isolated PG drill | Permitted shared memory/listeners and real host database |
| Compose topology | VERIFIED | `docker compose config --quiet` with disposable inputs succeeds; separate API/research/email/commercial processes | CLI configuration only | No running containers implied |
| Docker image | BLOCKED_EXTERNAL_INFRA | `docker build -t money-production-check .`: Docker API socket permission denied | Restricted shell | Accessible Docker daemon |
| Existing Netlify project | VERIFIED | Local state ID `c66f8305-899f-4deb-92c8-b2ee55f5acab`; root config retains apps/web/.next, explicit adapter and SSR output guard | Local only | No remote identity/deploy-status confirmation |
| Commit/publish commercial change set | BLOCKED_EXTERNAL_INFRA | Final `git add` fails creating `.git/index.lock` with Operation not permitted; current branch main, HEAD be30431; commercial changes remain modified/untracked, staging empty | Restricted Git metadata | Git-write-capable environment required; no SaaS commit, push or deploy completed; reference-only commit deliberately not pushed alone |
| Netlify build/status/live HTTP | BLOCKED_EXTERNAL_INFRA | `npx --yes netlify-cli@latest status` and `build`: registry ENOTFOUND; public curl cannot resolve host | Restricted shell | No actual HTTP status or deployed commit observed; reported 404 remains unverified/unrepaired live |
| Python advisory scan | FAILED | Locked `pip-audit==2.10.1`: five entries / **four distinct ChromaDB1.1.1 advisories**, no fixed versions listed | Actual advisory retrieval | Critical/high transitive CrewAI dependency remediation and native qualification; see SECURITY.md |
| Node production advisory scan | BLOCKED_EXTERNAL_INFRA | `npm audit --omit=dev` cannot retrieve registry advisories (ENOTFOUND) | Restricted shell | Current scan and any necessary safe patches |
| Admin/audit/analytics | VERIFIED | Internal UUID allowlist, bounded IDs/error codes/counts, immutable sensitive audit, authorised result-opened/frontend-error hooks; no research text in analytics | Local tests | Hosted support/error tracking/monitoring configured and exercised |
| Production monitoring and backups | BLOCKED_EXTERNAL_INFRA | Process health/log/metric interfaces and restore automation exist; no production destination/schedule configured | Code/runbook only | Real host/monitoring account, backup schedule and successful restore |
| Legal/commercial data approval | BLOCKED_CREDENTIAL | Editable legal drafts and provider licence inventory explicitly unapproved; unknown redistribution rights fail closed | Git assets/UI | Operator/legal review and commercial licences |
| Automated erasure/retention fulfilment | NOT_IMPLEMENTED | Deletion request disables access and revokes sessions; audit/research retained intentionally | Local implemented request workflow | Approved retention policy, billing closure/ownership succession and fulfilment implementation/drill |
| Rich provider-call/actual-cost analytics | NOT_IMPLEMENTED | Job/user/workspace/token-budget/worker-time admission exists; unknown cost remains unknown | Partial implementation | Complete granular provider-call/cost attribution and validated reporting |
| Commercial worker health and compute supervision | VERIFIED | Email CLI health 3 tests; billing service-mode/no-processing test; database failure cannot spawn an unbudgeted research child | Local processes/SQLite | Actual container/host health remains unverified |
| Money graph refresh | VERIFIED | `graphify update .`: 2425 nodes / 7230 edges / 146 communities; AST-only | Money graph only | Semantic relabel not run; upstream graphs untouched |

The passing local test count is not commercial launch acceptance.
The new-customer **public browser → delivered email → payment → PostgreSQL → worker
→ durable research → return later** journey has not passed. Required external
configuration is in ENVIRONMENT.md; safe release ordering is in
COMMERCIAL_DEPLOYMENT.md. No customer state or credentials were committed as
reference assets. Existing versioned data was preserved; upstreams were untouched.

## Historical production-only verification (before commercial conversion)

**PRODUCTION BLOCKED.** The reported live Netlify 404 has not been repaired or
independently reproduced from this restricted session. Local builds are not live
deployment evidence. This follow-up starts from pushed `main` / `acb38f1`, not the
older foundation recorded in historical work notes. No new commit, push, remote
configuration change or deployment was completed.

Allowed statuses: VERIFIED, FAILED, BLOCKED_CREDENTIAL, BLOCKED_EXTERNAL_INFRA,
NOT_IMPLEMENTED. A passing contract/scripted-inference test is not live qualification.
VERIFIED always applies only to the stated scope/environment. Legacy drill tools
may emit `BLOCKED_ENVIRONMENT`; this document classifies it as external infrastructure.

## Production incident and remote evidence

| Requirement | Status | Actual command/test and evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Correct release baseline | VERIFIED | Git branch/log/remote and connected GitHub branch API confirm `main` / `acb38f188a763af19abccb4954f015a1be2913b5` | Local + GitHub read API | Follow-up remains uncommitted |
| Local existing Netlify link | VERIFIED | `.netlify/state.json`: `c66f8305-899f-4deb-92c8-b2ee55f5acab` | Local only | Remote account/project identity still requires CLI confirmation |
| Netlify direct CLI/status | BLOCKED_EXTERNAL_INFRA | `npx --yes netlify-cli@latest status`, `logs --source deploy --since 24h`, `build` fail registry DNS; offline latest CLI resolution also fails | Restricted shell | Direct remote identity/status and published SHA unavailable |
| Supplied production deploy log | VERIFIED | User-provided deploy `6aaaa3c8a1d24e0009cc631b`: production/main, root config, correct apps/web base/publish, successful Next build, 40 files/zero functions uploaded, post-processing complete | User-supplied actual Netlify log | Commit SHA omitted; direct live HTTP still unavailable |
| SSR deployment packaging | FAILED | Supplied log has dynamic routes but no adapter; subsequent user API inspection confirms `plugins: []` and `available_functions: []` | User-supplied production log/API findings | Explicit adapter fix now local, but not published or host-verified |
| Live production routes/assets | BLOCKED_EXTERNAL_INFRA | Bounded curl fails DNS; `npm run check:deployment --prefix apps/web -- https://neon-griffin-08e616.netlify.app --production` fails fetch; web retrieval also unavailable | Public HTTP attempts | No actual HTTP status observed; user-reported generic 404 remains unresolved |
| Local monorepo/SSR invariants | VERIFIED | Deployment checker enforces base `apps/web`, command `npm run build`, publish `.next`, root catch-all, no static export/SPA rewrite/shadow config; Next manifest contains root/API routes | Local artifacts/config | Not proof of what Netlify uploaded |
| GitHub baseline CI | FAILED | [Run 35106931767](https://github.com/vivekkchandra/Money/actions/runs/35106931767): pytest, web HTTP smoke and Python dependency audit steps failed; Next and Docker/Compose builds passed | Observed GitHub jobs/steps API | Full failure logs unavailable; no guessed diagnosis or green rerun |
| Git-linked Netlify production/Preview | BLOCKED_EXTERNAL_INFRA | Commit status API has no statuses; supplied production log proves main was fetched and deployed, not that SSR worked | GitHub API + supplied Netlify log | Corrected adapter build, deployed SHA, live acceptance and Preview qualification |
| Publish this follow-up | BLOCKED_EXTERNAL_INFRA | Later staging denied `.git/index.lock`; push dry-run failed GitHub DNS; GitHub write tool rejected unavailable approval | Restricted filesystem/network/tool policy | Write/network-enabled execution; no remote write occurred |

No `netlify init`, project creation/deletion, fake index, SPA redirect or
speculative publish-directory rewrite was performed. After the supplied log,
`@netlify/plugin-nextjs` was explicitly configured and its available `5.15.11`
package installed/locked offline. This is not a completed Netlify build or current
advisory scan. `live-web.yml` adds a
read-only public acceptance check; it has not run remotely. CI now produces
bounded test/advisory summaries without dumping assertion bodies or secrets.

## Checks and acceptance evidence

| Requirement | Status | Evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Locked Python installation | VERIFIED | `uv sync --locked`: 191 resolved, 188 checked; existing lock retained | Local Python 3.12 | None for base runtime |
| Python lint | VERIFIED | `uv run ruff check .`: all checks passed | Local | None |
| Python typing | VERIFIED | `uv run mypy src/money`: 79 source files clean | Local | None |
| Expanded Python suite | VERIFIED | Final `uv run pytest -q`: 536 passed, 43 explicitly skipped, zero failed, 28 dependency deprecation warnings in 28.20s | Local | Skips: 32 real-PostgreSQL variants and 11 genuine-provider/native opt-in cases |
| SQLite migrations/schema drift | VERIFIED | Integration suite applies all migrations and Alembic check reports no operations | Local SQLite only | Does not establish PostgreSQL semantics |
| PostgreSQL locking/migrations/recovery | BLOCKED_EXTERNAL_INFRA | `uv run python scripts/backend_acceptance.py` reports ENVIRONMENT_PERMISSION_DENIED at postgres_init; PostgreSQL 17.10 initdb shared-memory allocation denied | Isolated temporary local cluster | No migration/restore claim; permitted shared memory/listeners required |
| Migration serialization | VERIFIED | Transaction-scoped advisory lock and bounded connect/lock/statement timeouts implemented; SQLite migration regression passes; drill launches concurrent migrations | Code/SQLite tests | Actual PG advisory-lock execution still unverified |
| Durable enqueue/worker/retrieval | VERIFIED | API TestClient → HTTP 202 → committed queue → separate subprocess → fresh API/store retrieval | Local SQLite/process tests | Actual network/browser/PostgreSQL path below |
| Claims/leases/fences/retries/deadline recovery | VERIFIED | Concurrent claims, stale owner, timeout/crash and sealed checkpoint tests | Local; real PG variants configured | Actual PG run still required |
| Idempotency/budget admission/settlement | VERIFIED | Concurrent atomic batch admission, rollback, usage reconcile, unknown cost and stale-worker tests | Local SQLite | PG variants include budget/correspondence recovery |
| Native correspondence recovery | VERIFIED | Barriers before paid calls, immutable input/result hashes, per-call journal, interrupted settlement and stale-worker fences; stage-filtered reconciliation prevents double charging | Local SQLite/optional PG tests | Paid runtime and PG variants unverified |
| Live worker capability binding | VERIFIED | Regression proves runtime factory receives claimed/fenced store before correspondence captures it | Local patched-factory test | No deployed worker started |
| Private authentication/authorization | VERIFIED | HttpOnly/Secure/SameSite cookie, CSRF, expiry/signatures, durable revocation/login rate limit and workspace predicates tested | Local API/web tests | Deployed cookie/proxy/TLS/browser acceptance |
| Source/prompt security | VERIFIED | SSRF, DNS/IP/redirect, body/decompression/archive/XML/markup limits; native tool/file/network denial; strict output/usage JSON | Local adversarial tests | Host OS egress is not proven by Python guards |
| API request receive deadline | VERIFIED | Slow body cancelled with 408 before business logic; small chunks replay as one bounded body | Local ASGI tests | Host TLS/header/connection limits remain deployment controls |
| Full history / bounded qualitative context | VERIFIED | 1500-bar fixture retains exact latest80 bars plus every non-price record, reports omissions, rejects unavailable/conflicting omitted history and out-of-context citations | Local native contract tests | No live model-quality claim |
| Research-only / Netlify compute boundary | VERIFIED | `uv run python scripts/check_deployment.py`; adversarial static capability/import/client-secret tests | Local | Static checks complement deployment/runtime policy |
| Eligibility/ethics/currency/£200 gates | VERIFIED | Unknown/stale/nonstock/currency/exclusion/capital tests fail closed | Local contracts | Actual current ISA and business proofs absent |
| GBX/GBP, quality, archive/PIT | VERIFIED | Raw/normalized price properties, financial/source conflicts, future timestamps, action-basis and archive/current mismatch tests | Local | Genuine historical publication corpus unavailable |
| TA-Lib / discovery channels | VERIFIED | Native deterministic indicators; title catalyst/financial-delta triggers; private Qlib scan reuse and union tests | Local synthetic fixtures | Live coverage/model qualification required |
| UK HTTP provider implementations | VERIFIED | Real endpoint/auth/normalization/coverage code with deterministic transport contract tests | Local test transport | No successful credentialed production call claimed |
| Filing document → financial snapshot | VERIFIED | 102 new transport/assembly tests: exact filing/content/unit proof, redirects/credentials, safe provenance, freshness and no fallback; selected documents integrated in live builder | Injected HTTP/parser tests | Companies House key, reviewed manifest/hosts/units/licences and actual source qualification |
| Native XBRL source attestation | VERIFIED | 13 new tests reject missing/mismatched/unsafe standalone modules before import; exact read-only upstream source digest verified; injected parser explicitly unattested | Local attestation tests | Pinned parser package/dependencies not installed; no native live conversion claimed |
| Real ISA availability | BLOCKED_CREDENTIAL | Metadata client does not invent ISA membership; reviewed manifest artifact required | Not run live | Verified ISA-specific source/coverage and optional metadata credentials |
| Real market/actions/news/filings | BLOCKED_CREDENTIAL | Six opt-in provider checks, including real document conversion, explicitly skipped | Not run live | EODHD entitlement/key, Companies House key, reviewed manifest/licences/parser |
| TradingAgents / AI-HF real paid qualification | BLOCKED_CREDENTIAL | Native assemblies and escape tests implemented; AI-HF pinned lifecycle exercised with scripted test inference only | Local read-only checkout | Pinned installed runtimes, inference credentials, host qualification |
| Qlib real approved production model | BLOCKED_EXTERNAL_INFRA | Native DatasetH/LinearModel seam, strict artifact/hash/registry tests; no fabricated score | Local contracts | Installed pinned Qlib, real PIT corpus, independent OOS review/promotion |
| Offline baseline training | VERIFIED | 20 actual NumPy training/CLI tests, future-label mutation isolation, purged/embargoed temporal folds, source/artifact/report hashes and rejected automatic promotion | Local synthetic data only | Production artifact remains PIT-unqualified and UNPROMOTED |
| LEAN actual engine / historical costs | BLOCKED_EXTERNAL_INFRA | Fixed OCI study/resource/PIT/cost/sensitivity contracts tested; real engine not run | Local tests | Accessible Docker, digest image, archived dataset/cost/survivorship controls |
| CrewAI real paid qualification | BLOCKED_CREDENTIAL | Actual Flow/Agent/Task lifecycle exercised with scripted inference, including isolated subprocess | Local test/demo only | Installed source mismatches pin; correct runtime and inference credentials |
| Blind barrier / immutable reports | VERIFIED | Capability, provider-escape, cross-connection barrier, DB mutation and peer-opinion exclusion tests | Local; PG CI coverage | Native deployed escape qualification pending |
| CIO / Red Team / consensus / challenges | VERIFIED | Independent numeric recomputation, unsupported-claim blocking, VETO, bounded immutable challenge provenance, no vote consensus | Local | Original audit vetoes remain intact even if later correspondence adds evidence |
| Native structured correspondence | VERIFIED | TA native bear factory, AI-HF persona, separate CrewAI source verifier and deterministic Qlib/LEAN checks; explicit permitted citations, source hashes, max two rounds and invocation metadata | Scripted/contract tests | Disabled by default; genuine runtime/source/budget qualification required |
| Signal design/expiry/invalidation/replay | VERIFIED | Recomputed risk/cost/targets; exact LEAN scenario-policy binding; actual TA-Lib ATR parity and calendar-day horizon; forged artifacts rejected; read-time expiry; immutable replay | Local test fixtures | No positive live signal qualified |
| Strong candidates / calibrated confidence | NOT_IMPLEMENTED | Intentionally unavailable without empirical qualification | None | Sufficient independent OOS outcomes and component calibration |
| Research-reference outcomes / QuantStats | VERIFIED | Durable operator-supplied outcome records and existing native diagnostics; research != actual trade | Local fixtures | Automated ingestion and adjusted basis remain unimplemented |
| Web installation | VERIFIED | `npm ci --prefix apps/web` passed (385 packages after locked Next adapter addition); bounded fetch retries/timeouts | Local cache/network-restricted shell | npm emitted existing ESLint9.39.4 deprecation; offline audit message is not a current advisory scan |
| Web lint / typecheck / unit tests | VERIFIED | Final npm lint/typecheck passed; 129 Vitest tests across nine files passed, zero skipped/failed | Local Node | None in this scope |
| Netlify SSR deployment guard | VERIFIED | 19 adversarial artifact-fixture tests; explicit locked adapter, local plugin after it, actual bundled-handler manifest/path/catch-all and published JS/CSS required; raw Next output rejected | Local fixture tests | Actual hosted adapter/guard execution not verified; pinned layout/version upgrade requires review |
| Cross-examination UX | VERIFIED | 34 component tests cover separate organisations/rounds/original claims, independent findings, source links, unknown costs, malformed/inconsistent packets and safe markup | Local components | Actual browser acceptance still blocked |
| Smoke process bounds | VERIFIED | Eight lifecycle tests cover finite operation/shutdown deadlines, escalation and safe temporary-data retention | Local synthetic child tests | Does not identify the previous remote smoke failure cause |
| Next.js production build | VERIFIED | `npm run build --prefix apps/web` passed without research/provider secrets | Local | Not a Netlify-hosted build |
| Browser / web HTTP E2E | BLOCKED_EXTERNAL_INFRA | Real login→202→worker→sealed packet→reload smoke implemented; listener attempts hit EPERM | Local sandbox | Allowed localhost listeners/Chromium runtime; remote baseline HTTP smoke actually failed |
| HTTP deployment checker | VERIFIED | Tests reject generic 404, wrong routes, missing/HTML assets, unsafe headers/nonces and nonclosed APIs; root/dashboard/research/system aliases tested | Injected HTTP tests | Actual public HTTP acceptance blocked |
| Compose configuration | VERIFIED | `docker compose config --quiet` passed using disposable nonproduction values | Local CLI | No running containers implied |
| Docker image build | BLOCKED_EXTERNAL_INFRA | `docker build -t money-production-check .` denied Docker socket access | Local sandbox | Accessible daemon; old acb38f1 image passed remotely, not this follow-up |
| Netlify CLI build | BLOCKED_EXTERNAL_INFRA | Latest CLI resolution fails registry DNS; cached npx CLI27.8.0 offline diagnostic starts but cannot write its global preferences (`EPERM`) | Local sandbox | Supported writable CLI environment/network; no HOME override, credentials copied or permissions weakened |
| Dependency advisory scans | FAILED | Remote baseline npm production audit passed; Python pip-audit failed; local npm/pip-audit retrieval blocked by DNS | Remote baseline + local | Obtain real advisory/error details and fix safely; no suppression or blanket upgrades |
| Backups / restore / OCI operations | BLOCKED_EXTERNAL_INFRA | Actual isolated concurrent-migration/killed-worker/PG-restart/dump/restore drill implemented and CI-wired; local execution stops at initdb | Local attempted drill | Successful real drill and host controls required; documentation is not verification |
| Production compute/API/worker/PG | BLOCKED_EXTERNAL_INFRA | No qualified host/production connection config found; legacy loopback PostgreSQL not reachable | Local configuration inspection | Authorized OCI host, TLS PostgreSQL, production secrets/live manifest |
| Genuine production-integration suite | BLOCKED_CREDENTIAL | Explicit opt-in `MONEY_RUN_PRODUCTION_INTEGRATION=1 uv run pytest tests/production -q`: eleven explicit skips | Local opt-in | Missing manifest/hash and genuine snapshot/credentials; zero live passes |
| Scheduled ingestion/calibration & broad corporate-action transforms | NOT_IMPLEMENTED | Explicitly listed in IMPLEMENTATION_PLAN.md | None | Remaining engineering plus qualified data |
| Money Graphify update | VERIFIED | Final `graphify update .`: 1868 nodes, 5477 edges, 135 communities; AST-only, no LLM; curated graph backed up by tool | Local | Semantic document refresh not run; upstream graphs untouched |

## Release blockers

1. Network/write-enabled execution for this repository and the existing authorized
   Netlify project. Obtain deploy logs/SHA/adapter output, diagnose the reported
   404 packaging fix, publish reviewed changes and pass live HTTP route/asset/auth/header
   acceptance. The supplied log identifies missing adapter execution; the exact
   reason automatic installation did not occur remains unobserved; the user API
   inspection confirms the current runtime/plugin is absent. No replacement project is needed.
2. Obtain failing GitHub pytest, HTTP-smoke and Python-audit logs, fix actual
   failures and rerun release jobs. New safe summaries are not yet pushed.
3. Provide an authorized OCI compute host and TLS PostgreSQL; run safe migrations,
   API/worker/queue/restart acceptance and an actual isolated backup/restore drill.
4. Verified ISA/ethical/source/licensing data, original-publication history,
   corporate-action coverage, dated costs and live market/filing/inference keys.
5. Pinned native dependency images, independently validated/manually promoted Qlib
   artifact, isolated LEAN engine and verified host egress/resource controls; then
   genuine end-to-end paid qualification, not a fixture demonstration.
6. Remaining engineering explicitly listed in IMPLEMENTATION_PLAN.md. Automated
   universe/automatic filing qualification, unsupported action transformations, scheduled outcome
   calibration and optional external alert delivery are not credential blockers.

Detailed workstream evidence: WORK_BACKEND.md, WORK_NATIVE.md, WORK_WEB.md,
WORK_DEPLOY.md, WORK_FILINGS.md, WORK_PROVIDERS_REVIEW.md, WORK_BUDGET_RECOVERY.md and WORK_TRAINING.md.
The final consolidated counts above supersede intermediate workstream counts.
Graphify's pre-existing
installed Claude-skill/package version warning does not authorize global changes.
