# Versioned product assets

This directory contains Git-owned release configuration, not customer state.
`manifest.json` lists the exact supported production tables; each has a strict
schema and metadata containing source, ownership, version and byte checksums.
Existing application assets outside this directory have not been moved or deleted.
Non-editable wheel/OCI installs set `MONEY_REFERENCE_DATA_DIR` to the absolute
immutable release-data path (`/app/data` in the container), never a customer upload.

Run `uv run python scripts/validate_data_tables.py` before a release. See
[DATA_TABLES.md](../docs/DATA_TABLES.md) for the reviewed update workflow.

Never write customer information, credentials, billing state, sessions, usage,
research results or other mutable runtime state here. Runtime code is read-only.
`cache/` and `backtests/` remain ignored scratch directories, not product tables.
