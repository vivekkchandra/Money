# UK provider scope and qualification — checked 2026-09-16

No provider is automatically production-qualified because an endpoint responds.
`ProviderQualification` records datasets, GB geography, STOCK types, currencies,
historical depth, publication-time status, freshness, reviewer/artifact hash,
usage purpose, storage/redistribution status and attribution. `load_manifest`
checks referenced qualification bytes; live fetches enforce declared coverage.
There is no silent provider fallback.

| Source | Implemented use | Limitation / licensing decision |
| --- | --- | --- |
| Trading 212 instrument metadata | Optional authenticated GET metadata only; bounded whitelisted fields and explicit ISIN/ticker mapping | No ISA flag inferred. Independently reviewed ISA/current availability proof is required. No balance/positions/orders/portfolio endpoints exist in Money |
| Companies House public/Document APIs | API-key Basic auth, bounded filing index, exact reviewed accounts-document retrieval, negotiated machine-readable representation and streaming XBRL conversion into snapshot facts | Reviewed GBP accounting units must match exact document bytes; exact storage hosts need review; PDF-only filings fail closed. Retrieval is not original historical publication. Raw redistribution stays prohibited |
| EODHD | Explicit `.LSE` mapping, historical raw OHLCV, news links/titles, splits/dividends | Subscription/UK entitlement and licence must be verified. Raw OHLC and split-adjusted volume cannot be mixed across splits: those windows fail closed. Rights/spin-offs/delistings need separately verified coverage |
| Reviewed publication archive | Hash-referenced Money OHLCV records with original-publication qualification | No timestamp inferred/backdated. Overlap with current provider must match exactly. Stable economic dataset hash survives snapshot rebinding |
| Reviewed supplemental facts | Versioned eligibility, ethical business activities, spread, financial facts and corporate-action completeness | Administrator review is explicit; this is not a live automated source. Stale, unknown or conflicting material information blocks research |
| yfinance | Development/tests/diagnostics only | Qualification rejects it as production authority |

Source text is untrusted. Fetchers enforce HTTPS allowlists, public address pinning,
DNS/connect/read/absolute deadlines, MIME/size/redirect/decompression bounds and
markup cleanup. XBRL/archive parsing rejects oversized members, traversal/symlinks,
DTD/entities and unknown unit/company mappings. Only bounded facts and links are
exposed by these adapters; no right to redistribute full articles is presumed.
Any broader raw-content redistribution requires recorded licence review.

Optional live document ingestion is explicit, not a provider fallback. Each
`VerifiedInstrument.filing_documents` entry names a selected transaction and
content-bound `FinancialCurrencyProof` (maximum four). The live manifest's exact
`filing_document_storage_hosts` (maximum eight) reference reviewed artifacts.
Missing qualification, changed document bytes, unsafe redirects, unsupported MIME,
missing native parser or unusable facts stop the snapshot even if supplemental
facts exist. No API caller chooses a retrieval URL or remote hostname.

The snapshot includes normalized financial facts and a bounded filing-provenance
sidecar: raw/document/index hashes, canonical official metadata URL, retrieval
availability and review identities. It excludes raw accounts and signed download
URLs. Record expiry cannot exceed provider maximum age, qualification validity or
identifier validity. Fact and sidecar share a canonical source, not independent
confirmation. Detailed official references and unresolved qualification are in
WORK_FILINGS.md. Automatic discovery/units qualification and historical filing
availability reconstruction are not implemented by this selected-document path.

Default liquidity/discovery thresholds are versioned screening policy, not
empirically calibrated promises: minimum 60 bars, £100k average daily value,
maximum 100-bps spread, no recent zero-volume bars, bounded gaps/outliers.
Financial change alerts explicitly do not assume accounting-period comparability;
the CIO must verify that before accepting a substantive claim.

## Transaction costs

`risk/costs.py` stores dated, sourced applicability rules, including the reviewed
SDRT and PTM parameters. Unknown applicability or unspecified broker/other fees
cannot become a zero-cost assumption. Current rules are deliberately not
backfilled over a historical study; LEAN requires a separately reviewed dated
cost configuration covering its complete interval. Exemptions need an explicit
instrument-specific reason. This configuration is research, not tax advice.

## Official references

- [Trading 212 instrument endpoint](https://docs.trading212.com/api/instruments/instruments)
- [Companies House authentication](https://developer.company-information.service.gov.uk/authentication)
- [Companies House filing history](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference/filing-history/list)
- [Companies House document metadata](https://developer-specs.company-information.service.gov.uk/document-api/reference/document-metadata/fetch-a-documents-metadata)
- [Companies House document content](https://developer-specs.company-information.service.gov.uk/document-api/reference/document-location/fetch-a-document)
- [EODHD price/volume semantics](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes)
- [EODHD splits and dividends](https://eodhd.com/financial-apis/api-splits-dividends)
- [EODHD news endpoint](https://eodhd.com/financial-apis/stock-market-financial-news-api)
- [EODHD exchange identifiers](https://eodhd.com/financial-apis/exchanges-api-list-of-tickers-and-trading-hours)
- [HMRC SDRT guidance](https://www.gov.uk/hmrc-internal-manuals/stamp-taxes-shares-manual/stsm031030)
- [Takeover Panel levy](https://www.thetakeoverpanel.org.uk/disclosure/ptm-levy)

These references informed implementation; no live account entitlement, legal
licence conclusion or production dataset qualification has been fabricated.
