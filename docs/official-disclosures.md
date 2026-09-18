# Official issuer disclosures instead of Companies House

Select `MONEY_ISSUER_SOURCE_POLICY=official_disclosures`. Companies House is then
not queried or required as a provider, credential or rights-review dependency.
The omitted setting retains `companies_house` for compatibility with existing
qualified manifests; invalid settings fail closed. New universe policy
`money-t212-gbx-stock-universe-v6` and source-policy changes rebuild derived
classifications, preserving raw responses, timestamps, hashes and reviews.

This changes the source, not the required evidence. Exact live Trading 212 and
EODHD identities must join official legal-name, incorporation/jurisdiction,
company-number and ISIN evidence. Current official issuer pages can corroborate
an exact regulator disclosure; names are never fuzzy-matched. A historical
regulatory disclosure cannot establish current live broker membership.

## Local acquisition and resume

After rotating previously exposed credentials, run from the Mac running Ollama:

```sh
railway run \
  --service Money \
  --environment production \
  -- sh scripts/refresh_personal.sh GB0006389398
```

The helper accepts any ISIN with an existing `inputs/issuer-sources/{ISIN}.json`
selection, not a hard-coded Nichols runtime. It captures that issuer's selected
sources, refreshes the complete live GBX universe, and runs qualification.
It stops if capture/refresh fails. It does not preselect snapshot membership,
approve missing evidence, deploy, trade, print credentials, or alter Railway
service variables. Existing fresh captures/provider observations are reused.

These explicit local settings select personal-use audit metadata, disable Qlib
and retain mandatory LEAN. Personal results cannot create a commercial manifest.
LOCAL OLLAMA != RAILWAY PRODUCTION INFERENCE. Hosted use still requires qualified
HTTPS inference, private-worker egress enforcement and all release/acceptance gates.

## Evidence admission

Capture records actual bytes, original retrieval time, hash and source URL.
Discovery/browser summaries and HTTP 200 JavaScript shells are not evidence of
the requested fact. Cross-host documents require an exact link from a captured
issuer page; allowlists, TLS/SSRF checks and byte limits remain. Unsupported large
PDFs are not silently truncated or declared parsed. Original publication times
are unknown until proved; they are never inferred from URL paths or retrieval.

`OfficialDisclosureProof` binds each document to the exact issuer/security,
qualified filing and GBP financial facts, source URL, actual raw hash, accounting
currency proof, independently reviewed technical extraction, and publication/PIT
evidence. Attach these bytes using `official_disclosure_evidence_files` in the
existing `SupplementalReview`, and admit the exact observations through
`inputs/supplemental-sources.json`. The proof's `conversion_evidence_hash` must
match the actual source-admission review bytes; its document/publication hashes
must match the corresponding observation attachments. No unsigned extraction or
PDF capture becomes financial coverage. Commercial rights approval stays strict;
personal-use audit is not an APPROVED licence.

An evidence-supported one-pass ethical PASS is still required. Current business
changes/acquisitions must be covered; name, sector or absent keywords never clear
exposure. The remaining spread/cost/slippage, corporate-action, historical/PIT,
native/security, sealed independent reports, FIRST_PASS_LOCKED, LEAN, CIO/Red Team
and release requirements are unchanged.
