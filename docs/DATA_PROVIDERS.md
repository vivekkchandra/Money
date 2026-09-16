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
| Companies House public API | API-key Basic auth, company-number mapping, bounded paginated filing index, official links | Index retrieval is not accounts extraction; current retrieval is not original historical availability. Streaming XBRL converter is separate. Redistribution terms require review |
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
- [EODHD price/volume semantics](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes)
- [EODHD splits and dividends](https://eodhd.com/financial-apis/api-splits-dividends)
- [EODHD news endpoint](https://eodhd.com/financial-apis/stock-market-financial-news-api)
- [EODHD exchange identifiers](https://eodhd.com/financial-apis/exchanges-api-list-of-tickers-and-trading-hours)
- [HMRC SDRT guidance](https://www.gov.uk/hmrc-internal-manuals/stamp-taxes-shares-manual/stsm031030)
- [Takeover Panel levy](https://www.thetakeoverpanel.org.uk/disclosure/ptm-levy)

These references informed implementation; no live account entitlement, legal
licence conclusion or production dataset qualification has been fabricated.
