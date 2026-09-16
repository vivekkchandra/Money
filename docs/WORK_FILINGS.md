# Companies House machine-readable filing documents

Status: implemented adapter and synthetic security/contract tests; **not a qualified live financial source**. No provider request, production configuration, upstream edit, deployment or raw-content redistribution was performed for this work.

## Implemented contract

`money.data.uk.filing_documents.CompaniesHouseFilingDocuments.fetch(identifiers, filing_id, snapshot_id, currency_proof)` returns a Money-owned `FilingDocumentBundle` containing normalized financial evidence and bounded provenance. It:

- Requires current Money identifier mapping and explicit Companies House provider qualification for both `filing` and `financial` datasets.
- Fetches the exact official company filing-history item and verifies its transaction ID and accounts category. Only that item's exact official document metadata link is admitted.
- Selects only an explicitly offered `application/xhtml+xml`, `application/xml` or `application/zip` resource. PDF-only filings produce `FILING_MACHINE_READABLE_UNAVAILABLE`; no OCR, fabricated values or demo fallback exists.
- Uses API-key Basic authentication only on official Companies House origins. A documented 302 content response is handled as one authenticated location lookup followed by a separate, unauthenticated storage download. An administrator must first supply the **exact** storage hostname and its review-evidence hash. No wildcard, automatic redirect-host trust or guessed S3 host is accepted. Same-origin official downloads retain Basic authentication.
- Reuses Money's HTTPS/DNS-public-address/IP-pinning, MIME, identity-encoding, body-size, timeout and archive protections. Metadata is bounded to 500 KB; the representation to 5 MB. Location lookup and each bounded fetch have explicit deadlines. Further download redirects are denied. Archives are subject to existing member count, expansion, traversal, symlink and compression-ratio limits. Only XML/XHTML/HTML members are accepted, with DTD/entities and unqualified encodings denied; initial encoding support is UTF-8 only.
- Passes the retrieved representation to the existing Money `parse_company_archive` seam. A direct XML/XHTML resource is wrapped losslessly in a bounded stored ZIP; the original bytes remain the content-hash authority.
- Requires an administrator-reviewed `FinancialCurrencyProof` for GBP accounting units, bound to the company, transaction ID **and exact raw-document hash**. A GBX/GBP stock quotation is not proof of accounting units. A revised representation cannot reuse an old units review.
- Reseals normalized facts with `provider=companies-house`: stream-read-xbrl is the processor, not an independently qualified financial source. Bundle conversion metadata distinguishes the expected `UPSTREAM_LOCK.txt` SHA from actual source attestation. The default converter verifies the installed standalone module before import, then successful conversion records `PINNED_SOURCE_VERIFIED` plus the exact source-file SHA-256. Explicitly injected test parsers record `INJECTED_UNATTESTED` with no attested hash. Source verification is not a claim that dependencies, financial coverage or live behavior have been fully qualified.
- Reseals fact expiry to the earliest original expiry, qualified provider maximum age, provider admission expiry and identifier review expiry. Already-stale converter output is rejected, never refreshed by downloading again.
- Records the filing processed date and document/representation created/updated timestamps separately. These are **not** treated as historical publication proof. Converted facts use actual retrieval as their conservative publication/availability time; original publication remains unknown. This ingestion cannot backfill PIT-sensitive historical financial availability.
- Retains SHA-256 hashes of the exact content, metadata and filing-index response, a canonical metadata URL, and a reviewed storage host/hash. Signed redirect URLs are neither returned nor persisted. Raw content is an explicitly excluded, non-repr field for internal conversion/restricted storage only; `model_dump()` and JSON serialization omit it.
- Preserves raw redistribution as false and licensing status `REVIEW_REQUIRED`. Normalized facts and source links still need a reviewed usage policy before production admission.

The adapter is connected to `LiveSnapshotBuilder` **only for explicitly selected reviewed filings**. `VerifiedInstrument.filing_documents` accepts zero to four `FinancialCurrencyProof` entries; `LiveManifest.filing_document_storage_hosts` accepts zero to eight individually reviewed exact hosts. Duplicate filing IDs/content hashes, company mismatch and missing financial dataset coverage are rejected. `load_manifest` verifies the bytes behind every selected currency review and storage-host review against `qualification_artifacts`. A credential or technically reachable URL does not constitute provider qualification.

Each selected document is required: failure propagates through the durable `companies-house:filing-document` provider circuit, even if supplemental financial facts exist. The immutable snapshot receives converted facts and one bounded `DocumentFact` provenance sidecar per document. The sidecar retains safe hashes, conversion metadata and official source link without raw document bytes, signed download URLs or duplicated financial payloads. Its canonical source matches its facts to avoid counting processing metadata as independent confirmation. Snapshot/decision-packet hashing anchors the sidecar; the factory-time source manifest is never mutated after download. There is no automatic filing selection or historical publication backfill. Native parser/package assembly and actual source qualification remain external prerequisites.

Caller-owned transport/parser injection exists for deterministic tests; production uses the bounded transport and existing pinned-parser seam by default. Conversion executes in the supervised compute worker, not a Netlify/API request. The Python configuration skill guided typed, bounded manifest validation and explicit review artifacts rather than implicit provider fallback.

## Official documentation consulted

Verified via web during implementation on 2026-09-16:

| Official reference | Behavior used |
| --- | --- |
| [Authentication](https://developer.company-information.service.gov.uk/authentication) | Read API key uses Basic authentication: key as username, blank password. |
| [Specific filing-history item](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference/filing-history/filinghistoryitem-resource) | Exact company/transaction endpoint binds the requested filing. |
| [Filing item schema](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/filinghistoryitem?v=latest) | `date` is processing date; `links.document_metadata` identifies the document. |
| [Document metadata endpoint](https://developer-specs.company-information.service.gov.uk/document-api/reference/document-metadata/fetch-a-documents-metadata) | Official authenticated `/document/{document_id}` endpoint. |
| [Document metadata schema](https://developer-specs.company-information.service.gov.uk/document-api/resources/documentmetadata?v=latest) | Available MIME representations and declared lengths; created/updated timestamps do not prove historical publication. |
| [Document content endpoint](https://developer-specs.company-information.service.gov.uk/document-api/reference/document-location/fetch-a-document) | Negotiated `Accept`; documented 302 location; unsupported representations return 406. |
| [Developer guidelines](https://developer.company-information.service.gov.uk/developer-guidelines) | TLS, key hygiene and bounded API usage. |
| [API testing](https://developer.company-information.service.gov.uk/api-testing) | Document API does not have a sandbox. No synthetic fixture is labelled a real sandbox document. |

No current official specification reviewed here establishes a deployment-specific storage-host allowlist or a blanket right to redistribute company-supplied accounts. Older forum examples are not used as host qualification or licensing evidence.

## Graphify-first discovery

| Question / graph | Query / budget | Symbols and minimal inspection | Decision |
| --- | --- | --- | --- |
| Money filing provenance and security seams / `graphify-out/graph.json` | `graphify query 'CompaniesHouseProvider SafeFetcher XBRL document publication availability' --budget 650` | `CompaniesHouseProvider`, `SafeFetcher`, `parse_company_archive`; targeted `uk/live.py`, `uk/xbrl.py`, `data/security.py`, provider qualification/identifier contracts | Reuse existing Money transport/archive checks and canonical financial conversion; add no upstream-native public objects. |
| Native bounded conversion / `upstreams/stream-read-xbrl/graphify-out/graph.json` | `graphify query 'stream_read_xbrl parse stream_read_xbrl' --graph upstreams/stream-read-xbrl/graphify-out/graph.json --budget 600` | `_xbrl_to_rows`, `_parse`, `stream_read_xbrl_zip`; only upstream `stream_read_xbrl.py` lines 74–102 inspected | Preserve Money's single-member conversion seam without upstream process-pool fanout; no upstream files modified. |
| Optional reviewed snapshot assembly / Money graph | `graphify query 'LiveManifest VerifiedInstrument LiveSnapshotBuilder qualification_artifacts source_provenance' --budget 650` | Targeted `research/live.py` manifest/load/snapshot sections and Money contracts | Add explicit reviewed selections and sealed provenance sidecars, not mutation of already-pinned job provenance. |
| Pre-import native parser qualification / Money graph | `graphify query 'native_attestation source_fingerprint attest_native parse_company_archive' --budget 650` | Existing `native_attestation.py` and `uk/xbrl.py`; only pinned upstream file hash was read, no additional upstream source inspection | Separate bounded single-module source check; never recursively fingerprint site-packages. Existing four package checks unchanged. |

Pinned stream-read-xbrl SHA remains `b95b48bbf50727648cebcba56634b17dc9e60ad3`. The root agent coordinates the final Money graph refresh; upstream graph refresh is not required.

The read-only checkout HEAD was verified against that lock SHA, and `git status --short -- stream_read_xbrl.py` returned no changes. `shasum -a 256 upstreams/stream-read-xbrl/stream_read_xbrl.py` returned `afff17f5a281e2474dcf11d6c8d407c08522cd144d7d075631e6e09c6ebc8b33`. This constant admits only the exact installed standalone source, not a package tree, symlink, arbitrary loader or bytecode-only module. Source reads are bounded to 5 MB and happen before `import_module`; absent or mismatched source fails closed.

## Verification and remaining assembly

| Requirement | Status | Evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Bounded metadata → negotiated content → Money conversion contract | VERIFIED | New synthetic unit suite; checks IDs, MIME/length, archive and encoding rejection, unit-review binding, original hashes, serialization and retrieval-only PIT | Local Python; injected HTTP/parser only | Not evidence of live provider coverage |
| Auth never forwarded to a reviewed cross-origin storage host | VERIFIED | Real transport orchestration tested with pinned-connection and HTTP fixtures; checks exact Basic first hop and absent Authorization second hop | Local unit tests | Actual redirect/storage-host deployment qualification pending |
| Ruff and mypy | VERIFIED | Targeted Ruff and mypy commands pass | Local Python environment | None for these files |
| Actual native parser availability | BLOCKED_EXTERNAL_INFRA | `importlib.util.find_spec('stream_read_xbrl')` returned false | Existing local environment | Pinned native package assembly/lock and native fixture qualification remain required |
| Credential-backed official document retrieval | BLOCKED_CREDENTIAL | Not run; unit fixtures do not use credentials | Local tests only | Configured Companies House key and permission to run opt-in qualification against a selected filing |
| Production host/source/licensing admission | NOT_IMPLEMENTED | No deployment host allowlist or licensing assertion created | Production operator review | Reviewed exact storage host(s), provider qualification record, usage/storage/licensing decision, exact-document GBP accounting-unit proof |
| Reviewed document → live snapshot assembly | VERIFIED | Synthetic assembly tests exercise required selection, circuit path, normalized facts, safe sidecar, freshness, unchanged manifest and tamper-evident snapshot hash | Local Python; injected provider transport/parser only | Credential-backed end-to-end execution not performed |
| Opt-in genuine document qualification suite | VERIFIED | `tests/production/test_data_live.py::test_real_reviewed_machine_readable_filing_document` uses real default transport/parser; absent selection/credentials and absent parser/external availability are explicit skips; schema/PIT/security mismatches fail | Ordinary CI skips without explicit opt-in | Reviewed manifest, credentials and pinned native parser required to execute live |

Commands and final counts are recorded below after the completed assembly checks. No live source success is implied by synthetic tests or an opt-in skip.

- `.venv/bin/ruff check src/money/data/uk/filing_documents.py src/money/research/live.py tests/unit/test_filing_documents.py tests/production/test_data_live.py` → passed.
- `.venv/bin/mypy src/money/data/uk/filing_documents.py src/money/research/live.py` → passed, two source files.
- Initial assembly check: `.venv/bin/pytest tests/unit/test_filing_documents.py tests/unit/test_source_security.py tests/unit/test_live_data_scanners.py tests/integration/test_runtime_pinning.py tests/production/test_data_live.py -q` → **166 passed, 6 skipped in 1.25s**. All six production-data skips explicitly report `SKIPPED_MISSING_CREDENTIAL: production integration opt-in absent`; no genuine document/provider qualification was executed.
- Final source-attestation closure: targeted Ruff passed; `.venv/bin/mypy src/money/adapters/native_attestation.py src/money/data/uk/xbrl.py src/money/data/uk/filing_documents.py` passed (three files); `.venv/bin/pytest tests/unit/test_native_single_module.py tests/unit/test_filing_documents.py tests/unit/test_native_qualification.py -q` → **167 passed in 8.53s**, with 26 existing CrewAI deprecation warnings. This includes 13 single-module attestation tests and 102 filing-document/assembly tests, plus the existing native qualification regression suite. Genuine parser-source hash acceptance was checked against the read-only pinned file without importing its unavailable dependency stack; scripted native test fixtures do not count as live financial qualification.
