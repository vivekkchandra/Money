# Native qualification blockers — 2026-09-18

The resumable state remains **unqualified**. This is an engineering diagnosis,
not independent approval, a fresh dependency audit, or hosted runtime evidence.
`outputs/native-blocker-diagnosis.json` is reproducible offline through
`money.qualification.native_diagnosis.write_native_diagnosis(ctx)`. It verifies
referenced artifact bytes, preserves the original receipt timestamps, and writes
only that diagnosis. The saved preflight/audit ran on Darwin on September 17;
its one-hour qualification window has expired.

## Sources and closure

The pinned TradingAgents, AI-Hedge-Fund and CrewAI checkout fingerprints match
`SOURCE_DIGESTS`. TradingAgents and `hedge_fund` are not installed in the observed
environment. CrewAI 1.15.21 is installed, but its Python-source hash differs from
the pinned checkout. Installed LangChain/LangGraph packages are absent. Source
checkouts and version labels do not establish an executable qualified runtime.

The real Linux/Python 3.12 resolver returned `No solution found`. These exact
checked-out distribution requirements independently have empty intersections:

| Package | AI-Hedge-Fund requirement | Conflicting requirement |
| --- | --- | --- |
| python-dotenv | `1.0.0` | Money `>=1.2.3` |
| NumPy | `^1.24.0` (`<2`) | Money `>=2.5.3` |
| pandas | `^2.1.0` (`<3`) | Money `>=3.0.5` |
| langchain-anthropic | `0.3.5` | TradingAgents `>=0.3.15` |
| langchain-google-genai | `^2.0.11` (`<3`) | TradingAgents `>=4.0.0` |

Do not retry a known-unsatisfiable worker build or install with `--no-deps`.
An engineering owner must first propose a reviewed compatible upstream patch/
source repin or an explicitly designed per-engine distribution/isolation
contract. Neither has been approved or implemented here. Preserve exact source
attestation and frozen Money evidence; then resolve, lock, build and audit the
actual exercised closure. Do not silently edit upstream requirements or pins.

The current research image installs Money's locked CrewAI extra only. It does
not package the pinned TradingAgents/AI-Hedge-Fund sources or qualify native
imports. The actual pinned CrewAI package roots include `lib/crewai`,
`lib/crewai-core` and `lib/cli`; see [NATIVE_WORKER_BUILD.md](NATIVE_WORKER_BUILD.md).

## ChromaDB: no verified safe upgrade under the current pins

Pinned CrewAI requires `chromadb~=1.1.0`; the installed version is 1.1.1. The
saved unsuppressed audit has five entries representing four distinct CVEs:

- [CVE-2026-45829](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c): unauthenticated code injection, affected `1.0.0` through `1.5.9`.
- [CVE-2026-45833](https://github.com/advisories/GHSA-36p7-vc44-83pf): authenticated code injection, affected `0.4.17` through `1.5.9`.
- [CVE-2026-45830](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr): cross-tenant access, affected `0.4.17` through `1.5.9`.
- [CVE-2026-45831](https://github.com/advisories/GHSA-xph7-9rjv-w5fr): authorization scope failure, affected `0.5.0` through `1.5.9`.

All four advisory pages, checked September 18, identify no patched release.
This does not prove every future version vulnerable; it means no safe compatible
replacement was verified. The recorded audit's duplicate PYSEC entry is retained
in the raw evidence. Diagnosis groups aliases without suppressing findings.
Disabling CrewAI memory, blocking Chroma capabilities, or a passing unit test is
containment, not advisory remediation. A reviewed compatible source/dependency
change and a fresh full passing audit are necessary.

## Qlib, LEAN, Linux and inference

`MONEY_QLIB_ENABLED=false` is explicit in the saved state. Qlib is disabled, not
qualified; do not install, train, fabricate its report, or require its promotion
for this mode. Re-enabling it restores every existing source/compiled ABI,
point-in-time, OOS, walk-forward, registry and independent-promotion requirement.

LEAN remains mandatory. `reviews/lean-inputs.json` has unresolved container,
cost, adjustment-policy and independent-approval fields. Historical eligibility,
survivorship, corporate actions, walk-forward/OOS, MAE/MFE and drawdown evidence
remain required. Docker CLI is present on the inspected Mac but this sandbox
cannot access its daemon; no image build or LEAN execution was verified. Local
`dotnet` and `nft` commands are absent. These observations do not prove what is
installed in the eventual Linux worker.

Local Ollama access is genuinely recorded, but `hosted_compatible=false`,
`native_runtime_qualified=false`, `production_qualified=false`. Select and
independently qualify a remotely reachable inference endpoint for the worker;
never use the Mac's `127.0.0.1` as Railway production inference.

## Exact deployment actions remaining

1. Keep project `incredible-flexibility`, environment `production`, existing
   `Money` and `Postgres`. After closure/security remediation, deploy the research
   image as private `Money Research Worker` in that same project, using the shared
   database queue; do not generate a public worker domain. Do not replace the
   lightweight API image or change its production/hosted/live safety flags.
2. Establish a real OS/container allowlist for the native execution namespace.
   Money currently implements only the `linux-nftables` inspector. Verify that
   its actual policy can be inspected while worker network-admin/raw/admin
   capabilities are denied and `NoNewPrivs=1`; if the platform cannot provide
   this, implement and independently review a trusted platform-specific
   inspector. Do not grant native processes policy-changing privilege or accept
   an uploaded policy as proof of enforcement.
3. Collect target identity, live policy bytes and native allowed/denied probe
   evidence; an independent reviewer must complete `reviews/native-egress.json`.
   Private inbound isolation and static outbound IPs are not evidence that
   unapproved destinations are blocked. Railway documents [private networking](https://docs.railway.com/networking/private-networking)
   and [outbound networking](https://docs.railway.com/networking/outbound-networking)
   separately; neither documents the required Money-specific enforcement proof.
4. Rerun native/security qualification on the **actual enforced Linux target**
   with its reviewed hosted inference selection. `railway run` executes locally
   with Railway variables, not inside that target. See the [Railway CLI local
   development commands](https://docs.railway.com/cli).

No Railway service, credential, package environment, source pin, review or
production manifest was changed by this investigation. The native/qualification/
optional-Qlib regression subset passed 138 tests with 26 visible CrewAI
deprecation warnings; those tests are not live native qualification evidence.
