# Failure modes

| Failure | Required result |
| --- | --- |
| Unknown/stale eligibility, non-stock, non-GBP/GBX, prohibited activities | REJECTED; no full research |
| Critical stale/conflicting evidence or historical publication unknown | Exclude unsafe historical data; suppress signal |
| Missing first-pass firm, mismatched snapshot, duplicate report | Barrier remains closed; fail job |
| LEAN unavailable / insufficient validation | INSUFFICIENT_EVIDENCE; no signal |
| CIO audit fails, unresolved contradiction, Red Team veto | No signal |
| Duplicate article from multiple firms/providers | Count one source; reduce independence |
| Worker dies | Lease expires; deterministic failure/recovery; no stale writes |
| API/Netlify unavailable | PostgreSQL state remains durable; UI explains unavailable status |
| Signal expired or event invalidated | Never displayed active, even before background sweep |
| Production credentials/runners absent | Fail closed; never substitute fixtures |

Errors sent to clients use bounded safe codes/messages; provider payloads, tokens, database URLs and exception traces stay out of responses.

Native runners must have enforced execution deadlines before live qualification. Worker leases fence crashed/lost workers; a still-heartbeating hung native library is a separate failure and cannot be solved by a lease alone. The current fixture runtime is bounded; no unqualified native runner is enabled by deployment configuration.
