"""Secret-safe, read-only deployment diagnostics; never a production qualification.

Run ``python -m money.research.preflight --role api`` or ``--role worker`` in the
configured container. This does not import the API, contact a broker/provider,
migrate a database, promote a model, run inference or manufacture proof files.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from money.api.settings import Settings
from money.data.source_policy import IssuerSourcePolicy, issuer_source_policy
from money.research.live import load_manifest
from money.research.qlib_mode import qlib_enabled
from money.schemas.contracts import utc_now


def deployment_preflight(role: Literal["api", "worker"]) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def check(name: str, status: str, code: str) -> None:
        checks.append({"name": name, "status": status, "code": code})

    required = {
        "MONEY_ENV": "production",
        "MONEY_DEPLOYMENT_ENV": "hosted",
        "MONEY_RESEARCH_MODE": "live",
        "MONEY_ENABLE_SYNTHETIC_DEMO": "false",
    }
    for name, expected in required.items():
        valid = os.environ.get(name, "").lower() == expected
        check(name, "VERIFIED_OFFLINE" if valid else "BLOCKED_CONFIGURATION",
              "EXPECTED_SETTING_PRESENT" if valid else "EXPLICIT_PRODUCTION_SETTING_REQUIRED")
    token = os.environ.get("RESEARCH_API_TOKEN", "")
    check("RESEARCH_API_TOKEN", "PRESENT_NOT_TESTED" if len(token) >= 32 else "BLOCKED_CREDENTIAL",
          "SERVER_TOKEN_PRESENT" if len(token) >= 32 else "STRONG_SERVER_TOKEN_REQUIRED")

    # Never serialize ValidationError: provider fields, URLs or input values can
    # contain secrets despite otherwise safe exception formatting settings.
    try:
        settings = Settings()  # type: ignore[call-arg]
    except (ValidationError, ValueError, OSError):
        settings = None
        check("settings", "BLOCKED_CONFIGURATION", "SETTINGS_VALIDATION_FAILED")
    else:
        check("settings", "VERIFIED_OFFLINE", "EXISTING_SETTINGS_VALIDATOR_PASSED")

    manifest = None
    path = os.environ.get("MONEY_LIVE_MANIFEST")
    digest = os.environ.get("MONEY_LIVE_MANIFEST_SHA256")
    if not path or not digest:
        check("manifest", "BLOCKED_CONFIGURATION", "PINNED_QUALIFICATION_BUNDLE_REQUIRED")
    else:
        try:
            manifest = load_manifest(Path(path), digest)
            for provider in manifest.provider_qualifications:
                for dataset in provider.datasets:
                    provider.require(dataset, utc_now())
        except (ValueError, OSError):
            manifest = None
            check("manifest", "BLOCKED_CONFIGURATION", "QUALIFICATION_BUNDLE_INVALID_OR_EXPIRED")
        else:
            check("manifest", "VERIFIED_OFFLINE", "SCHEMA_HASHES_AND_DATED_PROVIDER_RECORDS_VALID")

    if role == "worker":
        try:
            selected_source_policy = (
                manifest.issuer_source_policy if manifest else issuer_source_policy(os.environ)
            )
        except ValueError:
            selected_source_policy = IssuerSourcePolicy.COMPANIES_HOUSE
            check("issuer_source_policy", "BLOCKED_CONFIGURATION", "ISSUER_SOURCE_POLICY_INVALID")
        credential_names = {
            "broker_metadata_key": "TRADING212_API_KEY",
            "broker_metadata_secret": "TRADING212_API_SECRET",
            "market": manifest.market_credential_environment_variable if manifest else "EODHD_API_KEY",
        }
        if selected_source_policy == IssuerSourcePolicy.COMPANIES_HOUSE:
            credential_names["filings"] = (
                manifest.filings_credential_environment_variable if manifest else "COMPANIES_HOUSE_API_KEY"
            )
        else:
            check("filings_credential", "NOT_REQUIRED", "OFFICIAL_DISCLOSURES_SELECTED_EVIDENCE_STILL_REQUIRED")
        if manifest:
            for name in ("tradingagents", "ai_hedge_fund", "crewai"):
                selection = getattr(manifest, name)
                if getattr(selection, "authentication", "bearer") == "none":
                    check(name + "_credential", "NOT_REQUIRED", "INFERENCE_AUTHENTICATION_NONE")
                else:
                    credential_names[name] = selection.credential_environment_variable
        else:
            check("inference", "BLOCKED_CONFIGURATION", "REVIEWED_INFERENCE_SELECTIONS_REQUIRED")
        for component, name in credential_names.items():
            present = bool(os.environ.get(name, "").strip())
            # Report the stable component, not a potentially sensitive custom
            # environment variable name or its value from an invalid manifest.
            check(component + "_credential", "PRESENT_NOT_TESTED" if present else "BLOCKED_CREDENTIAL",
                  "CREDENTIAL_PRESENT_NOT_QUALIFIED" if present else "WORKER_CREDENTIAL_REQUIRED")

    # Only a live metadata refresh joined to fresh reviewed proofs can establish
    # the current admitted universe. Offline presence never establishes coverage.
    check("current_stock_universe", "NOT_VERIFIED", "LIVE_METADATA_AND_FRESH_REVIEW_JOIN_NOT_RUN")
    try:
        enabled = getattr(manifest, "qlib_enabled", True) if manifest is not None else qlib_enabled(os.environ)
    except ValueError:
        enabled = True
        check("qlib_mode", "BLOCKED_CONFIGURATION", "MONEY_QLIB_ENABLED_REQUIRES_TRUE_OR_FALSE")
    check(
        "promoted_qlib_model", "NOT_VERIFIED" if enabled else "NOT_REQUIRED",
        "LIVE_ACCEPTANCE_NOT_RUN" if enabled else "QLIB_EXPLICITLY_DISABLED",
    )
    for component in ("postgresql", "native_firms", "lean", "host_egress", "hosted_e2e"):
        check(component, "NOT_VERIFIED", "LIVE_ACCEPTANCE_NOT_RUN")
    return {
        "schema": "money-live-preflight-v1",
        "scope": "OFFLINE_DIAGNOSTICS_ONLY",
        "role": role,
        "production_status": "PRODUCTION BLOCKED",
        "settings_validated": settings is not None,
        "manifest_validated": manifest is not None,
        "checks": checks,
        "limitation": "No live services, eligibility feed, runtime, model, credentials or usage rights were qualified. No state was changed.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("api", "worker"), required=True)
    arguments = parser.parse_args()
    report = deployment_preflight(arguments.role)
    print(json.dumps(report, indent=2))
    # A diagnostic is never a release gate success, including when a genuine
    # manifest satisfies the existing offline validator.
    raise SystemExit(2)


if __name__ == "__main__":
    main()
