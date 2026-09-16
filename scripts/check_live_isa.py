"""Select current reviewed ISA candidates for opt-in production acceptance.

MONEY_RUN_PRODUCTION_INTEGRATION=1 uv run python scripts/check_live_isa.py --select-only

Requires MONEY_LIVE_MANIFEST and MONEY_LIVE_MANIFEST_SHA256. This read-only
preflight makes no provider/inference calls, writes no jobs, and never certifies
live research, licensing, or deployment. There is no default ticker or fallback.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from money.data.instruments import InstrumentCatalogue
from money.data.universe import IsaUniverseQuery, ReviewedIsaUniverse
from money.research.live import LiveManifest, VerifiedInstrument, load_manifest
from money.schemas.contracts import ResearchMandate, utc_now


class LiveAcceptanceFailure(RuntimeError):
    """Stable, secret-safe outcomes shared with the production test harness."""

    def __init__(self, status: str, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(f"{status}: {code}")


def configured_manifest(environment: Mapping[str, str] | None = None) -> LiveManifest:
    values = os.environ if environment is None else environment
    if values.get("MONEY_RUN_PRODUCTION_INTEGRATION") != "1":
        raise LiveAcceptanceFailure("SKIPPED_MISSING_CREDENTIAL", "LIVE_ACCEPTANCE_OPT_IN_REQUIRED")
    path, digest = values.get("MONEY_LIVE_MANIFEST"), values.get("MONEY_LIVE_MANIFEST_SHA256")
    if not path or not digest:
        raise LiveAcceptanceFailure("SKIPPED_MISSING_CREDENTIAL", "QUALIFICATION_MANIFEST_REQUIRED")
    try:
        if not Path(path).is_file():
            raise LiveAcceptanceFailure(
                "BLOCKED_EXTERNAL_INFRA", "QUALIFICATION_MANIFEST_UNAVAILABLE"
            )
        return load_manifest(Path(path), digest)
    except LiveAcceptanceFailure:
        raise
    except OSError as error:
        raise LiveAcceptanceFailure(
            "BLOCKED_EXTERNAL_INFRA", "QUALIFICATION_MANIFEST_UNAVAILABLE"
        ) from error
    except Exception as error:
        raise LiveAcceptanceFailure("FAILED", "QUALIFICATION_MANIFEST_INVALID") from error


def select_candidates(
    manifest: LiveManifest,
    now: datetime,
    *,
    limit: int = 1,
    ticker: str | None = None,
    require_filing_documents: bool = False,
) -> tuple[VerifiedInstrument, ...]:
    """Select only current eligible reviewed instruments, never list position zero."""
    if not 1 <= limit <= 3:
        raise LiveAcceptanceFailure("FAILED", "ACCEPTANCE_SELECTION_LIMIT_INVALID")
    try:
        catalogue = InstrumentCatalogue.from_manifest(manifest)
        if require_filing_documents:
            document_tickers = {
                item.metadata.ticker for item in manifest.instruments if item.filing_documents
            }
            catalogue = replace(
                catalogue,
                entries=tuple(
                    item for item in catalogue.entries if item.metadata.ticker in document_tickers
                ),
            )
        if ticker is not None:
            # Exact canonical matching; a substring is never a substitute for the
            # configured native snapshot's original instrument identity.
            catalogue = replace(
                catalogue,
                entries=tuple(item for item in catalogue.entries if item.metadata.ticker == ticker),
            )
        page = ReviewedIsaUniverse(catalogue).page(
            IsaUniverseQuery(limit=limit),
            mandate=ResearchMandate(),
            now=now,
        )
    except Exception as error:
        raise LiveAcceptanceFailure("FAILED", "QUALIFIED_ISA_UNIVERSE_INVALID") from error
    if not page.instruments:
        raise LiveAcceptanceFailure("FAILED", "NO_CURRENT_VERIFIED_ISA_CANDIDATE")
    by_ticker = {item.metadata.ticker: item for item in manifest.instruments}
    return tuple(by_ticker[item.ticker] for item in page.instruments)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--select-only", action="store_true", required=True)
    parser.add_argument("--limit", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--filing-documents", action="store_true")
    args = parser.parse_args(argv)
    try:
        specification = configured_manifest()
        now = utc_now()
        candidates = select_candidates(
            specification,
            now,
            limit=args.limit,
            require_filing_documents=args.filing_documents,
        )
        report = {
            "status": "CANDIDATES_SELECTED",
            "scope": "READ_ONLY_ACCEPTANCE_SELECTION",
            "evaluated_at": now.isoformat(),
            "coverage": "reviewed_manifest",
            "complete_broker_universe": False,
            "production_qualified": False,
            "research_execution": "NOT_RUN",
            "candidates": [
                {
                    "ticker": item.metadata.ticker,
                    "company": item.metadata.company,
                    "currency": item.metadata.quote_currency,
                    "instrument_type": item.metadata.instrument_type,
                    "verified_at": item.metadata.verified_at.isoformat(),
                    "eligibility_proof_hash": item.eligibility_proof_hash,
                    "ethical_proof_hash": item.ethical_proof_hash,
                }
                for item in candidates
            ],
        }
        code = 0
    except LiveAcceptanceFailure as error:
        report = {
            "status": error.status,
            "reason": error.code,
            "candidates": [],
            "production_qualified": False,
            "research_execution": "NOT_RUN",
        }
        code = 1 if error.status == "FAILED" else 2
    print(json.dumps(report, indent=2, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
