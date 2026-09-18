# Failure modes

| Failure | Required result |
| --- | --- |
| Unknown/stale eligibility, non-stock, non-GBX, prohibited activities | REJECTED; no full research |
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

Native subprocess and whole-job deadlines are enforced independently of renewable
leases; a still-heartbeating hung call is terminated. Only classified transient
provider failures receive bounded jittered retries. Permanent bad artifacts,
PIT/ethical/currency failures and malformed output never get optimistic fallback.
Retries keep source/model selections fixed and reuse independently sealed reports.
Missing native runtime configuration produces safe final failure, not demo research.

Provider circuit generation fencing prevents a late old success closing a newly
opened circuit. Half-open probes are serialized. Budget batch failure rolls back
unspent reservations; saved usage reconciles on recovery, while lost unmeasured
invocations stay pessimistically charged. Unknown costs stay unknown.

Current broker membership is not ethical/provider/release qualification and makes
no ISA assertion. Retrieval-time historical
bars are excluded at past decision clocks. Split-adjusted volume mixed with raw
prices, incomplete corporate actions, or conflicting archive/current values block
qualification rather than introducing an adjustment guessed by Money.
