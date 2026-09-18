"""Read-only provider admission probes; never invent rights/eligibility/history evidence.

Supply a JSON array of independently verified InstrumentIdentifiers as --samples.
Credentials are read only from the named environment variables. Output contains
artifact digests and bounded status codes, never credentials or request URLs.
Optional --review and --rights-evidence are required for production admission.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter

from money.data.identifiers import InstrumentIdentifiers
from money.data.provider_probes import (
    ProviderAdmissionReview,
    QualificationArtifacts,
    probe_companies_house,
    probe_eodhd,
    qualify_probe,
)
from money.schemas.contracts import utc_now


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("eodhd", "companies-house"), required=True)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/qualified/live/artifacts"))
    parser.add_argument("--review", type=Path)
    parser.add_argument("--rights-evidence", type=Path)
    args = parser.parse_args(argv)
    credential_name = {"eodhd": "EODHD_API_KEY", "companies-house": "COMPANIES_HOUSE_API_KEY"}[
        args.provider
    ]
    credential = os.environ.get(credential_name, "")
    if not credential:
        print(json.dumps({"status": "BLOCKED", "code": "PROVIDER_CREDENTIAL_MISSING"}))
        return 2
    if bool(args.review) != bool(args.rights_evidence):
        parser.error("--review and --rights-evidence must be supplied together")
    try:
        if args.samples.stat().st_size > 2_000_000:
            raise ValueError("samples too large")
        samples = TypeAdapter(tuple[InstrumentIdentifiers, ...]).validate_json(
            args.samples.read_bytes()
        )
        if not 1 <= len(samples) <= 10 or len({item.ticker for item in samples}) != len(samples):
            raise ValueError("bounded distinct samples required")
        now = utc_now()
        for sample in samples:
            sample.require_current(now)
        artifacts = QualificationArtifacts(
            args.output,
            secrets=tuple(
                os.environ.get(name, "")
                for name in (
                    "TRADING212_API_KEY",
                    "TRADING212_API_SECRET",
                    "EODHD_API_KEY",
                    "COMPANIES_HOUSE_API_KEY",
                    "OPENAI_API_KEY",
                    "RESEARCH_API_TOKEN",
                    "DATABASE_URL",
                )
            ),
        )
        probe = probe_eodhd if args.provider == "eodhd" else probe_companies_house
        report = probe(credential, samples, now, artifacts)
        digest, path = artifacts.save(report.model_dump(mode="json"))
        qualified = None
        if args.review:
            if (
                args.review.stat().st_size > 100_000
                or args.rights_evidence.stat().st_size > 2_000_000
            ):
                raise ValueError("review too large")
            review = ProviderAdmissionReview.model_validate_json(args.review.read_bytes())
            qualified = qualify_probe(report, review, args.rights_evidence.read_bytes(), artifacts)
            artifacts.save(qualified.model_dump(mode="json"))
        success = all(item.status != "FAILED" for item in report.datasets)
        output = {
            "status": "QUALIFIED" if qualified else "PROBED" if success else "FAILED",
            "production_qualified": qualified is not None,
            "report_hash": digest,
            "qualification_report_hash": qualified.qualification_report_hash if qualified else None,
            "report_path": str(args.output / path),
            "datasets": [
                {
                    "ticker": item.ticker,
                    "dataset": item.dataset,
                    "status": item.status,
                    "record_count": item.record_count,
                    "error_code": item.error_code,
                }
                for item in report.datasets
            ],
            "qualification": qualified.model_dump(mode="json") if qualified else None,
        }
        print(json.dumps(output, indent=2))
        return 0 if qualified else 2
    except Exception:
        # Do not emit Pydantic values, exception URLs, echoed headers or secrets.
        print(json.dumps({"status": "FAILED", "code": "PROVIDER_QUALIFICATION_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
