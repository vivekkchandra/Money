# Git-managed product tables

Money keeps immutable reference tables in Git and mutable customer state in
PostgreSQL. The pre-change tracked inventory contained no Money-owned CSV/Parquet
or reference-data JSON outside tests/tool/package configuration. No existing
asset was relocated, deleted or migrated into PostgreSQL.

## Release inventory

| Path | Purpose | Schema | Data version | Owner |
| --- | --- | --- | --- | --- |
| `data/reference/plans.csv` | Bounded allowances, seats, retention and feature primitives; no prices | 1 | 1.0.0 | Product and research governance maintainers |
| `data/reference/exclusions.csv` | Auditable core ethical exclusions | 1 | 1.0.0 | Product and research governance maintainers |
| `data/reference/provider_licences.json` | Commercial-use and redistribution inventory | 1 | 1.0.0 | Data governance maintainers |
| `data/reference/provider_datasets.csv` | Provider/dataset relationship inventory | 1 | 1.0.0 | Data governance maintainers |

Exact SHA-256 values live in `data/metadata/*.json` and are emitted by the validator
and `catalog.diagnostics()`, avoiding a separately maintained checksum list.
They identify exact bytes, not canonicalized/reformatted approximations.

## Runtime contract

`from money.reference import load_reference_catalog` loads an immutable catalog.
Editable installs resolve repository `data/`, independent of working directory.
Non-editable wheel/OCI installs set the server-only `MONEY_REFERENCE_DATA_DIR` to
an absolute mounted release-data path (`/app/data` in the OCI image); alternatively
the caller can pass a `Path` explicitly. Missing/relative/corrupt mounts fail
rather than downloading data or looking in the process working directory.
Copy `data/` alongside `src/` in OCI images. Never point it at a writable customer
upload directory or expose a customer-selected path through the API.

The loader validates the complete manifest before returning any data. It checks
metadata, dates, strict schema versions, schema-document compatibility with the
Python contract, hashes, exact ordered CSV columns, bounded UTF-8/JSON/CSV input,
types, value ranges, duplicate primary keys and provider foreign keys. Symlinks,
path traversal, duplicate JSON properties, non-finite numbers and unknown fields
fail. Current registered formats are CSV and JSON; there was no existing Parquet
reference table to migrate. Future formats require an explicit bounded reader,
schema contract and tests before registration.

`catalog.plan("PRO")` returns frozen server-side allowances. Defaults are bounded
launch engineering limits, not final commercial pricing or service guarantees.
Live-research entitlement never overrides credentials, evidence, licensing or
research qualification. FREE is synthetic-only with zero inference-token budget.
Retention values are policy inputs, not an automatic deletion mechanism: customer,
research, billing and audit retention still need their appropriate reviewed
deletion workflows. The exclusion table cannot remove the core mandate's bans.

## Safe update and rollback

1. The documented owner edits the existing Git table; never update it at runtime.
2. Increment its `data_version`, record source/date/provenance and actual update
   timestamp. Version all schema changes; this release accepts schema 1 only.
3. Calculate `shasum -a 256 data/reference/<file>` and the associated schema file;
   update the metadata deliberately. Do not add automatic checksum repair to boot.
4. If schema changes are intended, update both the typed row contract and its
   JSON Schema with compatibility/regression tests. Schemas are checked against
   `ROW_MODELS[name].model_json_schema()`; all fields reject unknown additions.
5. Run `uv run python scripts/validate_data_tables.py` and
   `uv run pytest tests/unit/test_reference_tables.py`.
   When a previous release's data directory is available, also pass
   `--previous-data-root /path/to/previous-release/data`; changed bytes must have
   a newer data version and update timestamp. This is a read-only comparison.
6. Review the data diff, quotas/rights changes and provenance like code; commit,
   run CI, then release immutable application and data versions together.

Rollback restores the previous whole release, not mixed old tables and new code.
Record plan/table versions with mutable subscriptions and decisions when applied;
existing customer allowances must not be retroactively inferred from a later Git
revision. Historical decision evidence remains immutable in PostgreSQL.
Checksums detect corruption; they are not cryptographic signatures proving trust.
Only a reviewed Git release is an authority to change policy.

## Commercial rights: denied until reviewed

The inventory contains status observations, not licence grants. Before commercial
data use call `catalog.require_commercial_use(provider)`, and before exposing
provider-derived material to customers call it with `redistribution=True`.
Unknown, prohibited, unlisted or expired permissions raise `MISSING_LICENCE`.
Even normalized/repackaged facts must pass the applicable contract review; do
not assume that rewriting a restricted data source removes its restrictions.
This gate does not replace the separately hash-qualified live provider manifest.

`require_manifest_commercial_rights` checks qualification, instrument, supplemental
and archive provider identities before commercial live compute.
`require_snapshot_commercial_rights` rechecks retained snapshots at read time, so
an expired/revoked permission cannot be bypassed with an old decision packet.
Apply it to job packets and firm/audit reports as well as the evidence view.
Only explicit server-side synthetic permission plus a validated `DEMO.L` snapshot
whose entire source set is `money-demo` and `demo:` canonical identifiers bypasses
external licence checks. A ticker label alone never supplies a licence exception.

All current external providers remain blocked for commercial use in the supplied
inventory. To approve a source, its owner must document a non-secret review
reference, scope/storage/retention/attribution conditions and review expiry, then
ship the reviewed table change. Never commit an account secret or private billing
contract. A broad approved provider entry cannot authorize unrelated unreviewed
source data; archives/supplemental data inherit their original source conditions.

Official sources inspected on 2026-09-16:

- [EODHD terms](https://eodhd.com/financial-apis/terms-conditions): personal versus
  commercial use are distinct; a personal subscription is not a SaaS redistribution
  grant. No Money commercial contract was supplied.
- [yfinance documentation](https://ranaroussi.github.io/yfinance/): the library
  explicitly distinguishes its code from underlying Yahoo data rights and
  describes the API as personal-use. Money also prohibits it as production
  authority independent of commercial rights.
- [Companies House developer overview](https://developer.company-information.service.gov.uk/):
  official API availability is not blanket permission for all document content.
  The per-dataset/document privacy and licence review remains unresolved.
- [Trading 212 API terms](https://www.trading212.com/legal-documentation/API-Terms_EN.pdf):
  technical API access does not prove Money has commercial metadata permission.
  Customer account data and execution remain out of scope.

This inventory is operational risk control, not legal advice or legal approval.
