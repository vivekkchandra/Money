"""Bulk, read-only GBX stock discovery and evidence admission, never trading authority.

Metadata membership, technical credential provenance, evidence rights and ethical
coverage are different facts. None is inferred from the others. The master file
is an operator artifact, not a replacement for a pinned production manifest.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import io
import json
import os
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from money.adapters.eligibility import (
    _UNKNOWN_ACTIVITIES,
    ELIGIBILITY_MAXIMUM_AGE,
    eligibility_failures,
)
from money.data.identifiers import InstrumentIdentifiers
from money.data.live_eligibility import EligibilityReview
from money.data.qualification import ProviderQualification
from money.data.uk.live import Trading212MetadataProvider
from money.qualification.core import QualificationContext, json_bytes
from money.qualification.universe_policy import UNIVERSE_POLICY_VERSION, ensure_universe_policy
from money.research.inference_config import load_inference_selections
from money.research.qlib_mode import qlib_enabled
from money.schemas.contracts import (
    EXCLUDED_ACTIVITIES,
    Contract,
    InstrumentMetadata,
    ResearchMandate,
    utc_now,
)

MASTER = "outputs/trading212-gbx-stock-universe.json"
LEGACY_MASTER = "outputs/uk-isa-stock-universe.json"
MASTER_CSV = "outputs/trading212-gbx-stock-universe.csv"
MODE = "state/bulk-universe-mode.json"
MAX_MASTER_BYTES = 64_000_000
MAX_BROKER_RESPONSE_BYTES = 20_000_000
BROKER_RESPONSE_CHUNK_BYTES = 1_500_000
UNIVERSE_VERSION = UNIVERSE_POLICY_VERSION
INITIAL_FILTER = {"instrument_type": "STOCK", "quote_currency": "GBX", "venue_required": False}


def _stamp() -> dict[str, Any]:
    return dict(
        status="UNRESOLVED", prepared_by=None, reviewed_by=None, reviewed_at=None, valid_until=None
    )


class EthicalEntry(Contract):
    """Structured evidence coverage, never an LLM/name/SIC absence heuristic."""

    isin: str = Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    company_name: str = Field(min_length=1)
    business_activities: tuple[str, ...] = Field(min_length=1)
    assessed_exclusions: tuple[str, ...] = Field(min_length=1)
    complete_material_exposure_review: Literal[True]
    evidence_files: tuple[str, ...] = Field(min_length=1, max_length=20)
    source_rights_review_files: tuple[str, ...] = Field(min_length=1, max_length=10)


def credential_binding(environ: Mapping[str, str]) -> str | None:
    """Bind cached retrievals to credentials without persisting either key.

    The legacy HMAC domain is retained for raw-cache compatibility only; this
    technical binding makes no assertion about account type or buy availability.
    """
    key, secret = environ.get("TRADING212_API_KEY"), environ.get("TRADING212_API_SECRET")
    if not key or not secret:
        return None
    return hmac.new(
        secret.encode(), b"money-isa-account-context-v1\0" + key.encode(), hashlib.sha256
    ).hexdigest()


def _attach(ctx: QualificationContext, paths: Sequence[str]) -> list[tuple[str, str]]:
    refs = []
    for path in paths:
        if not isinstance(path, str) or not path.startswith("inputs/"):
            raise ValueError("BULK_REVIEW_EVIDENCE_REQUIRED")
        raw = ctx.read_bytes(path)
        if not raw or raw.strip() in {b"{}", b"[]", b"null"}:
            raise ValueError("BULK_REVIEW_EVIDENCE_REQUIRED")
        refs.append(ctx.artifact(raw))
    return refs


def _review(ctx: QualificationContext, value: Any) -> Any:
    from money.qualification.providers import IndependentReview

    review = IndependentReview.model_validate(value)
    review.require_current(ctx.now)
    return review


def prepare_bulk_inputs(ctx: QualificationContext, binding: str | None) -> None:
    """Only bulk inputs and exception queues; never generate per-stock templates."""
    ctx.template(MODE, {"universe_policy_version": UNIVERSE_POLICY_VERSION})
    ctx.template("inputs/universe/venues.json", {"review": _stamp(), "venues": []})
    ctx.template("inputs/universe/ethics.json", {"review": _stamp(), "instruments": []})
    ctx.template("inputs/universe/supplemental.json", {"instruments": {}})
    ctx.write_json("inputs/universe/ethical-entry.schema.json", EthicalEntry.model_json_schema())
    ctx.write_json(
        "outputs/universe-policy-retirements.json",
        {
            "universe_policy_version": UNIVERSE_POLICY_VERSION,
            "ignored_legacy_inputs": [
                "inputs/universe/account-scope.json",
                "inputs/universe/account-scope.schema.json",
            ],
            "disposition": "PRESERVED_UNCHANGED_NOT_QUALIFICATION_AUTHORITY",
            "review_approval_created": False,
        },
    )
    ctx.write_bytes(
        "inputs/universe/README.md",
        b"""# Bulk review inputs

These inputs are unsigned. Discovery never approves them. Existing historical
per-stock preparation is not selected by this workflow.

Initial membership requires only STOCK + GBX in the current live Trading 212
accessible-instruments response. GBP is not admitted. Venue, MIC, country and
ISIN prefix are not membership requirements; genuine identity conflicts still
remain unresolved. Membership alone approves none of the reviews below.

- Legacy `account-scope.json` and its schema are deprecated audit artifacts.
  They are ignored by the current policy, never required, approved or rewritten.
  Technical credential binding protects raw-cache integrity only; membership
  makes no account-type or purchase-availability claim.
- `venues.json`: optional informational enrichment only. Existing reviewed
  exchange facts may be retained, but missing, foreign or unresolved venues
  never block admission or provider lookup. No venue review is required.
- `ethics.json`: one independent review can cover many structured instruments.
  Each entry: isin, company_name, business_activities, assessed_exclusions,
  complete_material_exposure_review=true, evidence_files and
  source_rights_review_files. Assess every existing policy exclusion using
  approved company/filing/report evidence; name/SIC/description absence is not
  clearance. Rights review files contain review, evidence_files and
  permitted_use='ethical-research'. Unknown exposure stays unresolved.
- `supplemental.json`: instruments keyed by ISIN may reference existing
  independently reviewed SupplementalReview files. Full research still needs
  approved spread/cost/action/PIT/financial evidence. No individual template is
  generated until an operator needs to resolve a genuine exception.

All review stamps require status=REVIEWED, distinct actual prepared_by and
reviewed_by, actual timezone-aware reviewed_at and valid_until. Never backdate.
No approval or secret should be manufactured to make a field validate.
""",
        replace=True,
    )


def _venues(ctx: QualificationContext) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        value = ctx.read_json("inputs/universe/venues.json")
        if not isinstance(value, dict):
            return result
        review = _review(ctx, value["review"])
        for entry in value["venues"]:
            try:
                refs = _attach(ctx, entry["evidence_files"])
                if not refs:
                    continue
                result.append(
                    {
                        **entry,
                        "reviewed_at": review.reviewed_at.isoformat(),
                        "valid_until": review.valid_until.isoformat(),
                        "source_evidence_hash": ctx.artifact(
                            {"review": value["review"], "entry": entry, "evidence": refs}
                        )[0],
                    }
                )
            except (ValueError, OSError, KeyError, TypeError):
                continue
    except (ValueError, OSError, KeyError, TypeError):
        pass
    return result


def _ethics(ctx: QualificationContext) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    try:
        value = ctx.read_json("inputs/universe/ethics.json")
        if not isinstance(value, dict):
            return result
        stamp = _review(ctx, value["review"])
        if ctx.now >= stamp.reviewed_at + ELIGIBILITY_MAXIMUM_AGE:
            return {}
        entries = value["instruments"]
        counts = Counter(item.get("isin") for item in entries if isinstance(item, dict))
        for raw in entries:
            try:
                entry = EthicalEntry.model_validate(raw)
                if counts[entry.isin] != 1 or not set(EXCLUDED_ACTIVITIES) <= set(
                    entry.assessed_exclusions
                ):
                    continue
                refs = _attach(ctx, entry.evidence_files)
                expires = stamp.valid_until
                for path in entry.source_rights_review_files:
                    rights = ctx.read_json(path)
                    if not isinstance(rights, dict):
                        raise ValueError("ETHICAL_SOURCE_RIGHTS_REQUIRED")
                    rights_review = _review(ctx, rights["review"])
                    expires = min(expires, rights_review.valid_until)
                    if rights["permitted_use"] != "ethical-research":
                        raise ValueError("ETHICAL_SOURCE_RIGHTS_REQUIRED")
                    refs.extend(_attach(ctx, rights["evidence_files"]))
                    refs.extend(_attach(ctx, [path]))
                proof = ctx.artifact(
                    {
                        "kind": "reviewed-material-exposure-v1",
                        "review": value["review"],
                        "entry": raw,
                        "source_evidence": refs,
                    }
                )
                result[entry.isin] = {
                    "entry": entry,
                    "stamp": stamp,
                    "proof": proof,
                    "valid_until": expires,
                }
            except (ValueError, OSError, KeyError, TypeError):
                continue
    except (ValueError, OSError, KeyError, TypeError):
        pass
    return result


def _store_response(ctx: QualificationContext, raw: bytes) -> tuple[str, str]:
    """Exact response hashes survive chunking; artifact safety limits stay intact."""
    if not raw or len(raw) > MAX_BROKER_RESPONSE_BYTES:
        raise ValueError("BROKER_RESPONSE_SIZE_INVALID")
    ctx.check_secrets(raw)
    parts = [
        ctx.artifact(raw[start : start + BROKER_RESPONSE_CHUNK_BYTES])
        for start in range(0, len(raw), BROKER_RESPONSE_CHUNK_BYTES)
    ]
    return ctx.artifact(
        {
            "kind": "exact-response-chunks-v1",
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "chunks": parts,
        }
    )


def _large_write(ctx: QualificationContext, path: str, raw: bytes) -> None:
    """Atomic bounded master projection, not a larger qualification-proof limit."""
    if not raw or len(raw) > MAX_MASTER_BYTES:
        raise ValueError("UNIVERSE_MASTER_SIZE_INVALID")
    ctx.check_secrets(raw)
    target = ctx._path(path)
    temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _qualify_rights(ctx: QualificationContext, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Existing real probe/admission machinery, independently reviewed rights."""
    from money.qualification.providers import _rights_template
    from money.qualification.universe_providers import qualify_bulk_provider_reports

    qualified: dict[str, Any] = {}
    for provider in ("eodhd", "companies-house"):
        path = f"inputs/provider-rights/{provider}.json"
        ctx.template(path, _rights_template(provider))
        rights = ctx.read_json(path)
        if not isinstance(rights, dict) or rights.get("status") != "REVIEWED":
            continue
        qualification = qualify_bulk_provider_reports(ctx, provider, rows, rights)
        if qualification is not None:
            qualified[provider] = qualification.model_dump(mode="json")
            ctx.artifact(qualified[provider])
    return qualified


def _activity(value: str) -> str:
    return re.sub(r"[\s-]+", "_", value.strip().casefold())


def _time(value: Any) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("AWARE_EVIDENCE_TIME_REQUIRED")
    return stamp


def classify_row(
    ctx: QualificationContext,
    row: dict[str, Any],
    ethics: dict[str, dict[str, Any]],
    rights: dict[str, Any],
    *,
    admission_allowed: bool = True,
) -> EligibilityReview | None:
    """Admission consumes actual evidence; missing conditions remain explicit."""
    initial = row["qualification_state"]
    row.update(
        ethical_state="ETHICAL_REVIEW_REQUIRED",
        evidence_freshness="CURRENT_METADATA",
        eligibility_review=None,
    )
    if initial.startswith("EXCLUDED_") or initial == "UNRESOLVED_IDENTITY":
        return None
    reasons: list[str] = list(row.get("reasons", []))
    try:
        observed, until = _time(row["observed_at"]), _time(row["valid_until"])
        if not observed <= ctx.now < min(until, observed + ELIGIBILITY_MAXIMUM_AGE):
            raise ValueError("BROKER_MEMBERSHIP_EXPIRED")
    except (ValueError, TypeError, KeyError):
        row.update(
            qualification_state="EXPIRED",
            evidence_freshness="EXPIRED",
            reasons=[*reasons, "BROKER_MEMBERSHIP_EXPIRED"],
        )
        return None
    state = "QUALIFIED"
    identifiers = None
    try:
        identifiers = InstrumentIdentifiers.model_validate(row.get("identifiers"))
        identifiers.require_current(ctx.now)
    except (ValueError, TypeError):
        state = "UNRESOLVED_PROVIDER_MAPPING"
        reasons.append("EXACT_CURRENT_PROVIDER_IDENTITY_REQUIRED")
    assessed = ethics.get(row.get("isin", ""))
    if assessed is None:
        if state == "QUALIFIED":
            state = "UNRESOLVED_ETHICAL"
        reasons.append("COMPLETE_APPROVED_MATERIAL_EXPOSURE_EVIDENCE_REQUIRED")
    else:
        entry = assessed["entry"]
        company = identifiers.company_name if identifiers else row.get("name", "")
        if (
            re.sub(r"\W", "", entry.company_name).casefold()
            != re.sub(r"\W", "", company).casefold()
        ):
            assessed = None
            state = "UNRESOLVED_ETHICAL"
            reasons.append("ETHICAL_COMPANY_IDENTITY_MISMATCH")
        elif set(map(_activity, entry.business_activities)) & set(EXCLUDED_ACTIVITIES):
            row.update(
                qualification_state="EXCLUDED_ETHICAL",
                ethical_state="ETHICALLY_EXCLUDED",
                reasons=[*reasons, "PROHIBITED_MATERIAL_ACTIVITY"],
            )
            return None
        elif set(map(_activity, entry.business_activities)) & _UNKNOWN_ACTIVITIES or any(
            not activity.strip() for activity in entry.business_activities
        ):
            assessed = None
            if state == "QUALIFIED":
                state = "UNRESOLVED_ETHICAL"
            reasons.append("UNKNOWN_MATERIAL_EXPOSURE_NOT_CLEARED")
        else:
            row["ethical_state"] = "ETHICALLY_CLEARED"
    required = {"eodhd"}
    jurisdiction = row.get("issuer_facts", {}).get("CountryISO")
    if jurisdiction == "GB":
        required.add("companies-house")
        if (
            row.get("companies_house_state") != "MAPPED"
            or identifiers is None
            or not identifiers.companies_house_number
        ):
            if state == "QUALIFIED":
                state = "UNRESOLVED_PROVIDER_MAPPING"
            reasons.append("VERIFIED_ISSUER_JURISDICTION_AND_COMPANY_IDENTITY_REQUIRED")
    elif not (
        row.get("companies_house_state") == "NOT_APPLICABLE"
        and isinstance(jurisdiction, str)
        and re.fullmatch(r"[A-Z]{2}", jurisdiction)
    ):
        # Unknown incorporation is a real blocker, but is not evidence that a
        # Companies House identity or UK filing exists. Do not manufacture
        # thousands of UK company/filing tasks before applicability is known.
        if state == "QUALIFIED":
            state = "UNRESOLVED_PROVIDER_MAPPING"
        reasons.append("VERIFIED_ISSUER_JURISDICTION_REQUIRED")
    if not required <= rights.keys():
        if state == "QUALIFIED":
            state = "UNRESOLVED_PROVIDER_MAPPING"
        reasons.append("PROVIDER_RIGHTS_AND_DATASET_QUALIFICATION_REQUIRED")
    provider_expiries = []
    for provider in required & rights.keys():
        try:
            qualification = ProviderQualification.model_validate(rights[provider])
            for dataset in (
                ("ohlcv", "corporate_action", "news") if provider == "eodhd" else ("filing",)
            ):
                qualification.require(dataset, ctx.now)
            if (
                row["quote_currency"] not in qualification.currencies
                or "GB" not in qualification.geography
                or "STOCK" not in qualification.instrument_types
            ):
                raise ValueError("PROVIDER_SCOPE_MISMATCH")
            provider_expiries.append(qualification.valid_until)
        except (ValueError, KeyError):
            if state == "QUALIFIED":
                state = "UNRESOLVED_PROVIDER_MAPPING"
            reasons.append("PROVIDER_CURRENCY_DATASET_OR_FRESHNESS_COVERAGE_REQUIRED")
    # EMPTY is recorded honestly, never promoted to a qualifying observation.
    datasets = row.get("provider_datasets", {})
    needed_datasets = ["eodhd:ohlcv", "eodhd:corporate_action", "eodhd:news"]
    if "companies-house" in required:
        needed_datasets.extend(["companies-house:company", "companies-house:filing"])
    for dataset in needed_datasets:
        fact = datasets.get(dataset, {}) if isinstance(datasets, dict) else {}
        if fact.get("status") != "RETRIEVED" or fact.get("record_count", 0) < 1:
            if state == "QUALIFIED":
                state = "UNRESOLVED_PROVIDER_MAPPING"
            reasons.append("CURRENT_" + dataset.replace(":", "_").upper() + "_OBSERVATION_REQUIRED")
    if row.get("identifiers") is not None:
        missing_timestamps = False
        try:
            first = _time(row["provider_evidence_observed_at"])
            last = _time(row["provider_evidence_valid_until"])
        except (ValueError, TypeError, KeyError):
            missing_timestamps = True
        else:
            if not first <= ctx.now < min(last, first + ELIGIBILITY_MAXIMUM_AGE):
                state = "STALE_EVIDENCE"
                row["evidence_freshness"] = "STALE_PROVIDER_EVIDENCE"
                reasons.append("PROVIDER_EVIDENCE_EXPIRED_OR_FUTURE")
        try:
            latest_bar = _time(datasets["eodhd:ohlcv"]["latest_observation"])
        except (ValueError, TypeError, KeyError):
            missing_timestamps = True
        else:
            # Preserve the existing market-quality 96-hour freshness requirement.
            from money.data.quality.market import MarketQualityPolicy

            if (
                not latest_bar
                <= ctx.now
                <= latest_bar + timedelta(hours=MarketQualityPolicy().maximum_latest_age_hours)
            ):
                state = "STALE_EVIDENCE"
                row["evidence_freshness"] = "STALE_PROVIDER_EVIDENCE"
                reasons.append("OHLCV_EVIDENCE_EXPIRED_OR_FUTURE")
        if missing_timestamps:
            if state == "QUALIFIED":
                state = "UNRESOLVED_PROVIDER_MAPPING"
            if state != "STALE_EVIDENCE":
                row["evidence_freshness"] = "PROVIDER_OBSERVATIONS_MISSING"
            reasons.append("CURRENT_TIMESTAMPED_PROVIDER_OBSERVATIONS_REQUIRED")
    if state != "QUALIFIED" or identifiers is None or assessed is None:
        row.update(qualification_state=state, reasons=sorted(set(reasons)))
        return None
    if not admission_allowed:
        row.update(
            qualification_state="UNRESOLVED_LIVE_RETRIEVAL",
            reasons=["LIVE_AUTHENTICATED_REFRESH_REQUIRED"],
        )
        return None
    try:
        verified_at = min(observed, assessed["stamp"].reviewed_at)
        deadline = min(
            until,
            identifiers.valid_until,
            assessed["valid_until"],
            *provider_expiries,
            _time(row["provider_evidence_valid_until"]),
            verified_at + ELIGIBILITY_MAXIMUM_AGE,
        )
        if ctx.now >= deadline:
            raise ValueError("ADMISSION_EVIDENCE_EXPIRED")
        # Downstream services consume EligibilityReview, not master row fields.
        # Carry every shorter review/rights expiry into that actual contract.
        identifiers = InstrumentIdentifiers.model_validate(
            identifiers.model_dump() | {"valid_until": deadline}
        )
        row["identifiers"] = identifiers.model_dump(mode="json")
        metadata = InstrumentMetadata(
            ticker=identifiers.ticker,
            company=identifiers.company_name,
            instrument_type="STOCK",
            quote_currency=identifiers.quote_currency,
            currently_available=True,
            business_activities=assessed["entry"].business_activities,
            activities_verified=True,
            verified_at=verified_at,
            source="hash-bound live broker membership and material-exposure evidence",
            provider="trading212",
            source_id=identifiers.trading212_id,
        )
        failures = eligibility_failures(metadata, ResearchMandate(), ctx.now)
        if failures:
            row.update(
                qualification_state="STALE_EVIDENCE"
                if "ELIGIBILITY_STALE" in failures
                else "UNRESOLVED_ETHICAL",
                reasons=list(failures),
            )
            return None
        proof = ctx.artifact(
            {
                "kind": "bulk-eligibility-join-v2",
                "universe_policy_version": UNIVERSE_POLICY_VERSION,
                "metadata": metadata.model_dump(mode="json"),
                "identifiers": identifiers.model_dump(mode="json"),
                "broker_row_hash": row["instrument_row_sha256"],
                "exchange_row_hash": row["exchange_row_sha256"],
                "broker_responses": row["broker_response_refs"],
            }
        )
        admitted = EligibilityReview(
            metadata=metadata,
            identifiers=identifiers,
            eligibility_proof_hash=proof[0],
            ethical_proof_hash=assessed["proof"][0],
        )
        row.update(
            qualification_state="QUALIFIED",
            reasons=[],
            eligibility_review=admitted.model_dump(mode="json"),
            valid_until=deadline.isoformat(),
        )
        return admitted
    except (ValueError, KeyError, TypeError):
        row.update(
            qualification_state="UNRESOLVED_IDENTITY",
            reasons=[*reasons, "ELIGIBILITY_CONTRACT_REJECTED"],
        )
        return None


def _summary(rows: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    raw_states = Counter(row["qualification_state"] for row in rows)
    relevant = [r for r in rows if r.get("universe_member") is True]
    states = Counter(row["qualification_state"] for row in relevant)

    def provider_failures(row: dict[str, Any]) -> set[str]:
        # Dataset probes preserve partial failures in their request diagnostics
        # instead of raising them through the identity join. Count those too,
        # once per stock, so bounded unfinished work is never reported as zero.
        failures = set(row.get("provider_reasons", []))
        failures.update(
            item["error_code"] for item in row.get("provider_request_diagnostics", [])
            if isinstance(item, dict) and isinstance(item.get("error_code"), str)
        )
        return failures

    def current_dataset(row: dict[str, Any], name: str) -> bool:
        from money.data.quality.market import MarketQualityPolicy

        fact = row.get("provider_datasets", {}).get("eodhd:" + name, {})
        if fact.get("status") != "RETRIEVED" or not fact.get("record_count"):
            return False
        try:
            if (
                not _time(row["provider_evidence_observed_at"])
                <= now
                < _time(row["provider_evidence_valid_until"])
            ):
                return False
            if name == "ohlcv":
                latest = _time(fact["latest_observation"])
                return (
                    latest
                    <= now
                    <= latest + timedelta(hours=MarketQualityPolicy().maximum_latest_age_hours)
                )
            return True
        except (ValueError, TypeError, KeyError):
            return False

    return {
        "raw_instruments": len(rows),
        "gbx_stocks": len(relevant),
        "identity_valid": sum(r.get("identity_valid") is True for r in relevant),
        "identity_unresolved": sum(r.get("identity_valid") is not True for r in relevant),
        "provider_stage_input_count": sum(
            r.get("provider_enrichment_input") is True for r in relevant
        ),
        "venue_resolved": sum(r.get("venue_status") == "RESOLVED" for r in relevant),
        "raw_states": dict(sorted(raw_states.items())),
        "states": dict(sorted(states.items())),
        "qualified": states["QUALIFIED"],
        "excluded": sum(
            count for state, count in raw_states.items() if state.startswith("EXCLUDED_")
        ),
        "unresolved": sum(
            count for state, count in states.items() if state.startswith("UNRESOLVED_")
        ),
        "eodhd_mapping_attempted": sum(r.get("eodhd_mapping_attempted") is True for r in relevant),
        "eodhd_attempted": sum(r.get("eodhd_mapping_attempted") is True for r in relevant),
        "eodhd_lookups_network": sum(r.get("eodhd_lookup_origin") == "NETWORK" for r in relevant),
        "eodhd_lookups_cached": sum(r.get("eodhd_lookup_origin") == "CACHE" for r in relevant),
        "eodhd_mapped": sum(bool(r.get("eodhd_symbol")) for r in relevant),
        "companies_house_mapped": sum(bool(r.get("companies_house_number")) for r in relevant),
        "companies_house_applicable": sum(
            r.get("issuer_facts", {}).get("CountryISO") == "GB" for r in relevant
        ),
        "companies_house_applicability_unknown": sum(
            r.get("identity_valid") is True
            and r.get("issuer_facts", {}).get("CountryISO") != "GB"
            and r.get("companies_house_state") != "NOT_APPLICABLE"
            for r in relevant
        ),
        "provider_deferred": sum(
            "PROVIDER_REQUEST_BUDGET_EXHAUSTED" in provider_failures(r)
            for r in relevant
        ),
        "provider_failure_counts": dict(sorted(Counter(
            reason for row in relevant for reason in provider_failures(row)
        ).items())),
        "ethical_review_required": sum(
            r.get("ethical_state") == "ETHICAL_REVIEW_REQUIRED" for r in relevant
        ),
        "ethical_excluded": states["EXCLUDED_ETHICAL"],
        "datasets": {
            name: sum(current_dataset(r, name) for r in relevant)
            for name in ("ohlcv", "corporate_action", "news")
        },
    }


def _csv(rows: list[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    fields = (
        "trading212_id",
        "short_ticker",
        "name",
        "isin",
        "exchange_id",
        "exchange_name",
        "mic",
        "quote_currency",
        "instrument_type",
        "universe_member",
        "identity_valid",
        "venue_status",
        "venue_reasons",
        "added_on",
        "max_open_quantity",
        "working_schedule_id",
        "observed_at",
        "valid_until",
        "companies_house_number",
        "eodhd_symbol",
        "qualification_state",
        "ethical_state",
        "evidence_freshness",
        "reasons",
    )
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        safe = {}
        for field in fields:
            value = row.get(field)
            text = (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value or "")
            )
            safe[field] = "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text
        writer.writerow(safe)
    return output.getvalue().encode()


def _review_work(rows: list[dict[str, Any]], provenance: dict[str, Any]) -> dict[str, Any]:
    """Bulk factual dossiers and distinct global reviews, never signed inputs."""
    return {
        "version": "money-bulk-review-work-v1",
        "universe_policy_version": UNIVERSE_POLICY_VERSION,
        "scope": provenance["scope"],
        "source_provenance": provenance,
        "review_status": "UNREVIEWED_MACHINE_FACTS",
        "global_reviews": {
            "provider_rights": {
                "review_files": [
                    "inputs/provider-rights/eodhd.json",
                    "inputs/provider-rights/companies-house.json",
                ],
                "action": "Independently substantiate permitted use, retention and redistribution. API success is not permission; no per-stock rights signatures are requested.",
            },
            "ethical_evidence": {
                "review_file": "inputs/universe/ethics.json",
                "schema_file": "inputs/universe/ethical-entry.schema.json",
                "required_exclusions": list(EXCLUDED_ACTIVITIES),
                "action": "One independent review may cover multiple instruments. Assess all material activities using rights-approved evidence; descriptions or absent keywords are not clearance.",
            },
        },
        "instruments": [
            {
                **{
                    field: row.get(field)
                    for field in (
                        "trading212_id", "isin", "name", "quote_currency",
                        "identity_valid", "eodhd_symbol", "companies_house_state",
                        "companies_house_number", "provider_evidence", "provider_datasets",
                        "provider_stage_status", "provider_request_diagnostics",
                        "provider_reasons", "ethical_state",
                        "provider_evidence_observed_at", "provider_evidence_valid_until",
                    )
                },
                "source_content_use": "REFERENCES_ONLY",
                "ethical_review_queue": "outputs/ethics-work-queue.json",
                "human_approval_supplied": False,
            }
            for row in rows if row.get("universe_member") is True
        ],
    }


def _optional_exchanges(
    ctx: QualificationContext, provider: Any
) -> tuple[bytes | None, tuple[Any, ...]]:
    """Exchange enrichment is never required to retrieve live membership."""
    try:
        raw, exchanges = provider.metadata_response("exchanges")
        ctx.check_secrets(raw)
        return raw, exchanges
    except Exception:
        # No empty response/hash is invented for unavailable or unsafe bytes.
        return None, ()


def _restore_response(ctx: QualificationContext, reference: Any) -> tuple[bytes, tuple[Any, ...]]:
    descriptor = json.loads(ctx.verify_artifact(*reference))
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("kind") != "exact-response-chunks-v1"
        or type(descriptor.get("bytes")) is not int
        or not 0 < descriptor["bytes"] <= MAX_BROKER_RESPONSE_BYTES
        or not isinstance(descriptor.get("chunks"), list)
        or not 1 <= len(descriptor["chunks"]) <= 14
    ):
        raise ValueError("BROKER_RESPONSE_CACHE_INVALID")
    chunks, size = [], 0
    for part in descriptor["chunks"]:
        chunk = ctx.verify_artifact(*part)
        size += len(chunk)
        if len(chunk) > BROKER_RESPONSE_CHUNK_BYTES or size > descriptor["bytes"]:
            raise ValueError("BROKER_RESPONSE_CACHE_INVALID")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if len(raw) != descriptor["bytes"] or hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
        raise ValueError("BROKER_RESPONSE_CACHE_INVALID")
    items = json.loads(raw)
    if not isinstance(items, list) or len(items) > 100000:
        raise ValueError("BROKER_RESPONSE_CACHE_INVALID")
    return raw, tuple(items)


def _broker_metadata(
    ctx: QualificationContext, binding: str | None
) -> tuple[bytes, tuple[Any, ...], bytes | None, tuple[Any, ...], datetime]:
    """Cache exact bytes for <=10 minutes; instrument failures invalidate membership.

    Trading 212 documents one instruments request per 50 seconds and one
    exchanges request per 30 seconds. Successful unchanged metadata is reused
    under the existing ten-minute refresh policy, bound to these exact credentials.
    """
    if binding is None:
        # A credential-free diagnostic run cannot perform a live refresh, but
        # must not erase the last genuine raw response's retrieval provenance.
        raise ValueError("TRADING212_CREDENTIALS_REQUIRED")
    cache_path = "state/bulk-broker-metadata.json"
    state = ctx.read_json(cache_path)
    if isinstance(state, dict) and state.get("credential_binding_sha256") == binding:
        if state.get("status") == "CURRENT":
            observed = _time(state["observed_at"])
            if observed <= ctx.now < observed + timedelta(minutes=10):
                raw_instruments, instruments = _restore_response(
                    ctx, state["responses"]["instruments"]
                )
                raw_exchanges, exchanges = None, ()
                if state["responses"].get("exchanges") is not None:
                    try:
                        raw_exchanges, exchanges = _restore_response(
                            ctx, state["responses"]["exchanges"]
                        )
                    except (ValueError, OSError, KeyError, TypeError):
                        # Invalid optional enrichment is discarded, never used
                        # as evidence or allowed to veto a valid instrument list.
                        pass
                return raw_instruments, instruments, raw_exchanges, exchanges, observed
        attempted = _time(state["attempted_at"])
        if ctx.now < attempted + timedelta(seconds=50):
            raise ValueError("BROKER_METADATA_RETRY_BACKOFF")
    attempted = utc_now()
    # Invalidates prior membership before I/O. A process interruption also
    # leaves a bounded retry delay rather than silently reviving stale data.
    state = {
        "status": "ATTEMPTED",
        "attempted_at": attempted.isoformat(),
        "credential_binding_sha256": binding,
        "last_success": state if isinstance(state, dict) and state.get("status") == "CURRENT"
        else state.get("last_success") if isinstance(state, dict) else None,
    }
    ctx.write_json(cache_path, state)
    provider = Trading212MetadataProvider(
        ctx.environ.get("TRADING212_API_KEY", ""), ctx.environ.get("TRADING212_API_SECRET", "")
    )
    raw_instruments, instruments = provider.metadata_response("instruments")
    ctx.check_secrets(raw_instruments)
    observed = utc_now()
    raw_exchanges, exchanges = _optional_exchanges(ctx, provider)
    state.update(
        status="CURRENT",
        observed_at=observed.isoformat(),
        responses={
            "instruments": _store_response(ctx, raw_instruments),
            **(
                {"exchanges": _store_response(ctx, raw_exchanges)}
                if raw_exchanges is not None
                else {}
            ),
        },
        exchange_enrichment_status="RETRIEVED"
        if raw_exchanges is not None
        else "UNAVAILABLE_OR_INVALID",
    )
    state.pop("last_success", None)
    ctx.write_json(cache_path, state)
    return raw_instruments, instruments, raw_exchanges, exchanges, observed


def _saved_broker_metadata(
    ctx: QualificationContext,
) -> tuple[bytes, tuple[Any, ...], bytes | None, tuple[Any, ...], datetime, str | None]:
    """Verify saved raw bytes without turning replay into a live retrieval.

    Credentials need not be present for forensic replay. Their recorded binding
    is preserved as a source fact, not as authentication of this process.
    A future timestamp, corrupted response or missing source is never accepted.
    """
    state = ctx.read_json("state/bulk-broker-metadata.json")
    if isinstance(state, dict) and state.get("status") != "CURRENT":
        state = state.get("last_success")
    source: dict[str, Any] | None
    if isinstance(state, dict) and state.get("status") == "CURRENT":
        source = state
        responses = state["responses"]
        observed = _time(state["observed_at"])
    else:
        # A migration archive may be much older than the last successful run.
        # Prefer its current provenance view; archive metadata is a fallback,
        # never a reason to silently replay older broker bytes.
        source = ctx.read_json("outputs/universe-provenance.json")
        if not isinstance(source, dict):
            preserved = ctx.read_json("state/universe-rebuild-source.json")
            source = preserved.get("source") if isinstance(preserved, dict) else None
        if not isinstance(source, dict) or source.get("retrieval_environment") != "live":
            raise ValueError("SAVED_LIVE_BROKER_RESPONSE_REQUIRED")
        responses = source["response_artifacts"]
        observed = _time(source["retrieved_at"])
    if observed > ctx.now:
        raise ValueError("SAVED_BROKER_OBSERVATION_IN_FUTURE")
    raw, instruments = _restore_response(ctx, responses["instruments"])
    if source.get("instrument_response_hash") not in {None, hashlib.sha256(raw).hexdigest()}:
        raise ValueError("SAVED_BROKER_RESPONSE_HASH_MISMATCH")
    exchange_raw, exchanges = None, ()
    if responses.get("exchanges") is not None:
        try:
            exchange_raw, exchanges = _restore_response(ctx, responses["exchanges"])
            if source.get("exchange_response_hash") not in {
                None,
                hashlib.sha256(exchange_raw).hexdigest(),
            }:
                exchange_raw, exchanges = None, ()
        except (ValueError, OSError, KeyError, TypeError):
            pass
    recorded_binding = source.get("credential_binding_sha256")
    if recorded_binding is not None and (
        not isinstance(recorded_binding, str) or not re.fullmatch(r"[a-f0-9]{64}", recorded_binding)
    ):
        raise ValueError("SAVED_CREDENTIAL_BINDING_INVALID")
    return raw, instruments, exchange_raw, exchanges, observed, recorded_binding


def _provider_projection(master: dict[str, Any]) -> dict[str, Any]:
    """Separate enrichment inputs from fully approved research instruments."""
    summary = master.get("summary") or {}
    candidates = [
        {
            key: row.get(key)
            for key in (
                "trading212_id",
                "short_ticker",
                "name",
                "isin",
                "quote_currency",
                "instrument_type",
                "instrument_row_sha256",
                "observed_at",
                "valid_until",
            )
        }
        for row in master.get("stocks", [])
        if row.get("provider_enrichment_input") is True
    ]
    reviews = master.get("eligibility_reviews", [])
    return {
        "universe_policy_version": UNIVERSE_POLICY_VERSION,
        "stage": "UNIVERSE_ENRICHMENT_ONLY",
        "scope": master.get("scope", "LIVE_RETRIEVAL"),
        "status": master.get("status", "REFRESH_IN_PROGRESS"),
        "complete": False,
        "production_qualified": False,
        "summary": summary,
        "provider_stage_inputs": candidates,
        "provider_stage_input_count": len(candidates),
        "candidate_counts": {"GBX": summary.get("gbx_stocks", 0)},
        "eligible_counts": {"GBX": len(reviews)},
        "unresolved_candidate_count": summary.get("unresolved", 0),
        "instruments": [],
        "eligibility_reviews": reviews,
        "qualified_universe": reviews,
        "provider_qualifications": master.get("provider_qualifications", []),
        "universe_artifact": master.get("universe_artifact"),
        "universe_provenance": master.get("universe_provenance"),
        "universe_account_binding_sha256": (master.get("provenance") or {}).get(
            "credential_binding_sha256"
        ),
        "candidate_exclusions": [],
        "artifact_refs": master.get("artifact_refs", []),
    }


def _write_provider_projection(ctx: QualificationContext, result: dict[str, Any]) -> None:
    for path in ("state/provider-stage.json", "outputs/providers-result.json"):
        _large_write(ctx, path, json_bytes(result))


def _update_universe_diagnostics(
    ctx: QualificationContext, master: dict[str, Any], *, capture_documents: bool = False
) -> None:
    from money.qualification.diagnostics import write_qualification_diagnostics
    from money.qualification.universe_progress import write_enrichment_progress
    from money.qualification.universe_reviews import prepare_universe_reviews
    from money.qualification.universe_status import reconcile_universe_status

    write_enrichment_progress(ctx, master)
    prepare_universe_reviews(
        ctx, master.get("stocks", []), master.get("provenance", {}),
        fetch_documents=capture_documents,
    )
    reconcile_universe_status(ctx, master, live_refresh=master.get("scope") == "LIVE_RETRIEVAL")
    write_qualification_diagnostics(ctx, master=master)


def finalize_universe(
    ctx: QualificationContext,
    *,
    max_requests: int = 300,
    requests_per_minute: int = 30,
    broker: Any = None,
    enricher: Any = None,
    rebuild_universe: bool = False,
    replay_saved: bool = False,
    reclassify_saved: bool = False,
    capture_documents: bool = False,
) -> dict[str, Any]:
    """Refresh live membership, optionally enrich venues, and isolate bad rows."""
    from money.qualification.universe_normalize import normalize_universe
    from money.qualification.universe_progress import enrichment_order, record_network_progress
    from money.qualification.universe_providers import BulkProviderEnricher

    if replay_saved and reclassify_saved:
        raise ValueError("SAVED_MODE_CONFLICT")
    offline = replay_saved or reclassify_saved
    source_reference = None
    if reclassify_saved:
        prior = ctx.read_json("outputs/universe-provenance.json")
        if isinstance(prior, dict):
            source_reference = prior.get("source_provenance") or ctx.artifact(prior)
        else:
            preserved = ctx.read_json("state/universe-rebuild-source.json") or {}
            source_reference = preserved.get("source_provenance")
    migration = ensure_universe_policy(ctx, force=rebuild_universe)
    binding = credential_binding(ctx.environ)
    prepare_bulk_inputs(ctx, binding)
    # A standalone finalizer also refreshes these projections. No prior ready
    # result survives an interrupted or failed provider-enrichment invocation.
    scope_label = (
        "SAVED_LIVE_DERIVED_RECLASSIFICATION" if reclassify_saved
        else "SAVED_RESPONSE_REPLAY_ONLY" if replay_saved else "LIVE_RETRIEVAL"
    )
    _write_provider_projection(ctx, _provider_projection({"scope": scope_label}))
    try:
        if offline:
            if broker is not None or enricher is not None:
                raise ValueError("OFFLINE_REPLAY_CANNOT_OVERRIDE_TRANSPORT")
            raw_instruments, instruments, raw_exchanges, exchanges, observed, binding = (
                _saved_broker_metadata(ctx)
            )
            current_binding = credential_binding(ctx.environ)
            if current_binding is not None and current_binding != binding:
                raise ValueError("CURRENT_CREDENTIAL_BINDING_MISMATCH")
            if reclassify_saved:
                if (
                    not isinstance(source_reference, (list, tuple))
                    or len(source_reference) != 2
                    or not all(isinstance(value, str) for value in source_reference)
                ):
                    raise ValueError("RECORDED_LIVE_PROVENANCE_REQUIRED")
                source = json.loads(ctx.verify_artifact(source_reference[0], source_reference[1]))
                if (
                    source.get("scope") != "LIVE_RETRIEVAL"
                    or source.get("retrieval_environment") != "live"
                    or source.get("credential_binding_verified_this_run") is not True
                    or source.get("credential_binding_sha256") != binding
                    or source.get("instrument_response_hash")
                    != hashlib.sha256(raw_instruments).hexdigest()
                    or _time(source.get("retrieved_at")) != observed
                ):
                    raise ValueError("RECORDED_LIVE_PROVENANCE_REQUIRED")
        elif broker is None:
            raw_instruments, instruments, raw_exchanges, exchanges, observed = _broker_metadata(
                ctx, binding
            )
        else:
            raw_instruments, instruments = broker.metadata_response("instruments")
            ctx.check_secrets(raw_instruments)
            observed = utc_now()
            raw_exchanges, exchanges = _optional_exchanges(ctx, broker)
        # A failed new refresh never re-admits a previously saved catalogue.
        ctx.now = utc_now()
        responses = {
            "instruments": _store_response(ctx, raw_instruments),
            **(
                {"exchanges": _store_response(ctx, raw_exchanges)}
                if raw_exchanges is not None
                else {}
            ),
        }
    except Exception:
        failed = {
            "version": UNIVERSE_VERSION,
            "universe_policy_version": UNIVERSE_POLICY_VERSION,
            "scope": scope_label,
            "initial_filter": INITIAL_FILTER,
            "status": "REFRESH_FAILED",
            "observed_at": None,
            "valid_until": None,
            "stocks": [],
            "summary": None,
            "production_qualified": False,
            "errors": ["TRADING212_LIVE_METADATA_REQUIRED"],
        }
        _large_write(ctx, MASTER, json_bytes(failed))
        _large_write(ctx, MASTER_CSV, _csv([]))
        ctx.write_json(
            "outputs/universe-review-queue.json",
            {
                "universe_policy_version": UNIVERSE_POLICY_VERSION,
                "status": "REFRESH_FAILED",
                "venue_review_required": False,
                "instruments": [],
                "errors": failed["errors"],
            },
        )
        _write_provider_projection(ctx, _provider_projection(failed))
        for path in ("outputs/universe-review-work.json", "outputs/universe-retrieval-facts.json"):
            ctx.write_json(
                path,
                {
                    "universe_policy_version": UNIVERSE_POLICY_VERSION,
                    "scope": scope_label,
                    "status": "REFRESH_FAILED",
                    "errors": failed["errors"],
                },
            )
        ctx.block(
            "TRADING212_LIVE_METADATA_REQUIRED",
            "Fetch live accessible instruments using the configured Trading 212 credentials; no old membership was served. Exchanges are optional enrichment.",
        )
        _update_universe_diagnostics(ctx, failed, capture_documents=capture_documents and not offline)
        return failed
    rows = normalize_universe(
        instruments, exchanges, observed_at=observed, venue_reviews=_venues(ctx)
    )
    enricher = enricher or BulkProviderEnricher(
        ctx,
        max_requests=0 if offline else max_requests,
        requests_per_minute=requests_per_minute,
        offline=offline,
    )
    for row in rows:
        row["broker_response_refs"] = responses
        row["provider_enrichment_input"] = row["universe_member"] and row["identity_valid"]
    for index in enrichment_order(ctx, rows, binding, replay=offline):
        row = rows[index]
        before = getattr(enricher, "requests_used", 0)
        try:
            rows[index] = enricher.enrich(row)
        except Exception:
            row["provider_reasons"] = ["PROVIDER_ROW_FAILED"]
        finally:
            if not offline and getattr(enricher, "requests_used", 0) > before:
                record_network_progress(ctx, row, binding)
    ctx.now = utc_now()
    # A long provider batch must not extend any review or membership lifetime.
    ethics = _ethics(ctx)
    canonical_counts = Counter(
        row.get("identifiers", {}).get("ticker")
        for row in rows
        if isinstance(row.get("identifiers"), dict)
    )
    for row in rows:
        canonical = (row.get("identifiers") or {}).get("ticker")
        if canonical and canonical_counts[canonical] > 1:
            row.update(
                identity_valid=False,
                qualification_state="UNRESOLVED_IDENTITY",
                reasons=[*row.get("reasons", []), "CANONICAL_TICKER_COLLISION"],
            )
    rights = _qualify_rights(ctx, rows)
    reviews = []
    for row in rows:
        reviewed = classify_row(
            ctx, row, ethics, rights, admission_allowed=not offline and binding is not None
        )
        if reviewed is not None:
            reviews.append(reviewed.model_dump(mode="json"))
    provenance = {
        "universe_policy_version": UNIVERSE_POLICY_VERSION,
        "scope": scope_label,
        "credential_binding_verified_this_run": not offline and binding is not None,
        "retrieval_environment": "live",
        "retrieved_at": observed.isoformat(),
        "reclassified_at": ctx.now.isoformat(),
        "credential_binding_sha256": binding,
        "instrument_response_hash": hashlib.sha256(raw_instruments).hexdigest(),
        "exchange_response_hash": hashlib.sha256(raw_exchanges).hexdigest()
        if raw_exchanges is not None
        else None,
        "exchange_enrichment_status": "RETRIEVED"
        if raw_exchanges is not None
        else "UNAVAILABLE_OR_INVALID",
        "initial_filter": INITIAL_FILTER,
        "response_artifacts": responses,
        **({"source_provenance": source_reference} if reclassify_saved else {}),
    }
    summary = _summary(rows, ctx.now)
    summary["provider_requests_this_run"] = getattr(enricher, "requests_used", 0)
    provenance.update(
        {
            key: summary[key]
            for key in (
                "raw_instruments",
                "gbx_stocks",
                "venue_resolved",
                "excluded",
                "unresolved",
            )
        }
    )
    # Separate compact admission output from the larger full discovery projection.
    from money.qualification.universe_catalog import eligibility_catalogs

    membership_chunks = eligibility_catalogs(ctx, reviews) if reviews else ()
    provenance_artifact = ctx.artifact(provenance)
    membership = ctx.artifact(
        {
            "kind": "complete-qualified-universe-membership-v1",
            "provenance": provenance,
            "eligibility_catalogs": membership_chunks,
            "qualified_count": len(reviews),
        }
    )
    result = {
        "version": UNIVERSE_VERSION,
        "universe_policy_version": UNIVERSE_POLICY_VERSION,
        "scope": scope_label,
        "policy_migration": migration,
        "initial_filter": INITIAL_FILTER,
        "status": "RECLASSIFIED" if reclassify_saved else "REPLAYED" if replay_saved else "REFRESHED",
        "observed_at": provenance["retrieved_at"],
        "valid_until": (_time(provenance["retrieved_at"]) + ELIGIBILITY_MAXIMUM_AGE).isoformat(),
        "production_qualified": False,
        "provenance": provenance,
        "summary": summary,
        "stocks": rows,
        "eligibility_reviews": reviews,
        "universe_artifact": membership,
        "universe_provenance": provenance_artifact,
        "provider_qualifications": list(rights.values()),
        "provider_request_budget": max_requests,
        "requests_per_minute": requests_per_minute,
        "artifact_refs": sorted(ctx.artifact_refs),
    }
    queue = {
        "universe_policy_version": UNIVERSE_POLICY_VERSION,
        "scope": scope_label,
        "credential_binding_sha256": binding,
        "bulk_evidence_work_file": "outputs/universe-review-work.json",
        "provider_rights_reviews": [
            "inputs/provider-rights/eodhd.json",
            "inputs/provider-rights/companies-house.json",
        ],
        "machine_enrichment": {
            "requests_this_run": summary["provider_requests_this_run"],
            "deferred": summary["provider_deferred"],
            "failure_counts": summary["provider_failure_counts"],
            "action": "Rerun the finalizer to resume bounded provider enrichment; these are not human identity approvals.",
        },
        "venue_review_required": False,
        "venue_information": list(
            {
                r.get("exchange_row_sha256"): {
                    key: r.get(key)
                    for key in (
                        "exchange_id",
                        "exchange_name",
                        "exchange_row_sha256",
                        "mic",
                        "country",
                    )
                }
                for r in rows
                if r.get("exchange_id") is not None and (not r.get("country") or not r.get("mic"))
            }.values()
        ),
        "instruments": [
            {
                **{
                    key: r.get(key)
                    for key in (
                        "trading212_id",
                        "isin",
                        "name",
                        "qualification_state",
                        "provider_reasons",
                        "provider_evidence",
                    )
                },
                "reasons": [
                    reason
                    for reason in r.get("reasons", [])
                    if reason
                    not in {
                        "PROVIDER_RIGHTS_AND_DATASET_QUALIFICATION_REQUIRED",
                    }
                ],
            }
            for r in rows
            if r["universe_member"]
            and (
                r["qualification_state"].startswith("UNRESOLVED_")
                or r["qualification_state"] in {"EXPIRED", "STALE_EVIDENCE"}
            )
        ],
    }
    _large_write(ctx, MASTER, json_bytes(result))
    _large_write(ctx, MASTER_CSV, _csv(rows))
    _large_write(ctx, "outputs/universe-review-queue.json", json_bytes(queue))
    _large_write(
        ctx, "outputs/universe-review-work.json", json_bytes(_review_work(rows, provenance))
    )
    ctx.write_json(
        "outputs/universe-retrieval-facts.json",
        {
            "universe_policy_version": UNIVERSE_POLICY_VERSION,
            "status": "UNREVIEWED_MACHINE_FACTS",
            "provenance": provenance,
            "instruction": "Technical retrieval provenance only. No account-type or current-purchase claim is made. Legacy account-scope reviews are ignored and preserved unchanged.",
        },
    )
    ctx.write_json("outputs/universe-provenance.json", provenance)
    _write_provider_projection(ctx, _provider_projection(result))
    _update_universe_diagnostics(ctx, result, capture_documents=capture_documents and not offline)
    return result


def run_bulk_provider_stages(ctx: QualificationContext) -> dict[str, Any]:
    """Feed genuine bulk eligibility into unchanged supplemental/release gates."""
    from money.qualification.providers import (
        _additional_sources,
        _filter_source_coverage,
        _financial_documents,
        _verified_instrument,
    )

    master = finalize_universe(ctx)
    reviews = tuple(
        EligibilityReview.model_validate(value) for value in master.get("eligibility_reviews", [])
    )
    result = _provider_projection(master)
    result["stage"] = "SUPPLEMENTAL_RESEARCH_QUALIFICATION"
    refs = set(ctx.artifact_refs)
    supplemental = ctx.read_json("inputs/universe/supplemental.json") or {}
    paths = supplemental.get("instruments", {})
    rows_by_isin = {
        row.get("isin"): row
        for row in master.get("stocks", [])
        if row.get("qualification_state") == "QUALIFIED"
    }
    for review in reviews:
        try:
            path = paths.get(review.identifiers.isin)
            if not isinstance(path, str) or not path.startswith("inputs/"):
                continue
            row = rows_by_isin[review.identifiers.isin]
            foreign = None
            if row.get("companies_house_state") == "NOT_APPLICABLE":
                country = row.get("issuer_facts", {}).get("CountryISO")
                if (
                    not isinstance(country, str)
                    or country == "GB"
                    or not re.fullmatch("[A-Z]{2}", country)
                ):
                    raise ValueError("FOREIGN_ISSUER_PROOF_REQUIRED")
                evidence = row["provider_evidence"]
                if not evidence:
                    raise ValueError("FOREIGN_ISSUER_PROOF_REQUIRED")
                for ref in evidence:
                    ctx.verify_artifact(ref["sha256"], ref["path"])
                proof = ctx.artifact(
                    {
                        "kind": "exact-provider-issuer-jurisdiction-v1",
                        "identifiers": review.identifiers.model_dump(mode="json"),
                        "registered_country": country,
                        "provider_evidence": evidence,
                    }
                )
                refs.add(proof)
                foreign = country, proof[0]
            instrument = _verified_instrument(
                ctx, review, path, refs, foreign_issuer_evidence=foreign
            )
            if instrument is not None:
                result["instruments"].append(instrument.model_dump(mode="json"))
        except (ValueError, OSError, KeyError, TypeError):
            result["candidate_exclusions"].append(
                {"ticker": review.metadata.ticker, "code": "SUPPLEMENTAL_REVIEW_REQUIRED"}
            )
    _financial_documents(ctx, result, refs)
    _additional_sources(ctx, result, refs)
    _filter_source_coverage(ctx, result)
    result["complete"] = bool(
        result["instruments"]
        and {"eodhd", "companies-house"}
        <= {q["provider"] for q in result["provider_qualifications"]}
        and any(
            q["provider"] == "companies-house" and "financial" in q["datasets"]
            for q in result["provider_qualifications"]
        )
        and any(i["filing_documents"] for i in result["instruments"])
    )
    if not reviews:
        ctx.block(
            "BULK_UNIVERSE_NO_QUALIFIED_MEMBERS",
            "Resolve provider and ethical evidence for current members; venue enrichment is optional and no account attestation is required.",
        )
    if not result["instruments"]:
        ctx.block(
            "QUALIFIED_INSTRUMENT_EVIDENCE_REQUIRED",
            "Attach approved spread, cost, action, financial and PIT sources for qualified members through inputs/universe/supplemental.json; unresolved peers do not block ready members.",
        )
    result["artifact_refs"] = sorted(refs | ctx.artifact_refs)
    # This is a resumable projection, never a qualification proof. Individual
    # referenced artifacts retain their unchanged two-megabyte verification bound.
    _write_provider_projection(ctx, result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-provider-requests", type=int, default=300)
    parser.add_argument("--requests-per-minute", type=int, default=30)
    parser.add_argument(
        "--rebuild-universe",
        action="store_true",
        help="Rebuild only derived universe views, preserving raw evidence and reviews. Policy changes do this automatically.",
    )
    parser.add_argument(
        "--replay-saved",
        action="store_true",
        help="Reclassify hash-verified saved responses without network access. Diagnostic replay only; cannot approve eligibility or production.",
    )
    parser.add_argument(
        "--reclassify-saved",
        action="store_true",
        help="Migrate derived classifications from hash-verified recorded live evidence without network access, new retrieval claims or eligibility approval.",
    )
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.max_provider_requests <= 100000 or not 1 <= args.requests_per_minute <= 60:
            raise ValueError("PROVIDER_BUDGET_INVALID")
        repo = Path(__file__).resolve().parents[3]
        selected_qlib = qlib_enabled(os.environ)
        selections = load_inference_selections(repo, os.environ)
        root = args.output or Path(
            "data/qualified/local-inference"
            if any(item.is_local for item in selections.values())
            else "data/qualified/live"
        )
        ctx = QualificationContext(root, repo, dict(os.environ), utc_now())
        with ctx.locked():
            result = finalize_universe(
                ctx,
                max_requests=args.max_provider_requests,
                requests_per_minute=args.requests_per_minute,
                rebuild_universe=args.rebuild_universe,
                replay_saved=args.replay_saved,
                reclassify_saved=args.reclassify_saved,
                capture_documents=not (args.replay_saved or args.reclassify_saved),
            )
        print(
            "TRADING 212 SAVED RESPONSE REPLAY"
            if args.replay_saved or args.reclassify_saved
            else "TRADING 212 LIVE GBX STOCK UNIVERSE"
        )
        print(f"Universe policy: {UNIVERSE_POLICY_VERSION}")
        if result["status"] not in {"REFRESHED", "REPLAYED", "RECLASSIFIED"}:
            print("Live refresh failed; counts unavailable. No stale membership admitted.")
            return 2
        s = result["summary"]
        print(f"Raw instruments: {s['raw_instruments']}\nGBX stocks: {s['gbx_stocks']}")
        print(
            f"Identity valid: {s['identity_valid']}\nProvider-stage input count: {s['provider_stage_input_count']}"
        )
        print(f"Venue resolved (informational only): {s['venue_resolved']}/{s['gbx_stocks']}")
        print("\nQUALIFICATION")
        labels = {
            "Qualified": "QUALIFIED",
            "Identity unresolved": "UNRESOLVED_IDENTITY",
            "Provider unresolved": "UNRESOLVED_PROVIDER_MAPPING",
            "Ethically excluded": "EXCLUDED_ETHICAL",
            "Stale": "STALE_EVIDENCE",
            "Expired": "EXPIRED",
        }
        for label, state in labels.items():
            print(f"{label}: {s['states'].get(state, 0)}")
        print(f"Ethical review required: {s['ethical_review_required']}")
        print(
            f"\nPROVIDERS\nEODHD mappings attempted: {s['eodhd_mapping_attempted']}/{s['gbx_stocks']} (network: {s['eodhd_lookups_network']}; cached: {s['eodhd_lookups_cached']})"
        )
        print(f"EODHD mapped: {s['eodhd_mapped']}/{s['gbx_stocks']}")
        print(f"Provider requests this run: {s['provider_requests_this_run']}; deferred: {s['provider_deferred']}")
        print(
            f"Companies House mapped: {s['companies_house_mapped']}/{s['companies_house_applicable']} applicable; applicability unknown: {s['companies_house_applicability_unknown']}"
        )
        for label, name in (
            ("Current OHLCV", "ohlcv"),
            ("Corporate actions", "corporate_action"),
            ("News", "news"),
        ):
            print(f"{label}: {s['datasets'][name]}/{s['gbx_stocks']}")
        print("Universe qualification is not production/native/release qualification.")
        print("Bulk evidence and remaining reviews: outputs/universe-review-work.json")
        print("Provider progress: outputs/universe-enrichment-progress.json")
        print("Prioritized dependencies and human reviews: outputs/NEXT_ACTIONS.md; outputs/REVIEW_TASKS.md")
        if args.replay_saved or args.reclassify_saved:
            print(
                "Saved-response replay only; original retrieval time preserved. No network access or eligibility approval."
            )
        print("After resolving bulk review inputs, continue with:")
        mode_prefix = "" if selected_qlib else "MONEY_QLIB_ENABLED=false "
        print(
            f"railway run --service Money --environment production sh -c '{mode_prefix}MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/build_live_qualification.py'"
        )
        return 0 if s["qualified"] else 2
    except Exception:
        print(
            "UNIVERSE QUALIFICATION BLOCKED: inspect inputs and safe provider status; no raw exception or credential was printed."
        )
        return 2
