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
from money.qualification.universe_policy import RETIRED_BLOCKERS, UNIVERSE_POLICY_VERSION
from money.qualification.universe_providers import BulkProviderEnricher
from money.qualification.universe_reviews import _rights
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
            "Today's accessible-instrument response is not historical universe membership or survivorship proof.",
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
    if not metadata.get("current") and not metadata.get("source_current"):
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
        if not metadata.get("current"):
            retry["status"] = "AUTHENTICATED_LIVE_REFRESH_REQUIRED"
        elif credential_binding(ctx.environ) != metadata["credential_binding_sha256"]:
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
    remaining = _remaining_work(ctx, recorded, evidence)
    output = {
        "version": "money-first-candidate-preparation-v2",
        "universe_policy_version": UNIVERSE_POLICY_VERSION,
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
        # Raw normalization deliberately establishes identity, not ethical
        # clearance. Preserve the matching master row's recorded screening
        # alongside its qualification state; preparation never re-screens it.
        "ethical_state": recorded.get("ethical_state", "NOT_YET_SCREENED"),
        "ethical_screening": recorded.get("ethical_screening"),
        "remaining_blockers": remaining,
        "next_genuine_blocker": remaining[0] if remaining else None,
        "missing_evidence": [item["action"] for item in remaining],
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
    _write_first_stock_next(ctx, output)
    return output


def _remaining_work(
    ctx: QualificationContext, row: dict[str, Any], evidence: dict[str, Any] | None
) -> list[dict[str, str]]:
    """Explain actual recorded failures and missing downstream inputs, not approvals."""
    actions = {
        "PROVIDER_RIGHTS_AND_DATASET_QUALIFICATION_REQUIRED": (
            "Complete the applicable global inputs/provider-rights reviews against actual "
            "permission and dataset observations; API access alone is not permission."
        ),
        "VERIFIED_ISSUER_JURISDICTION_REQUIRED": (
            "Admit exact issuer jurisdiction/company identity, then applicable company and "
            "filing observations; public discovery notes alone are not provider evidence."
        ),
        "COMPLETE_APPROVED_MATERIAL_EXPOSURE_EVIDENCE_REQUIRED": (
            "Reclassify under the one-pass issuer policy using admissible issuer-wide activity "
            "evidence. No second ethical reviewer; absence of keywords is not clearance."
        ),
        "ISSUER_ETHICAL_SCREENING_REQUIRED": (
            "Supply one admissible issuer-wide business/activity dossier identifying the exact "
            "issuer and addressing every configured exclusion, then run the machine screening. "
            "Global provider rights are reused; no independent second ethical reviewer is required."
        ),
        "ADMISSIBLE_ISSUER_BUSINESS_EVIDENCE_REQUIRED": (
            "Missing ethical evidence item: one rights-admissible, exact-issuer business/activity "
            "disclosure covering every configured excluded activity. Attach the genuine source "
            "bytes in inputs/universe/ethical-evidence.json. Price/dividend/news observations "
            "alone are not an issuer-wide material-exposure disclosure; no second reviewer is required."
        ),
    }
    reasons = [str(code) for code in row.get("reasons", [])]
    # An old policy must be reclassified, not described as current qualification.
    if RETIRED_BLOCKERS.intersection(reasons) or row.get("qualification_state") == "UNRESOLVED_ISA_SCOPE":
        raise ValueError("FIRST_CANDIDATE_POLICY_RECLASSIFICATION_REQUIRED")
    blockers = {
        code: {"code": code, "stage": "eligibility", "action": actions.get(
            code, "Resolve the recorded instrument evidence requirement: " + code
        )}
        for code in reasons
    }
    screening = row.get("ethical_screening")
    if isinstance(screening, dict) and screening.get("result") == "UNKNOWN":
        for reason in screening.get("reasons", []):
            code = str(reason)
            blockers[code] = {
                "code": code, "stage": "eligibility",
                "action": actions.get(code, "Resolve the specific issuer screening evidence gap: " + code
                + ". Supply admissible source bytes in inputs/universe/ethical-evidence.json; "
                "do not sign an approval or infer PASS from the name, sector or absent keywords."),
            }
    providers = ["eodhd"]
    if (
        row.get("companies_house_state") == "MAPPED"
        or row.get("issuer_facts", {}).get("CountryISO") == "GB"
    ):
        providers.append("companies-house")
    for provider in providers:
        rights = _rights(ctx, provider)
        for field, code, path in (
            ("provider_rights_current", "PROVIDER_RIGHTS_REVIEW_REQUIRED", rights["provider_review_file"]),
            ("ethical_use_current", "ETHICAL_SOURCE_RIGHTS_REVIEW_REQUIRED", rights["ethical_review_file"]),
        ):
            if not rights[field]:
                key = code + ":" + provider
                blockers[key] = {
                    "code": key, "stage": "rights",
                    "action": (
                        "Complete the genuine current global provider rights review in " + path + "."
                        if field == "provider_rights_current" else
                        "Explicitly cover the applicable ethical_research_datasets in "
                        + rights["provider_review_file"] + " using actual licence evidence. "
                        "The same global approval covers all issuers; a second review in "
                        + path + " is not required (existing scope supplements remain compatible)."
                    ),
                }
    supplemental = ctx.read_json("inputs/universe/supplemental.json") or {}
    if not supplemental.get("instruments", {}).get(row.get("isin")):
        blockers["SUPPLEMENTAL_REVIEW_REQUIRED"] = {
            "code": "SUPPLEMENTAL_REVIEW_REQUIRED", "stage": "supplemental",
            "action": "Link an independently reviewed SupplementalReview for this ISIN in "
            "inputs/universe/supplemental.json: financial, spread/cost/slippage, complete "
            "corporate-action and archived publication/PIT evidence remain required.",
        }
    if not evidence or not evidence.get("historical_publication_verified"):
        blockers["HISTORICAL_PUBLICATION_AND_UNIVERSE_EVIDENCE_REQUIRED"] = {
            "code": "HISTORICAL_PUBLICATION_AND_UNIVERSE_EVIDENCE_REQUIRED", "stage": "lean_evidence",
            "action": "Prepare original-publication/PIT, historical membership and survivorship "
            "evidence; current prices or today's broker membership cannot establish history.",
        }
    priority = {"rights": 0, "eligibility": 1, "supplemental": 2, "lean_evidence": 3}
    return sorted(blockers.values(), key=lambda item: (
        priority[item["stage"]],
        not item["code"].startswith("PROVIDER_RIGHTS_REVIEW_REQUIRED:"),
        not item["code"].endswith(":eodhd"),
        item["code"],
    ))


def _write_first_stock_next(ctx: QualificationContext, output: dict[str, Any]) -> None:
    next_blocker = output["next_genuine_blocker"]
    lines = [
        "# First stock — genuine remaining work", "",
        f"{output['company']} ({output['trading212_id']}, {output['isin']}); policy {UNIVERSE_POLICY_VERSION}.",
        f"Recorded qualification state: {output['recorded_qualification_state']}.",
        f"Ethical screening: {output.get('ethical_state', 'NOT_YET_SCREENED')}. "
        "One issuer PASS is reused through snapshot, first pass, LEAN and CIO; no second ethical review.",
        "No account-type, ISA-scope or current-ISA-buyability review is required. Historical account reviews are ignored, not approved.",
        "",
        "Next genuine evidence blocker: " + (next_blocker["code"] if next_blocker else "No recorded eligibility reason; runner verification still required."),
        next_blocker["action"] if next_blocker else "Preparation cannot grant eligibility or execute downstream stages.",
        "", "## Remaining evidence", "",
        *[f"- `{item['code']}`: {item['action']}" for item in output["remaining_blockers"]],
        "", "## Execution order", "",
        "Keep live broker retrieval and provider evidence within their original freshness limits. "
        "An offline reclassification is not a new authenticated refresh; run the live finalizer before admission.",
        "After the remaining evidence genuinely qualifies the stock: freeze the complete qualified "
        "universe → independent TradingAgents and AI-Hedge-Fund sealed reports → FIRST_PASS_LOCKED "
        "→ mandatory LEAN → CIO / Red Team. Native source/security, reviewed hosted inference, "
        "worker egress, release and hosted acceptance remain separate requirements.",
        "Qlib stays disabled when MONEY_QLIB_ENABLED=false; no substitute numeric report is created.",
        "See outputs/first-candidate-lean-readiness.json for exact study/audit input errors. "
        "No downstream stage, signature, approval or production manifest is created by preparation.",
    ]
    ctx.write_bytes("outputs/FIRST_STOCK_NEXT.md", ("\n".join(lines) + "\n").encode())


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
