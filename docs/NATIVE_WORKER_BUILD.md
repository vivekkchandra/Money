# Native worker packaging — 2026-09-17

Status: **PRODUCTION BLOCKED**. The research Docker target currently installs the
locked CrewAI extra, which is insufficient to run the four pinned native engines.
No successful image build, native production run or host-egress qualification is
claimed here. The existing API/control-plane image is not expanded by this plan.

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

## Concrete build sequence

1. Resolve the narrowly exercised native dependency closure with registry access.
   Add those dependencies to Money's `research` extra and build tools to a
   separate locked build group. Preserve an independently resolved control-plane
   export. Pin and audit both exports, including all transitive dependencies.
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
   | CrewAI CLI | `lib/crewai-cli/src/crewai_cli/` |
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
