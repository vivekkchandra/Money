"""Bounded preparation for one discovered stock, never an admission authority.

Selection is an operator work priority, not a research recommendation or a
replacement universe. This command cannot sign reviews, build a snapshot, run
firms/LEAN, or write a manifest. Provider retries reuse the existing transport.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from money.data.identifiers import InstrumentIdentifiers
from money.data.provider_probes import ProviderProbeReport
from money.data.security import ProviderFailure, SourceSecurityError
from money.qualification.core import QualificationContext, fingerprint
from money.qualification.diagnostics import read_master
from money.qualification.quant import LeanInputs
from money.qualification.universe import _restore_response, credential_binding
from money.qualification.universe_normalize import normalize_universe
from money.qualification.universe_providers import BulkProviderEnricher
from money.qualification.universe_status import live_metadata_state
from money.schemas.contracts import utc_now

SELECTION = "inputs/first-qualification-selection.json"
OUTPUT = "outputs/first-qualification-candidate.json"


def _lean_readiness(ctx: QualificationContext, evidence: dict[str, Any] | None) -> dict[str, Any]:
    """Validate the existing input shape only; never execute an unlocked study."""
    inputs = ctx.read_json("reviews/lean-inputs.json")
    errors = []
    try:
        LeanInputs.model_validate(inputs)
    except ValidationError as error:
        errors = [
            {"field": ".".join(map(str, item["loc"])), "type": item["type"]}
            for item in error.errors(include_input=False, include_url=False)
        ]
    mode = ctx.read_json("outputs/research-mode.json") or {}
    result = {
        "status": "NOT_EXECUTED",
        "lean_mandatory": True,
        "recorded_qlib_enabled": mode.get("qlib_enabled"),
        "input_contract_valid": not errors,
        "input_errors": errors,
        "input_fingerprint": fingerprint(inputs),
        "historical_publication_verified": (evidence or {}).get(
            "historical_publication_verified", False
        ),
        "execution_prerequisite": "FIRST_PASS_LOCKED for the exact genuine qualified snapshot",
        "required_independent_audits": [
            "historical_eligibility",
            "survivorship",
            "corporate_actions",
            "costs",
        ],
        "required_runtime_outputs": [
            "walk_forward",
            "out_of_sample",
            "mae",
            "mfe",
            "maximum_drawdown",
        ],
        "notes": [
            "Today's accessible-instrument response is not past ISA membership or survivorship proof.",
            "AS_RETRIEVED historical prices do not establish original historical publication availability.",
            "Dividend samples do not establish complete corporate-action/adjustment coverage.",
            "No spread, slippage, zero cost, study approval or pinned image is inferred from OHLCV.",
            "Input schema validation, if successful, is not approval or successful LEAN execution.",
        ],
    }
    ctx.write_json("outputs/first-candidate-lean-readiness.json", result)
    return result


def _saved_evidence(ctx: QualificationContext, row: dict[str, Any]) -> dict[str, Any]:
    """Check bytes and identity; successful retrieval does not confer rights/PIT."""
    identity = InstrumentIdentifiers.model_validate(row.get("identifiers"))
    if (
        identity.trading212_id != row["trading212_id"]
        or identity.isin != row["isin"]
        or identity.quote_currency != "GBX"
    ):
        raise ValueError("FIRST_CANDIDATE_IDENTITY_MISMATCH")
    references = row["provider_evidence"]
    for ref in references:
        ctx.verify_artifact(ref["sha256"], ref["path"])
    reference = row["provider_reports"]["eodhd"]["report_ref"]
    raw = ctx.verify_artifact(reference["sha256"], reference["path"])
    report = ProviderProbeReport.model_validate_json(raw)
    if (
        report.provider != "eodhd"
        or len(report.samples) != 1
        or report.samples[0] != identity
        or fingerprint(json.loads(raw)) != fingerprint(row["provider_reports"]["eodhd"]["report"])
    ):
        raise ValueError("FIRST_CANDIDATE_REPORT_MISMATCH")
    current = (
        identity.verified_at <= ctx.now < identity.valid_until
        and report.retrieved_at <= ctx.now < report.retrieved_at + timedelta(days=1)
    )
    datasets = []
    for item in report.datasets:
        if (
            item.ticker != identity.ticker
            or (
                item.status in {"RETRIEVED", "EMPTY"}
                and not (item.artifact_hash and item.artifact_path)
            )
            or (item.status == "RETRIEVED" and item.record_count == 0)
            or (item.status in {"EMPTY", "FAILED"} and item.record_count != 0)
        ):
            raise ValueError("FIRST_CANDIDATE_DATASET_MISMATCH")
        if item.artifact_hash and item.artifact_path:
            payload = json.loads(
                ctx.verify_artifact(item.artifact_hash, "artifacts/" + item.artifact_path)
            )
            if (
                payload.get("provider") != "eodhd"
                or payload.get("ticker") != identity.ticker
                or payload.get("dataset") != item.dataset
                or len(payload.get("records", [])) != item.record_count
            ):
                raise ValueError("FIRST_CANDIDATE_DATASET_MISMATCH")
        datasets.append(item.model_dump(mode="json"))
    return {
        "identifiers": identity.model_dump(mode="json"),
        "datasets": datasets,
        "provider_report": reference,
        "retrieved_at": report.retrieved_at.isoformat(),
        "identity_valid_until": identity.valid_until.isoformat(),
        "historical_publication_verified": report.historical_publication_verified,
        "financial_documents_verified": report.financial_documents_verified,
        "references": references,
        "current": current,
        "rights_approved_by_preparation": False,
    }


def prepare_first_candidate(
    ctx: QualificationContext, *, refresh_providers: bool = False, max_requests: int = 12
) -> dict[str, Any]:
    """Verify one selected member and optionally retry only its provider work.

    The master universe, status, reviews and provider cursor are never replaced.
    Network mode requires the current credential binding and current membership.
    A failed retry remains separate from the verified historical observations.
    """
    if not 1 <= max_requests <= 12:
        raise ValueError("FIRST_CANDIDATE_BUDGET_INVALID")
    selection = ctx.read_json(SELECTION)
    if not isinstance(selection, dict) or not isinstance(selection.get("rationale"), str):
        raise ValueError("FIRST_CANDIDATE_SELECTION_REQUIRED")
    master, master_hash = read_master(ctx)
    metadata = live_metadata_state(ctx, master)
    if not metadata.get("current"):
        raise ValueError("FIRST_CANDIDATE_CURRENT_LIVE_MEMBERSHIP_REQUIRED")
    _, instruments = _restore_response(
        ctx, master["provenance"]["response_artifacts"]["instruments"]
    )
    # Recompute basic integrity over the complete raw universe, including peers
    # with duplicate identities; never trust a saved row's qualification flag.
    normalized = normalize_universe(instruments, [], observed_at=ctx.now)
    matched = [row for row in normalized if row["trading212_id"] == selection.get("trading212_id")]
    if len(matched) != 1 or not matched[0]["universe_member"] or not matched[0]["identity_valid"]:
        raise ValueError("FIRST_CANDIDATE_UNAMBIGUOUS_GBX_STOCK_REQUIRED")
    current = matched[0]
    current["observed_at"] = metadata["observed_at"]
    current["valid_until"] = metadata["valid_until"]
    saved = [row for row in master["stocks"] if row["trading212_id"] == current["trading212_id"]]
    if len(saved) != 1 or saved[0]["instrument_row_sha256"] != current["instrument_row_sha256"]:
        raise ValueError("FIRST_CANDIDATE_SAVED_ROW_MISMATCH")
    recorded = saved[0]
    if any(
        recorded.get(field) != current.get(field)
        for field in (
            "trading212_id",
            "isin",
            "name",
            "short_ticker",
            "quote_currency",
            "instrument_type",
        )
    ):
        raise ValueError("FIRST_CANDIDATE_SAVED_ROW_MISMATCH")
    evidence = (
        _saved_evidence(ctx, recorded)
        if recorded.get("provider_reports", {}).get("eodhd")
        else None
    )
    # Re-run the existing exact ISIN/currency/type/corroboration join, cache only.
    verifier = BulkProviderEnricher(ctx, offline=True, max_requests=0, clock=lambda: ctx.now)
    provider_identity = None
    try:
        provider_identity = verifier._mapping(current)
    except ProviderFailure:
        # A current cache miss is not corrupt proof, and must not prevent the
        # explicit bounded live retry below. No old mapping becomes current.
        pass
    if provider_identity is not None and evidence is not None:
        identity = evidence["identifiers"]
        if (
            provider_identity != recorded["eodhd_identity"]
            or current["eodhd_symbol"] != recorded["eodhd_symbol"]
            or dict(identity["provider_symbols"]).get("eodhd") != current["eodhd_symbol"]
            or identity["exchange_ticker"] != provider_identity["Code"]
            or identity["exchange"] != provider_identity["Exchange"]
        ):
            raise ValueError("FIRST_CANDIDATE_PROVIDER_IDENTITY_MISMATCH")
    retry: dict[str, Any] = {"status": "NOT_REQUESTED", "network_requests": 0}
    if refresh_providers:
        if credential_binding(ctx.environ) != metadata["credential_binding_sha256"]:
            retry["status"] = "CURRENT_TRADING212_CREDENTIAL_BINDING_REQUIRED"
        elif not ctx.environ.get("EODHD_API_KEY"):
            retry["status"] = "EODHD_CREDENTIAL_REQUIRED"
        else:
            enricher = BulkProviderEnricher(ctx, max_requests=max_requests)
            observation = enricher.enrich(current)
            digest, path = ctx.artifact(observation)
            retry = {
                "status": "OBSERVATIONS_ONLY",
                "network_requests": enricher.requests_used,
                "stages": observation["provider_stage_status"],
                "reasons": observation["provider_reasons"],
                "reference": {"sha256": digest, "path": path},
            }
            # A successful new sample may supersede old observation evidence;
            # a failed refresh never silently borrows a previous ready state.
            provider_identity = observation.get("eodhd_identity")
            evidence = (
                _saved_evidence(ctx, observation)
                if observation.get("provider_reports", {}).get("eodhd")
                else None
            )
            current["eodhd_symbol"] = observation.get("eodhd_symbol")
    output = {
        "version": "money-first-candidate-preparation-v1",
        "status": "PREPARATION_ONLY",
        "prepared_at": ctx.now.isoformat(),
        "selection": selection,
        "source_universe_sha256": master_hash,
        "membership": metadata,
        "trading212_id": current["trading212_id"],
        "isin": current["isin"],
        "company": current["name"],
        "quote_currency": "GBX",
        "verified_provider_identity": provider_identity,
        "provider_symbol": current.get("eodhd_symbol"),
        "available_evidence": evidence
        if evidence and evidence["current"] and provider_identity
        else None,
        "verified_observation_evidence": evidence,
        "recorded_qualification_state": recorded["qualification_state"],
        "recorded_qualification_reasons": recorded["reasons"],
        "missing_evidence": [
            "Current independent account/ISA AND purchase-availability provenance review",
            "Provider and ethical-source rights reviews (access is not permission)",
            "Admitted issuer jurisdiction/company identity and applicable company/filing observations",
            "Complete independently reviewed material exposure evidence",
            "Reviewed supplemental financial, spread, cost and corporate-action coverage evidence",
            "Archived publication/PIT and historical membership/survivorship evidence for LEAN",
        ],
        "provider_retry": retry,
        "eligibility_granted": False,
        "reviews_modified": False,
        "master_universe_modified": False,
        "first_pass": "NOT_RUN_PREREQUISITES_REQUIRED",
        "lean": "NOT_RUN_FIRST_PASS_LOCK_REQUIRED",
        "production_qualified": False,
        "lean_preparation": _lean_readiness(ctx, evidence),
    }
    ctx.write_json(OUTPUT, output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/qualified/local-inference"))
    parser.add_argument("--refresh-providers", action="store_true")
    parser.add_argument("--max-requests", type=int, default=12, choices=range(1, 13))
    args = parser.parse_args(argv)
    try:
        ctx = QualificationContext(args.root, Path.cwd(), dict(os.environ), utc_now())
        with ctx.locked():
            result = prepare_first_candidate(
                ctx, refresh_providers=args.refresh_providers, max_requests=args.max_requests
            )
    except (ValueError, KeyError, TypeError, OSError, ProviderFailure, SourceSecurityError):
        # Never print arbitrary provider exceptions or untrusted input values.
        print(
            "FIRST_CANDIDATE_BLOCKED: verify current membership, selection and linked artifact bytes; no gate changed."
        )
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "output": OUTPUT,
                "trading212_id": result["trading212_id"],
                "network_requests": result["provider_retry"]["network_requests"],
                "provider_retry_status": result["provider_retry"]["status"],
                "eligibility_granted": False,
                "first_pass": result["first_pass"],
                "lean": result["lean"],
            },
            sort_keys=True,
        )
    )
    return 0  # Preparation succeeded, not research/production acceptance.
