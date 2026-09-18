"""Explicit personal research entry point; never modifies Railway configuration."""

from __future__ import annotations

import os

from money.qualification.runner import SafeArgumentParser, main


def run() -> int:
    parser = SafeArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--instrument", help="Exact Trading 212 ID for one explicit pipeline test")
    selection.add_argument("--isin", help="Select exactly one current non-conflicting ISIN")
    parser.add_argument("--output")
    args = parser.parse_args()
    # This dedicated local workflow makes its purpose explicit, without changing
    # injected hosted settings or any persistent Railway variables.
    selected = dict(os.environ)
    selected["MONEY_USAGE_MODE"] = "personal_research"
    selected["MONEY_QLIB_ENABLED"] = "false"
    if args.instrument:
        selected["MONEY_RESEARCH_TEST_INSTRUMENT"] = args.instrument
    if args.isin:
        selected["MONEY_RESEARCH_TEST_ISIN"] = args.isin
        selected.pop("MONEY_RESEARCH_TEST_INSTRUMENT", None)
    return main(["--output", args.output] if args.output else [], environment=selected)


if __name__ == "__main__":
    raise SystemExit(run())
