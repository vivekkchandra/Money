# Provider and security review — 2026-09-16

Concrete fixes from the independent review:

- EODHD OHLC retrieval now requires qualified corporate-action coverage and fetches
  the split/consolidation history through retrieval time before accepting bars.
  Raw OHLC with split-adjusted volume is rejected if any split is present in the
  requested window. Accepted records preserve their raw values and explicitly
  record `RAW_OHLC_VOLUME_NO_SPLITS_IN_WINDOW_V1` in source provenance. No unverified
  volume reconstruction is attempted. Original-publication history is still not
  inferred from ex-dates or trading dates.
- Provider geography, instrument type, currency, qualified historical coverage and
  requested date window are checked. Companies House uses fresh identifier mappings
  and strict date-only fields. Provider symbol names and mappings have syntax bounds.
- Market-quality checks reject conflicting/stale critical prices and invalid
  non-finite spreads. The technical scanner rejects currency mismatch or stale
  inputs, and rejects inverted RSI policy intervals. GBP/GBX equivalence tests use
  correctly matched instrument currencies.
- DNS resolution has a bounded caller deadline and a maximum of 16 resolver threads.
  Timeouts cannot create an unbounded resolver pool. Connected addresses remain
  pinned, with multicast and embedded private-address forms rejected. An absolute
  socket shutdown deadline also covers deliberately slow HTTP headers. Custom
  transport headers cannot change Host, compression or inject CRLF.
- ZIP parsing rejects symlink metadata, Windows drive paths and control characters,
  in addition to prior traversal, size and compression limits. Untrusted text limits
  reject negative or unbounded display sizes.
- Cost-rule windows must be ordered and documented; the PTM explanation derives
  from the configured rule instead of hardcoding an assumption that becomes false
  if a versioned threshold changes.

Focused source/security/scanner/cost tests passed (63 tests at review completion),
including injected slow DNS/headers, poisoned source responses and mismatched
currency fixtures. Ruff passes for the reviewed files. Full-project checks are
recorded by the integrating agent in VERIFICATION.md.

Qualification limits remain: corporate actions beyond splits/dividends (rights,
spin-offs, delistings, special distributions) need a fully qualified feed/policy;
raw histories containing actions conservatively fail the market gate. Restored
historical publication availability is not established by these feeds. EODHD
licensing, exchange-currency mappings, live data completeness and successful
credentialed retrieval require deployment verification. Companies House currently
provides filing-index metadata, not a claim that financial statement facts were
extracted. Trading 212 metadata does not attest account-specific ISA eligibility.

Official documentation checked: [EODHD corporate actions](https://eodhd.com/financial-apis/api-splits-dividends),
[HMRC principal SDRT charge](https://www.gov.uk/hmrc-internal-manuals/stamp-taxes-shares-manual/stsm031030),
[Takeover Panel levy](https://www.takeoverpanel.org.uk/disclosure/ptm-levy).
The EOD historical-data documentation URL timed out during this review; the
raw-price/split-volume inconsistency was independently identified by the integrating
agent and is conservatively blocked, not silently normalized.
