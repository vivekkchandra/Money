"""Validate release-owned tables without writing any files or contacting providers."""

import argparse
import json
from pathlib import Path

from money.reference import (
    ReferenceTableError,
    compare_reference_releases,
    load_reference_catalog,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--previous-data-root", type=Path)
    args = parser.parse_args()
    try:
        catalog = load_reference_catalog(args.data_root)
        if args.previous_data_root:
            compare_reference_releases(load_reference_catalog(args.previous_data_root), catalog)
    except ReferenceTableError:
        print(json.dumps({"status": "FAILED", "error": "REFERENCE_TABLE_INVALID"}))
        return 1
    print(json.dumps({"status": "VERIFIED", "tables": catalog.diagnostics()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
