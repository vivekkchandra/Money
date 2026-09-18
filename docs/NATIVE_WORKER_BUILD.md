# Native runtime packaging

## Local two-firm research: isolated environments

The personal research runner now calls `prepare_native_environments(ctx)` from
`run_local_native_preflight`. The operator command in
[research-testing.md](research-testing.md) is unchanged; when native preflight
is reached it prepares `.venv-tradingagents` and `.venv-ai-hedge-fund` separately,
using Python 3.12.14 and the exact commits in `UPSTREAM_LOCK.txt`.

Each environment resolves and installs its **complete declared dependency
graph**, together with the thin Money snapshot/inference bridge, rather than
installing Money's full application dependencies into both. This separates
AI-Hedge-Fund's NumPy/pandas/dotenv/LangChain constraints from TradingAgents and
the API/control plane without editing upstream application code or source pins.
AIHF's five-declaration metadata-only compatibility patch, source preservation,
advisories and actual validation results are documented in
[aihf-dependency-compatibility.md](aihf-dependency-compatibility.md). It is not a
resolver override or security exemption; installed metadata and audit must pass.
Hashed dependency locks and setup cache live under `<qualification root>/state/native/`
(normally `data/qualified/local-inference/state/native/`).

Execution requires each actual target interpreter to pass exact installed-source
attestation, native import checks, installed dependency consistency and a complete
unsuppressed dependency audit. A successful resolver or created virtualenv alone
does not qualify it. Money's parent validates bounded child reports against the
same immutable snapshot before either independent report can be sealed. Provider
credentials are not passed into child environments; Ollama needs no fake key.

CrewAI/ChromaDB are not automatically added to these two environments. Their
absence is valid only if the actual resolved native dependency closure does not
require them; every installed package is still audited. Existing main/CrewAI
advisories remain recorded, not ignored or relabelled fixed. This local process
separation is not hosted OS/container egress qualification or production approval.

Installation and fresh audit outcomes are recorded by the runtime preparation
workflow. The initial September 18 sandbox run verified both exact source archives and
created both Python 3.12.14 environments, then stopped with
`NATIVE_PACKAGE_INDEX_UNREACHABLE`. Neither upstream wheel was installed and
neither target dependency audit ran. This is not a claim that installation or
audit will pass elsewhere. A failed setup, unavailable
registry/advisory service, missing source or reported vulnerability remains an
explicit native-execution blocker. No native report is substituted.

## Separate hosted worker: historical shared-environment diagnosis

The following September 17 observations/build requirements describe the retained
hosted/commercial runtime, not a requirement to merge the two local research
environments back into one dependency set.

Status: **PRODUCTION BLOCKED**. The research Docker target currently installs the
locked CrewAI extra, which is insufficient to run the four pinned native engines.
No successful image build, native production run or host-egress qualification is
claimed here. The existing API/control-plane image is not expanded by this plan.

For the current explicit `MONEY_QLIB_ENABLED=false` qualification path, Qlib
source, compilation, training and promotion are not required. TradingAgents,
AI-Hedge-Fund, CrewAI and LEAN remain required. The saved receipts and current
advisory investigation are summarized in [native-blockers.md](native-blockers.md);
neither that diagnosis nor a local scripted test is production qualification.

## Observed constraints

- The read-only source trees for TradingAgents, AI-Hedge-Fund, Qlib and CrewAI
  match all four existing `SOURCE_DIGESTS` when selected explicitly on `PYTHONPATH`.
- TradingAgents is absent from the installed environment. Its pinned source
  cannot import `tradingagents.agents` without `langchain_core`; its actual import
  graph also imports LangGraph and vendor modules even when Money disables tools.
- AI-Hedge-Fund's pinned Buffett and Lynch modules import successfully using the
  existing environment. Installing its complete distribution requirements would
  conflict with Money: it declares NumPy `<2`, pandas `<3`,
  `python-dotenv==1.0.0` and `langchain-anthropic==0.3.5`. Money requires NumPy
  `>=2.5.3`, pandas `>=3.0.5` and dotenv `>=1.2.3`. Money's native snapshot adapter
  does not invoke the upstream vendor-client constructors.
- The installed CrewAI version is `1.15.21`, but its source fingerprint differs
  from the pinned checkout. Selecting the pinned CrewAI package alone then fails
  because installed `crewai_core` lacks `platform_apps`. Selecting all three
  pinned package roots (`crewai`, `crewai_core`, `crewai_cli`) makes the real Flow
  import succeed. A version number alone is insufficient source attestation.
- Qlib's pinned source cannot import without `setuptools_scm`. The actual numeric
  path also needs scikit-learn, joblib, ruamel.yaml, dill, python-redis-lock and
  further transitive imports. `DatasetH` loads compiled `qlib.data._libs.rolling`
  and `expanding`; copying only Python files cannot produce a working runtime.
- Qlib's normal build writes `qlib/_version.py` through `setuptools_scm`, while
  the existing source attestation hashes every Python file. The generated file
  changes the digest. Omitting it without preserving Git metadata makes source
  version discovery fail instead. Neither failure may be silently bypassed.
- An actual offline dependency-resolution attempt, using a writable copy of the
  existing uv registry metadata cache, failed because `langchain-core` is absent
  from the cache. Required setuptools-scm, ruamel.yaml, dill and redis-lock
  metadata are also absent. An honest updated lock cannot be produced from the
  currently available cache. No speculative dependency versions were added.

## Hosted build sequence (not the local two-environment installer)

1. Resolve each exercised native dependency closure with registry access, retaining
   separate environments where upstream requirements conflict. Preserve an
   independently resolved control-plane export. Pin and audit every runtime's
   actual complete installed closure, including transitive dependencies.
   Do not use `--no-deps` to hide unsatisfied upstream requirements or overwrite
   the existing lock without a successful resolver result.
2. Add a builder stage used only by the research target in both Dockerfiles.
   Retrieve each repository URL and full commit from `UPSTREAM_LOCK.txt`, verify
   the fetched Git commit, and reject unexpected repositories, mutable refs,
   symlinks and source fingerprints. Do not copy local upstream working trees,
   repository credentials, `.git`, caches or test fixtures into the final image.
3. Preserve exact source package bytes and upstream license files. Native source
   roots are:

   | Repository | Required source root |
   | --- | --- |
   | TradingAgents | `tradingagents/` |
   | AI-Hedge-Fund | `hedge_fund/` |
   | CrewAI | `lib/crewai/src/crewai/` |
   | CrewAI core | `lib/crewai-core/src/crewai_core/` |
   | CrewAI CLI | `lib/cli/src/crewai_cli/` |
   | Qlib | `qlib/`, with compiled extensions from the same commit |

   Treat this as an explicit Money native-source distribution with its own
   declared dependency closure if full upstream distributions cannot coexist;
   do not falsely claim that incompatible upstream metadata was satisfied.
4. Build Qlib's extensions against the selected Python/NumPy ABI in the builder.
   Record the compiler/build-input identity and output hashes. Extend source
   attestation only through a separately reviewed, deterministic generated-file
   contract: original source hashes must remain unchanged and the exact generated
   version bytes must be verified. Do not broadly exclude `_version.py` or accept
   an arbitrary post-build digest as proof of upstream identity.
5. Install the resulting source distribution and extensions under the worker
   virtualenv's read-only package directory so the existing child filesystem
   boundary can read them. Merely placing sources in `/opt` and adding
   `PYTHONPATH` is insufficient: that path is not in the child's permitted read
   roots. Keep build compilers, Git history and fetch tooling out of the final
   non-root worker image.
6. Make the image build fail on missing native imports or source mismatches.
   Run the existing source checks and real scripted native lifecycle regressions
   inside the image. Validate Qlib numerical training and prediction against its
   compiled runtime. A scripted lifecycle is packaging verification only.
7. Obtain a passing unsuppressed research dependency audit and deploy a real
   OS/container egress policy. Qualify the exact deployed image using the frozen
   Money snapshot and actual inference credentials. Persist authentic reports,
   runtime identities, usage and hashes. Only then may those artifacts contribute
   to the live qualification bundle.

## Security remediation evidence

The official advisory records still identify no patched version for
[pre-authentication RCE](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c),
[authenticated RCE](https://github.com/advisories/GHSA-36p7-vc44-83pf) and
[cross-tenant collection access](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr).
The [RBAC advisory](https://osv.dev/vulnerability/PYSEC-2026-3815) records affected
versions through 1.5.9. The [upstream authorization fix PR](https://github.com/chroma-core/chroma/pull/7602)
remains open, including a September 16 request for merge/release; the
[RCE issue](https://github.com/chroma-core/chroma/issues/6717) remains open.

The [release page](https://github.com/chroma-core/chroma/releases) identifies 1.5.9
as the latest stable release and 1.5.10.dev290 as a prerelease. No safe upgrade
compatible with pinned CrewAI's `chromadb~=1.1.0` contract was established. An
unaffected-looking version number or ignored advisory is not remediation.

The Money Python capability guard was strengthened to cover alternate DNS APIs,
connectionless sends and UDP connections. The focused native/dependency/
correspondence suite passed 100 tests, with 26 visible CrewAI deprecation
warnings; Ruff and mypy passed for the changed boundary. These are local
containment regressions, not OS-egress, live-inference or dependency-remediation
qualification evidence.
