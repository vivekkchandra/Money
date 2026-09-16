# Architecture and implementation boundary

Money is independent investment research. It never prepares broker orders or reads a user's portfolio. `knowledge/STACK.md` and the product mandate govern this implementation.

Initial inspection: the existing `src/money` package contains empty module seams and a greeting CLI; there is no existing research runtime, database, web app or test suite to migrate. The checked-in upstream SHA locks and existing Graphify indexes are retained. Upstreams and optional references remain read-only.

## Deployment

```text
Browser → Next.js on Netlify → authenticated research API → PostgreSQL job queue
                                                           ↓
                                              separate container worker
                                                           ↓
                            eligibility → snapshot → three blind firms → lock
                                                           ↓
                                         LEAN → CrewAI/audit → consensus
                                                           ↓
                                        immutable packet → PostgreSQL → UI
```

One Python image supplies API and worker processes. PostgreSQL owns durable state, queue claims and report locking. Netlify requests enqueue or read; request completion cannot terminate research. LEAN has a runner boundary for its separate .NET environment. RD-Agent remains an offline administrative workflow.

The first vertical slice includes mandate/evidence contracts, deterministic quality gates, isolated adapter inputs, durable sealed-report barrier, job API/worker, demonstrable fixture research, web UI and deployment configuration. A fixture is never described as a live upstream run. Production cannot silently use fixtures or yfinance, invent eligibility, or publish on missing firms/validation/audit. Live provider credentials, verified ISA-universe supply and full upstream runtime qualification remain explicit deployment gates.

Initial web access is a single-user authenticated workspace. API credentials stay on servers. Deploy previews require separate development API/database credentials; they must not write to production.
