# Bulk review preparation

`money.qualification.universe_reviews.prepare_universe_reviews` produces review
work, not approval. It does not modify an existing human input, infer ISA account
ownership or purchase availability, or qualify a provider/security/runtime.

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

There is one account review (`inputs/universe/account-scope.json`), not one per
stock. Its existing independent-review, credential-binding, evidence and 24-hour
freshness contract remains unchanged. The Trading 212 endpoint is documented as
all available instruments; this does not establish which account owns a key,
account-specific ISA membership, or current purchase availability. Do not attest
those claims without account-specific evidence. `maxOpenQuantity` is not proof.

Provider rights remain global (`inputs/provider-rights/{provider}.json`). Reuse of
source contents in ethical dossiers additionally requires independently reviewed
ethical-use permission in `inputs/universe/source-rights/{provider}.json`, backed
by actual attached rights evidence. These templates contain no signer or review
timestamp. Existing edited files are preserved byte-for-byte on resume.

Dossiers rehash source artifacts, verify the source's exact identity against the
mapped security, and preserve original observation/expiry times. Unapproved or
expired sources are references only; derived descriptions are not trusted.
Current verified Companies House issuer numbers may group share classes. Without
a verified issuer identity, grouping stays per ISIN rather than guessing from
names, venue or incorporation prefix. Filing metadata is never represented as
financial-document content or rights.

Dossier status is `DOSSIER_PREPARATION_ONLY`, not a reassessment of an existing
ethical decision. Member records preserve `recorded_ethical_state`; preparation
lists every exclusion under `unresolved_exposures` because it resolves none of
them itself. This does not revoke an existing independently validated approval or
claim that such approval requires a duplicate review.

Even rights-approved descriptions and SIC codes do not establish complete ethical
coverage. An independent reviewer still assesses every existing exclusion using
appropriate approved evidence, records actual business activities, and signs the
existing `inputs/universe/ethics.json` contract. Unknown exposure remains unresolved;
excluded exposure remains excluded. No downstream production gate is bypassed.
