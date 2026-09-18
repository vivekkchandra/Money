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

## Minimum path for the first genuine candidate

Rechecked September 18 at 09:10 UTC: all five referenced resolver/audit artifacts
match their SHA256, and all eight resolver input files still match the failed
receipt. All three selected source checkouts still match their source pins;
installed TradingAgents/AI-Hedge-Fund remain absent and installed CrewAI still
differs. No runtime blocker was resolved by rechecking. The narrower diagnostic,
network-boundary, native-qualification and optional-Qlib suite passed 74 tests.
`outputs/native-runtime-readiness.json` records this bounded inspection, not a
new qualification. The four linked security advisories were checked again and
still identify no patched release.

The minimum hosted inference selection is three independently configured roles
using a public, authenticated HTTPS endpoint on port 443, an exact returned model
identity, bounded requests and an independently reviewed selection. Public DNS
must resolve only to permitted public addresses. The existing checked-in OpenAI
records parse and satisfy the hosted **schema**; this check did not call that
provider or validate its credential. They were not changed or selected for a run.
A different hosted model requires its real endpoint/credential-variable name and
qualification; do not insert an illustrative URL or fake credential into a review.

Under the current `InferenceSelection`/transport contract, unauthenticated access
is local-only, and `.internal`, loopback and private IP inference targets are
rejected. Consequently, a Railway-private model endpoint is not a drop-in
replacement today. This does **not** require exposing the research worker: keep
`Money Research Worker` private and let only its enforced research namespace
reach the reviewed HTTPS gateway. Do not expose the Mac's Ollama listener.

After the source/dependency/security work is legitimately complete, that worker
needs the existing Postgres queue, `python -m money.worker`, and its existing
`python -m money.worker --healthcheck` probe. The exact native image, manifest,
hosted selection and OS enforcement must be qualified together. Current
`Dockerfile.research` does not copy `scripts/` or the pinned source checkouts;
one cannot claim to run `scripts/build_live_qualification.py` inside that image
without first implementing and verifying the qualification/build packaging.
The LEAN runner additionally needs access to its separately pinned, order-free
container runtime; a Python research image alone does not provide it.

The read-only Railway 5.57.7 status check for the existing project failed on
sandbox DNS after an OAuth-refresh network error. It did not verify whether a
worker currently exists or whether any service is healthy; do not duplicate a
worker based on that failure. The operator can inspect the current topology with:

```bash
railway status --project 57b5ce77-898d-40a7-9f4a-d864c84e73dc --environment production
```

No deployment/configuration change is the next corrective step while the pinned
closure is unsatisfiable and the security audit fails. Required human/infrastructure
decisions are the reviewed compatible native closure, real hosted inference
selection, target enforcement/inspector, and independent LEAN/egress evidence.
These requirements apply to one genuine candidate as well as a larger universe.
