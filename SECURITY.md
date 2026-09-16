# Security and commercial launch boundary

Money is research software and never executes broker orders. Native research
remains outside Netlify. Customer authentication is a separate boundary from the
server-to-server API credential. Workspace membership is checked on every request;
UUID knowledge grants no access. OWNER/ADMIN/MEMBER/VIEWER permissions are enforced
server-side. Internal diagnostics additionally require an operator UUID allowlist.

Passwords use bounded-memory scrypt; account/action tokens are random and stored
as hashes. Verification/reset links use URL fragments, expire and are single-use.
Email outbox content is encrypted at rest with an external key. HttpOnly Secure
SameSite cookies, same-origin mutation checks, durable rate limits, expiry,
rotation and revocation protect browser sessions. Password reset revokes sessions.
No payment state supplied by the browser creates an entitlement.

Customer/product data lives in PostgreSQL. Git tables contain only reviewed public
reference/configuration assets with schema, provenance and SHA256 validation.
Unknown commercial redistribution permission blocks research evidence exposure.

Sensitive audit and usage records are append-oriented/database immutable. Deletion
requests disable customer access and revoke sessions; they do **not** silently
cascade-delete billing/audit/research evidence. Reviewed fulfilment pseudonymises
eligible mutable identity only; recipient suppression hashes prevent delayed
contact-data resurrection. Password-confirmed ownership succession, durable billing
closure and audited operator resolution precede fulfilment. Retention periods,
retained-record exceptions and external erasure still require legal/operator approval.

Tests cover forged sessions, reset/verification reuse, cross-workspace resources,
viewer/owner escalation, invitation seats, quota races, immutable audit, Stripe
signature/time/mode/redirect checks, provider escape, SSRF and prompt injection.
Actual PostgreSQL, live-browser, payment, SMTP and deployed-runtime acceptance are
separate release gates. No penetration test certification is claimed.

Report a vulnerability privately to the repository owner; do not put passwords,
customer records, database URLs or session tokens in public issues. A verified
commercial security-contact address and incident-response ownership remain launch
configuration, not invented contact details.

## Open dependency release blocker — 2026-09-16

The locked research-runtime dependency audit reports five entries representing **four
distinct vulnerabilities in `chromadb==1.1.1`**, a transitive dependency of
`crewai==1.15.21`. The duplicate is PYSEC-2026-311. The reported fixed-version lists
are empty; no unverified replacement version or audit suppression was introduced.

| Advisory | Reported risk | Status |
| --- | --- | --- |
| [PYSEC-2026-311](https://osv.dev/vulnerability/PYSEC-2026-311) | Pre-authentication Chroma HTTP API code injection | Unresolved |
| [PYSEC-2026-3814](https://osv.dev/vulnerability/PYSEC-2026-3814) | Authenticated Chroma HTTP API code injection | Unresolved |
| [PYSEC-2026-3815](https://osv.dev/vulnerability/PYSEC-2026-3815) | Chroma RBAC cross-tenant authorization | Unresolved |
| [PYSEC-2026-3813](https://osv.dev/vulnerability/PYSEC-2026-3813) | Chroma collection cross-tenant authorization | Unresolved |

The default Docker target (API, email and billing) now excludes CrewAI/ChromaDB.
The research target explicitly installs the unchanged locked `research` extra;
development retains it so native regression tests cannot silently disappear.
The separate control-plane audit reports no known vulnerabilities. Research audit
still reports the four advisories. CI audits both sets and Node independently.

Money does not deploy a Chroma HTTP service or use it for customer authorization.
Native subprocesses deny socket listeners and Chroma backend/client/embedding
capabilities, including captured constructor aliases; the Money CIO keeps memory
and tool delegation disabled. Tests exercise actual installed constructors and
the isolated CrewAI Flow with scripted inference. This is containment, **not a
package patch or an arbitrary-code sandbox**. Do not deploy the research image for commercial use until a
reviewed dependency remediation and native-regression qualification are complete.
The dependency CI gate remains failing rather than ignored. Python advisory
retrieval succeeded; the separate Node production advisory retrieval failed DNS
and therefore has no current passing result.

Reproduce using both CI locked requirements exports (`--no-dev`, then
`--no-dev --extra research`) and `pip-audit==2.10.1`, not an
unlocked resolution. A replacement native runtime must follow the pinned-upstream
upgrade/Graphify/contract/replay acceptance process; upstream files stay read-only.
