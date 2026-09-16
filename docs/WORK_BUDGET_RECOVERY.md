# Budget recovery and independence verification — 2026-09-16

Implemented in Money-owned budget orchestration, without changing provider or research gates:

- Typed reservation batches are admitted atomically before first-pass paid calls. Any failed admission rolls back the whole batch.
- CIO reservation occurs only upon reaching its audit stage.
- Recovery reconciles sealed report/CIO usage before new admission. It reads only the report usage JSON field, not pre-barrier peer conclusions.
- Identical usage settlement is idempotent; conflicting replacement is rejected. Unknown usage and prior failed attempts remain pessimistically charged, never inferred as zero.
- Reservation, settlement and reconciliation enforce the current worker fence when operating through a claimed store, and enforce workspace ownership.
- Independent tests exercise configuration pinning before provider calls and on retry, plus the quant discovery cache's isolation from qualitative first-pass capabilities.

| Requirement | Status | Evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Atomic admission, recovery, fencing, tenant access, late CIO reservation | VERIFIED | `pytest tests/integration/test_budget_recovery.py tests/integration/test_production_services.py tests/integration/test_durable_research.py -q`: 32 passed, 8 skipped | Local SQLite fixture, 2026-09-16 | PostgreSQL cases separate below |
| Provenance retry pinning and discovery-cache independence | VERIFIED | `pytest tests/integration/test_runtime_pinning.py tests/integration/test_budget_recovery.py -q`: 15 passed, 8 skipped | Local SQLite + explicit synthetic firms | Does not qualify live upstream systems |
| Same budget recovery cases on PostgreSQL | BLOCKED_ENVIRONMENT | Eight parameterized PostgreSQL cases explicitly skipped | Local | `TEST_DATABASE_URL` unavailable; CI runs them against its PostgreSQL service |
| Web release checks | VERIFIED | `npm ci --prefix apps/web --offline`, lint, typecheck, 45 tests across 4 files, production build | Local locked dependencies | Offline installation is not a fresh vulnerability advisory scan |
| Deployment/control-plane boundary | VERIFIED | `python scripts/check_deployment.py` passed | Local compiled web and source | None for static checks |

Graphify scoped queries (500–650 tokens) located the flow, store and contract seams. New budget symbols were not yet in the existing graph; exact Money-owned files were then inspected. Root coordinates the final graph refresh to avoid parallel graph writes. Python testing guidance informed crash-window, rollback, idempotency and adversarial isolation checks.

Remaining conservative limitation: if an invocation was admitted but the process dies without a sealed result, actual usage cannot be established. Its maximum reservation remains charged. This avoids treating potentially billable interrupted work as free.
