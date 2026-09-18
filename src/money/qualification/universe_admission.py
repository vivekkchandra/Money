"""Cheap, authenticated broker admission, deliberately not release approval.

Optional provider or company evidence can improve research, never create broker
membership. Consumers verify the immutable raw retrieval again rather than trust
the editable operator projection or a cached RESEARCH_ELIGIBLE label.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from money.adapters.eligibility import ELIGIBILITY_MAXIMUM_AGE
from money.qualification.core import QualificationContext
from money.qualification.universe_normalize import normalize_universe

ENRICHMENT_SOURCES = (
    "eodhd_market_data", "eodhd_fundamentals", "companies_house",
    "official_disclosures", "news",
)


def classify_research_admission(
    row: dict[str, Any], *, now: datetime, authenticated_live: bool
) -> None:
    """Classify broker facts only; ethics and company qualification are annotations."""
    reasons = []
    if row.get("universe_member") is not True:
        reasons.append("STOCK_GBP_OR_GBX_REQUIRED")
    if row.get("basic_identity_valid", row.get("identity_valid")) is not True:
        reasons.extend(row.get("basic_identity_reasons", []) or ["BASIC_IDENTITY_INVALID"])
    if not authenticated_live:
        reasons.append("LIVE_AUTHENTICATED_REFRESH_REQUIRED")
    try:
        observed = datetime.fromisoformat(row["observed_at"])
        until = datetime.fromisoformat(row.get("broker_valid_until", row["valid_until"]))
        if not observed <= now < min(until, observed + ELIGIBILITY_MAXIMUM_AGE):
            reasons.append("BROKER_MEMBERSHIP_EXPIRED")
    except (KeyError, TypeError, ValueError):
        reasons.append("BROKER_MEMBERSHIP_TIMESTAMP_INVALID")
    row["research_state"] = "DISCOVERED" if reasons else "RESEARCH_ELIGIBLE"
    row["research_reasons"] = sorted(set(reasons))
    row["ethical_status"] = (
        row.get("ethical_state") if row.get("ethical_state") in {"PASS", "FAIL", "UNKNOWN"}
        else "NOT_SCREENED"
    )
    row["research_admission_is_production_approval"] = False
    row["research_valid_until"] = row.get("broker_valid_until", row.get("valid_until"))


def optional_enrichment_status(
    row: Mapping[str, Any], environ: Mapping[str, str], *, now: datetime
) -> dict[str, str]:
    """Report honest coarse coverage without turning missing company data into gates."""
    datasets = row.get("provider_datasets") or {}
    diagnostics = row.get("provider_request_diagnostics") or []
    try:
        current = (
            datetime.fromisoformat(row["provider_evidence_observed_at"])
            <= now < datetime.fromisoformat(row["provider_evidence_valid_until"])
        )
    except (KeyError, ValueError, TypeError):
        current = False

    def dataset(name: str) -> bool:
        fact = datasets.get(name, {})
        return isinstance(fact, dict) and fact.get("status") == "RETRIEVED" and bool(fact.get("record_count"))

    facts = {
        "eodhd_market_data": dataset("eodhd:ohlcv"),
        "eodhd_fundamentals": bool(row.get("issuer_facts")) and str(row.get("issuer_facts_source", "")).startswith("EODHD fundamentals"),
        "companies_house": dataset("companies-house:company") and bool(row.get("companies_house_number")),
        "official_disclosures": row.get("issuer_identity_state") == "VERIFIED" and bool(row.get("issuer_source_evidence")),
        "news": dataset("eodhd:news"),
    }
    result = {}
    for source, available in facts.items():
        key = "COMPANIES_HOUSE_API_KEY" if source == "companies_house" else "EODHD_API_KEY"
        endpoints = {
            "eodhd_market_data": {"eod"},
            "eodhd_fundamentals": {"fundamentals"},
            "news": {"news"},
        }.get(source, set())
        denied = any(
            isinstance(item, dict)
            and item.get("http_status") in {401, 402, 403}
            and (
                (item.get("provider") == "eodhd" and item.get("endpoint") in endpoints)
                or (source == "companies_house" and item.get("provider") == "companies-house")
            )
            for item in diagnostics
        )
        result[source] = (
            "AVAILABLE" if available and current else "STALE" if available else
            "ACCESS_DENIED" if denied else
            "NOT_CONFIGURED" if source != "official_disclosures" and not environ.get(key) else
            "UNAVAILABLE"
        )
    return result


def current_research_rows(
    ctx: QualificationContext, master: dict[str, Any]
) -> list[dict[str, Any]]:
    """Verify live provenance and raw identity before returning admitted instruments.

    Replay, changed credentials, stale retrievals, altered projections and copied
    labels never create admission. Raw optional enrichment is left unapproved.
    """
    from money.qualification.universe import _restore_response
    from money.qualification.universe_status import live_metadata_state

    metadata = live_metadata_state(ctx, master)
    if not metadata.get("current") or not metadata.get("current_process_binding_verified"):
        raise ValueError("CURRENT_AUTHENTICATED_BROKER_UNIVERSE_REQUIRED")
    provenance = master["provenance"]
    _, instruments = _restore_response(ctx, provenance["response_artifacts"]["instruments"])
    observed = datetime.fromisoformat(provenance["retrieved_at"])
    normalized = normalize_universe(instruments, (), observed_at=observed)
    projections = master.get("stocks", [])
    if not isinstance(projections, list) or len(projections) != len(normalized):
        raise ValueError("RESEARCH_UNIVERSE_PROJECTION_MISMATCH")
    admitted = []
    for raw_row, projected in zip(normalized, projections, strict=True):
        if not isinstance(projected, dict) or any(
            raw_row.get(key) != projected.get(key)
            for key in (
                "source_index", "trading212_id", "isin", "quote_currency",
                "instrument_type", "instrument_row_sha256", "observed_at", "name", "short_ticker",
            )
        ):
            raise ValueError("RESEARCH_UNIVERSE_PROJECTION_MISMATCH")
        classify_research_admission(raw_row, now=ctx.now, authenticated_live=True)
        if raw_row["research_state"] == "RESEARCH_ELIGIBLE":
            # Canonical basic identity/freshness wins over mutable enrichment fields.
            admitted.append({**projected, **{
                key: raw_row[key] for key in (
                    "basic_identity_valid", "basic_identity_reasons", "research_state",
                    "research_reasons", "observed_at", "valid_until", "broker_valid_until",
                    "research_valid_until",
                )
            }})
    return admitted
