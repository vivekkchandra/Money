# Architecture and implementation boundary

Money is independent investment research. It never prepares broker orders or reads a user's portfolio. `knowledge/STACK.md` and the product mandate govern this implementation.

Audit baseline was commit `6607d76`, an existing substantial vertical slice with
94 passing Python tests, a durable SQL queue, governance contracts and a Next.js
product. It was extended in place, not recreated. Upstream SHA locks and indexes
are retained; `upstreams/` and `optional_reference/` were not modified.

## Deployment

```text
Browser → Next.js on Netlify → authenticated research API → PostgreSQL job queue
                                                           ↓
                                              separate container worker
                                                           ↓
                            eligibility → snapshot → three blind firms → lock
                                                           ↓
                                LEAN → CrewAI/audit → bounded challenges → consensus
                                                           ↓
                                        immutable packet → PostgreSQL → UI
```

One Python image supplies API and worker processes. PostgreSQL owns durable state, queue claims and report locking. Netlify requests enqueue or read; request completion cannot terminate research. LEAN has a runner boundary for its separate .NET environment. RD-Agent remains an offline administrative workflow.

The current live assembly uses an administrator-controlled hash-pinned manifest.
It names qualified providers, immutable source artifacts, original-publication
archives, a manually promoted Qlib model, exact inference selections and an
isolated LEAN image. Job retries pin that manifest and reuse sealed checkpoints.
Provider-neutral inference uses pessimistic durable token reservations. Native
processes receive frozen contracts, no database capability, no peer outputs and
only their inference credential. Host confinement remains required: Python guards
are not an OS sandbox.

The base image is separate from qualified native/LEAN runtimes. A fixture is never
a live upstream run. Production startup rejects unconfigured/demo research and
unqualified provider manifests. Current retrieval is not historical publication
proof. The real critical path remains unqualified: see VERIFICATION.md.

Initial web access is a single-user authenticated workspace. API credentials stay on servers. Deploy previews require separate development API/database credentials; they must not write to production.
