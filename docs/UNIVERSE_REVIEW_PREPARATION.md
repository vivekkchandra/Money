# Bulk review preparation

`money.qualification.universe_reviews.prepare_universe_reviews` produces review
work, not approval. It does not modify an existing human input or qualify a
provider/security/runtime. Policy `money-t212-gbx-stock-universe-v4` does not
require or claim ISA account ownership, account type or buy availability.

Generated outputs under the qualification root:

- `outputs/REVIEW_TASKS.md`: the precise remaining human decisions and fields.
- `outputs/universe-review-tasks.json`: non-secret provenance and rights status.
- `outputs/ethics-work-queue.json`: issuer/security grouping and dossier pointers.
- `outputs/ethical-dossiers/`: machine-prepared evidence packets, never signatures.
- `outputs/universe-review-documents.json`: official documentation URLs and exact
  raw-byte hashes where retrieval actually succeeded. An unavailable document has
  no manufactured hash.

The callable accepts `ctx, rows, provenance`; document retrieval is explicitly
enabled with `fetch_documents=True`. `offline=True` forbids document networking
even when capture is requested. Capture makes at most four unauthenticated,
allowlisted HTTPS GETs: Trading 212 instruments documentation, EODHD terms and
licensing, and Companies House developer guidelines. Valid captures are reused
for seven days; unsuccessful attempts back off for five minutes. The safe
transport enforces public destinations, bounded response size/deadline, and no
redirects. Documentation access never establishes the operator's actual licence.

Legacy `inputs/universe/account-scope.json` and its schema are deprecated and
ignored, not approved. No account review template or signature is generated.
An old `outputs/ACCOUNT_SCOPE_REVIEW.md` preparation is preserved in a hashed
audit artifact before replacing its instructions with a deprecation notice.
Credential binding remains a technical cache/retrieval integrity mechanism;
genuine live response bytes, hash verification, original observation times and
24-hour freshness remain mandatory. None of them assert account type.

Provider rights remain global (`inputs/provider-rights/{provider}.json`). A
current provider review can explicitly cover ethical use through
`review.ethical_research_datasets`, backed by actual licence evidence. That one
approval is reused for all relevant issuers. Existing global
`inputs/universe/source-rights/{provider}.json` approvals remain optional scope
supplements, not another mandatory review. No duplicate template is generated;
existing edited files remain byte-for-byte unchanged on resume.

Dossiers rehash source artifacts, verify the source's exact identity against the
mapped security, and preserve original observation/expiry times. Unapproved or
expired sources are references only; derived descriptions are not trusted.
Current verified Companies House issuer numbers may group share classes. Without
a verified issuer identity, grouping stays per ISIN rather than guessing from
names, venue or incorporation prefix. Filing metadata is never represented as
financial-document content or rights.

Dossier status is `DOSSIER_PREPARATION_ONLY`, not a second assessment or approval.
Member records preserve the screening result. `PASS` is reused, `FAIL` remains
excluded, `UNKNOWN` requires the specific evidence gap/conflict resolved, and
`NOT_YET_SCREENED` is machine acquisition/screening work. The human queue contains
UNKNOWN groups, not a request for an independent second reviewer for every issuer.

The screening engine checks every existing exclusion once using admissible
issuer-wide evidence, recording exact source references, material activities,
result, screening time and expiry. A name, SIC code or missing keyword does not
establish PASS. Current document inputs use
`inputs/universe/ethical-evidence.json`; legacy signed `ethics.json` evidence
remains compatible without creating new signatures. Clearance normally lasts
30 days (configurable), with earlier re-screening for identity, material evidence,
policy changes or credible contradiction. Non-ethical independent supplemental,
native/security, mandatory LEAN and release requirements remain unchanged. See
[the ethical policy](ethical-policy.md).
