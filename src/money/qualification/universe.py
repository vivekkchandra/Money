"""Bulk, read-only ISA discovery and evidence admission, never trading authority.

Metadata membership, credential/account provenance, evidence rights and ethical
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
from money.research.inference_config import load_inference_selections
from money.schemas.contracts import (
    EXCLUDED_ACTIVITIES,
    Contract,
    InstrumentMetadata,
    ResearchMandate,
    utc_now,
)

MASTER = "outputs/uk-isa-stock-universe.json"
MODE = "state/bulk-universe-mode.json"
MAX_MASTER_BYTES = 64_000_000
UNIVERSE_VERSION = "money-gbx-isa-universe-v2"
INITIAL_FILTER = {"instrument_type": "STOCK", "quote_currency": "GBX", "venue_required": False}


def _stamp() -> dict[str, Any]:
    return dict(
        status="UNRESOLVED", prepared_by=None, reviewed_by=None, reviewed_at=None, valid_until=None
    )


class AccountScopeReview(Contract):
    """One explicit account/endpoint attestation, not 1,000 identity signatures."""

    review: dict[str, Any]
    account_context: Literal["STOCKS_AND_SHARES_ISA"]
    retrieval_environment: Literal["live"]
    credential_binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    accessible_response_is_account_scoped: Literal[True]
    accessible_response_confirms_current_buy_availability: Literal[True]
    evidence_files: tuple[str, ...] = Field(min_length=1, max_length=10)


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
    """Bind a review to high-entropy credentials without persisting either key."""
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
    ctx.template(MODE, {"version": "money-bulk-universe-v1"})
    ctx.template(
        "inputs/universe/account-scope.json",
        {
            "review": _stamp(),
            "account_context": "STOCKS_AND_SHARES_ISA",
            "retrieval_environment": "live",
            "credential_binding_sha256": binding,
            "accessible_response_is_account_scoped": None,
            "accessible_response_confirms_current_buy_availability": None,
            "evidence_files": [],
        },
    )
    ctx.template("inputs/universe/venues.json", {"review": _stamp(), "venues": []})
    ctx.template("inputs/universe/ethics.json", {"review": _stamp(), "instruments": []})
    ctx.template("inputs/universe/supplemental.json", {"instruments": {}})
    ctx.write_json(
        "inputs/universe/account-scope.schema.json", AccountScopeReview.model_json_schema()
    )
    ctx.write_json("inputs/universe/ethical-entry.schema.json", EthicalEntry.model_json_schema())
    ctx.write_bytes(
        "inputs/universe/README.md",
        b"""# Bulk review inputs

These inputs are unsigned. Discovery never approves them. Existing historical
per-stock preparation is not selected by this workflow.

Initial membership requires only STOCK + GBX in the current live Trading 212
accessible-instruments response. GBP is not admitted. Venue, MIC, country and
ISIN prefix are not membership requirements; genuine identity conflicts still
remain unresolved. Membership alone approves none of the reviews below.

- `account-scope.json`: independently verify this credential binding is the
  Stocks & Shares ISA key, that the endpoint response is account-specific, and
  what genuine evidence establishes current purchase availability. Metadata has
  no documented account-type/buy-enabled field. Never approve from listing alone.
  Account review must also remain within the 24-hour eligibility freshness rule.
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


def _scope(ctx: QualificationContext, binding: str | None) -> dict[str, Any] | None:
    try:
        value = ctx.read_json("inputs/universe/account-scope.json")
        scope = AccountScopeReview.model_validate(value)
        review = _review(ctx, scope.review)
        if (
            scope.credential_binding_sha256 != binding
            or ctx.now >= review.reviewed_at + ELIGIBILITY_MAXIMUM_AGE
        ):
            raise ValueError("ISA_SCOPE_STALE_OR_DIFFERENT_KEY")
        refs = _attach(ctx, scope.evidence_files)
        ref = ctx.artifact(
            {"kind": "reviewed-isa-account-provenance-v1", "review": value, "evidence": refs}
        )
        return {"review": review, "proof": ref}
    except (ValueError, OSError, TypeError):
        return None


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
    ctx.check_secrets(raw)
    parts = [
        ctx.artifact(raw[start : start + 1_500_000]) for start in range(0, len(raw), 1_500_000)
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
    scope: dict[str, Any] | None,
    ethics: dict[str, dict[str, Any]],
    rights: dict[str, Any],
) -> EligibilityReview | None:
    """Admission consumes actual evidence; missing conditions remain explicit."""
    initial = row["qualification_state"]
    row.update(
        ethical_state="ETHICAL_REVIEW_REQUIRED",
        isa_account_provenance_state=(
            "REVIEWED_STOCKS_AND_SHARES_ISA" if scope else "UNRESOLVED_ISA_SCOPE"
        ),
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
    if scope is None:
        state = "UNRESOLVED_ISA_SCOPE"
        reasons.append("ACCOUNT_AND_CURRENT_BUY_PROVENANCE_REQUIRED")
    required = {"eodhd"}
    if row.get("companies_house_state") != "NOT_APPLICABLE":
        required.add("companies-house")
        if (
            row.get("companies_house_state") != "MAPPED"
            or identifiers is None
            or not identifiers.companies_house_number
        ):
            if state == "QUALIFIED":
                state = "UNRESOLVED_PROVIDER_MAPPING"
            reasons.append("VERIFIED_ISSUER_JURISDICTION_AND_COMPANY_IDENTITY_REQUIRED")
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
        try:
            first = _time(row["provider_evidence_observed_at"])
            last = _time(row["provider_evidence_valid_until"])
            if not first <= ctx.now < min(last, first + ELIGIBILITY_MAXIMUM_AGE):
                raise ValueError("PROVIDER_EVIDENCE_EXPIRED")
            latest_bar = _time(datasets["eodhd:ohlcv"]["latest_observation"])
            # Preserve the existing market-quality 96-hour freshness requirement.
            from money.data.quality.market import MarketQualityPolicy

            if (
                not latest_bar
                <= ctx.now
                <= latest_bar + timedelta(hours=MarketQualityPolicy().maximum_latest_age_hours)
            ):
                raise ValueError("OHLCV_EVIDENCE_EXPIRED")
        except (ValueError, TypeError, KeyError):
            state = "STALE_EVIDENCE"
            row["evidence_freshness"] = "STALE_PROVIDER_EVIDENCE"
            reasons.append("CURRENT_TIMESTAMPED_PROVIDER_OBSERVATIONS_REQUIRED")
    if state != "QUALIFIED" or identifiers is None or scope is None or assessed is None:
        row.update(qualification_state=state, reasons=sorted(set(reasons)))
        return None
    try:
        verified_at = min(observed, scope["review"].reviewed_at, assessed["stamp"].reviewed_at)
        deadline = min(
            until,
            identifiers.valid_until,
            scope["review"].valid_until,
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
            isa_available=True,
            currently_available=True,
            business_activities=assessed["entry"].business_activities,
            activities_verified=True,
            verified_at=verified_at,
            source="hash-bound bulk ISA account and material-exposure evidence",
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
                "kind": "bulk-eligibility-join-v1",
                "metadata": metadata.model_dump(mode="json"),
                "identifiers": identifiers.model_dump(mode="json"),
                "account_review": scope["proof"],
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
        "eodhd_lookups_network": sum(r.get("eodhd_lookup_origin") == "NETWORK" for r in relevant),
        "eodhd_lookups_cached": sum(r.get("eodhd_lookup_origin") == "CACHE" for r in relevant),
        "eodhd_mapped": sum(bool(r.get("eodhd_symbol")) for r in relevant),
        "companies_house_mapped": sum(bool(r.get("companies_house_number")) for r in relevant),
        "companies_house_applicable": sum(
            r.get("issuer_facts", {}).get("CountryISO") == "GB" for r in relevant
        ),
        "companies_house_applicability_unknown": sum(
            r.get("issuer_facts", {}).get("CountryISO") != "GB"
            and r.get("companies_house_state") != "NOT_APPLICABLE"
            for r in relevant
        ),
        "ethical_review_required": sum(
            r.get("ethical_state") == "ETHICAL_REVIEW_REQUIRED" for r in relevant
        ),
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
        "isa_account_provenance_state",
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
    raw = b"".join(ctx.verify_artifact(*part) for part in descriptor["chunks"])
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
    under the existing ten-minute refresh policy, bound to this exact ISA key.
    """
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
    ctx.write_json(cache_path, state)
    return raw_instruments, instruments, raw_exchanges, exchanges, observed


def finalize_universe(
    ctx: QualificationContext,
    *,
    max_requests: int = 300,
    requests_per_minute: int = 30,
    broker: Any = None,
    enricher: Any = None,
) -> dict[str, Any]:
    """Refresh live membership, optionally enrich venues, and isolate bad rows."""
    from money.qualification.universe_normalize import normalize_universe
    from money.qualification.universe_providers import BulkProviderEnricher

    binding = credential_binding(ctx.environ)
    prepare_bulk_inputs(ctx, binding)
    try:
        if broker is None:
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
        _large_write(ctx, "outputs/uk-isa-stock-universe.csv", _csv([]))
        ctx.block(
            "TRADING212_LIVE_METADATA_REQUIRED",
            "Fetch live accessible instruments using the ISA credential; no old membership was served. Exchanges are optional enrichment.",
        )
        return failed
    rows = normalize_universe(
        instruments, exchanges, observed_at=observed, venue_reviews=_venues(ctx)
    )
    scope, ethics = _scope(ctx, binding), _ethics(ctx)
    enricher = enricher or BulkProviderEnricher(
        ctx, max_requests=max_requests, requests_per_minute=requests_per_minute
    )
    for index, row in enumerate(rows):
        row["broker_response_refs"] = responses
        if not row["universe_member"]:
            continue
        try:
            rows[index] = enricher.enrich(row)
        except Exception:
            row["provider_reasons"] = ["PROVIDER_ROW_FAILED"]
    ctx.now = utc_now()
    # A long provider batch must not extend any review or membership lifetime.
    scope, ethics = _scope(ctx, binding), _ethics(ctx)
    canonical_counts = Counter(
        row.get("identifiers", {}).get("ticker")
        for row in rows
        if isinstance(row.get("identifiers"), dict)
    )
    for row in rows:
        canonical = (row.get("identifiers") or {}).get("ticker")
        if canonical and canonical_counts[canonical] > 1:
            row.update(
                qualification_state="UNRESOLVED_IDENTITY",
                reasons=[*row.get("reasons", []), "CANONICAL_TICKER_COLLISION"],
            )
    rights = _qualify_rights(ctx, rows)
    reviews = []
    for row in rows:
        reviewed = classify_row(ctx, row, scope, ethics, rights)
        if reviewed is not None:
            reviews.append(reviewed.model_dump(mode="json"))
    provenance = {
        "account_context": "STOCKS_AND_SHARES_ISA" if scope else "UNVERIFIED",
        "requested_account_context": "STOCKS_AND_SHARES_ISA",
        "retrieval_environment": "live",
        "retrieved_at": observed.isoformat(),
        "credential_binding_sha256": binding,
        "account_review_hash": scope["proof"][0] if scope else None,
        "instrument_response_hash": hashlib.sha256(raw_instruments).hexdigest(),
        "exchange_response_hash": hashlib.sha256(raw_exchanges).hexdigest()
        if raw_exchanges is not None
        else None,
        "exchange_enrichment_status": "RETRIEVED"
        if raw_exchanges is not None
        else "UNAVAILABLE_OR_INVALID",
        "initial_filter": INITIAL_FILTER,
        "response_artifacts": responses,
    }
    summary = _summary(rows, ctx.now)
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
        "initial_filter": INITIAL_FILTER,
        "status": "REFRESHED",
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
        "artifact_refs": sorted(ctx.artifact_refs),
    }
    queue = {
        "account_scope_required": scope is None,
        "credential_binding_sha256": binding,
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
                key: r.get(key)
                for key in (
                    "trading212_id",
                    "isin",
                    "name",
                    "qualification_state",
                    "reasons",
                    "provider_reasons",
                    "provider_evidence",
                )
            }
            for r in rows
            if r["qualification_state"].startswith("UNRESOLVED_")
        ],
    }
    _large_write(ctx, MASTER, json_bytes(result))
    _large_write(ctx, "outputs/uk-isa-stock-universe.csv", _csv(rows))
    _large_write(ctx, "outputs/universe-review-queue.json", json_bytes(queue))
    ctx.write_json("outputs/universe-provenance.json", provenance)
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
    result: dict[str, Any] = {
        "complete": False,
        "instruments": [],
        "eligibility_reviews": [r.model_dump(mode="json") for r in reviews],
        "qualified_universe": [r.model_dump(mode="json") for r in reviews],
        "universe_artifact": master.get("universe_artifact"),
        "universe_provenance": master.get("universe_provenance"),
        "universe_account_binding_sha256": (master.get("provenance") or {}).get(
            "credential_binding_sha256"
        ),
        "provider_qualifications": master.get("provider_qualifications", []),
        "candidate_exclusions": [],
        "candidate_counts": {"GBP": 0, "GBX": 0},
        "eligible_counts": {
            currency: sum(r.metadata.quote_currency == currency for r in reviews)
            for currency in ("GBP", "GBX")
        },
        "unresolved_candidate_count": (master.get("summary") or {}).get("unresolved", 0),
    }
    for row in master.get("stocks", []):
        if row.get("universe_member") is True:
            result["candidate_counts"]["GBX"] += 1
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
            "Resolve the bulk account, provider and ethical evidence queue; venue enrichment is optional and no ticker selection is required.",
        )
    if not result["instruments"]:
        ctx.block(
            "QUALIFIED_INSTRUMENT_EVIDENCE_REQUIRED",
            "Attach approved spread, cost, action, financial and PIT sources for qualified members through inputs/universe/supplemental.json; unresolved peers do not block ready members.",
        )
    result["artifact_refs"] = sorted(refs | ctx.artifact_refs)
    # This is a resumable projection, never a qualification proof. Individual
    # referenced artifacts retain their unchanged two-megabyte verification bound.
    _large_write(ctx, "state/provider-stage.json", json_bytes(result))
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-provider-requests", type=int, default=300)
    parser.add_argument("--requests-per-minute", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.max_provider_requests <= 100000 or not 1 <= args.requests_per_minute <= 60:
            raise ValueError("PROVIDER_BUDGET_INVALID")
        repo = Path(__file__).resolve().parents[3]
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
            )
        print("TRADING 212 LIVE ISA UNIVERSE")
        if result["status"] != "REFRESHED":
            print("Live refresh failed; counts unavailable. No stale membership admitted.")
            return 2
        s = result["summary"]
        print(f"Raw instruments: {s['raw_instruments']}\nGBX stocks: {s['gbx_stocks']}")
        print(f"Venue resolved (informational only): {s['venue_resolved']}/{s['gbx_stocks']}")
        print("\nQUALIFICATION")
        labels = {
            "Qualified": "QUALIFIED",
            "Identity unresolved": "UNRESOLVED_IDENTITY",
            "Provider unresolved": "UNRESOLVED_PROVIDER_MAPPING",
            "ISA scope unresolved": "UNRESOLVED_ISA_SCOPE",
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
        print("After resolving bulk review inputs, continue with:")
        print(
            "railway run --service Money --environment production sh -c 'MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/build_live_qualification.py'"
        )
        return 0 if s["qualified"] else 2
    except Exception:
        print(
            "UNIVERSE QUALIFICATION BLOCKED: inspect inputs and safe provider status; no raw exception or credential was printed."
        )
        return 2
