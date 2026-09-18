"""Fair resumable work ordering; cursors never carry qualification decisions."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from money.qualification.core import QualificationContext
from money.qualification.universe_policy import UNIVERSE_POLICY_VERSION

CURSOR = "state/universe-enrichment-cursor.json"
SCHEDULER_VERSION = "money-bulk-round-robin-v1"
PROGRESS = "outputs/universe-enrichment-progress.json"
_ACCESS_ERRORS = {
    401: "PROVIDER_AUTHENTICATION_REJECTED",
    402: "PROVIDER_PAYMENT_REQUIRED",
    403: "PROVIDER_ACCESS_DENIED",
}
_RETRYABLE_ERRORS = {
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_UPSTREAM_UNAVAILABLE",
    "PROVIDER_TIMEOUT",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_DNS_UNAVAILABLE",
}


def _key(row: dict[str, Any]) -> str:
    return f"{row['trading212_id']}:{row['isin']}"


def enrichment_order(
    ctx: QualificationContext,
    rows: list[dict[str, Any]],
    binding: str | None,
    *,
    replay: bool,
) -> list[int]:
    """Rotate only the network-work order, not membership or qualification.

    A persistent cursor advances only after actual network work. Thus repeated
    failures near the start cannot consume every run's request budget forever.
    Membership churn uses the next surviving identity, not an old array index.
    Offline replay neither consumes nor changes the live scheduling cursor.
    """
    candidates = sorted(
        (i for i, row in enumerate(rows) if row.get("provider_enrichment_input") is True),
        key=lambda index: _key(rows[index]),
    )
    if replay or not candidates:
        return candidates
    try:
        state = ctx.read_json(CURSOR)
    except (ValueError, OSError):
        # A corrupt optimization cannot confer admission or omit any stock.
        return candidates
    if (
        not isinstance(state, dict)
        or state.get("version") != SCHEDULER_VERSION
        or state.get("universe_policy_version") != UNIVERSE_POLICY_VERSION
        or state.get("credential_binding_sha256") != binding
        or not isinstance(state.get("resume_after"), str)
    ):
        return candidates
    start = bisect_right([_key(rows[i]) for i in candidates], state["resume_after"])
    return candidates[start:] + candidates[:start]


def record_network_progress(
    ctx: QualificationContext, row: dict[str, Any], binding: str | None
) -> None:
    """Persist no provider success or approval, only the last serviced identity."""
    ctx.write_json(
        CURSOR,
        {
            "version": SCHEDULER_VERSION,
            "universe_policy_version": UNIVERSE_POLICY_VERSION,
            "credential_binding_sha256": binding,
            "resume_after": _key(row),
            "updated_at": ctx.now.isoformat(),
        },
    )


def _records(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _code(value: Any) -> str | None:
    """Diagnostics are enum-like codes, never provider error bodies or URLs."""
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z_]{1,100}", value) else None


def _mapping_reason(row: dict[str, Any]) -> str | None:
    diagnostics = row.get("eodhd_mapping_diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    explicit = _code(diagnostics.get("unresolved_reason"))
    if explicit:
        return explicit
    # Interpret older genuine diagnostics without inventing a new provider observation.
    reasons = row.get("provider_reasons", [])
    if not isinstance(reasons, list):
        return None
    if "EODHD_MAPPING_AMBIGUOUS" in reasons:
        return "AMBIGUOUS_EXACT_IDENTITY"
    if "EODHD_MAPPING_NOT_FOUND" not in reasons:
        return None
    if diagnostics.get("response_count") == 0:
        return "EMPTY_SEARCH_RESPONSE"
    if diagnostics.get("exact_isin_count") == 0:
        return "ISIN_NOT_RETURNED"
    if diagnostics.get("exact_quote_count") == 0:
        return "GBX_LINE_NOT_RETURNED"
    return "STOCK_TYPE_SYMBOL_OR_CORROBORATION_REQUIRED"


def _example(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in ("trading212_id", "isin", "quote_currency", "eodhd_symbol")
        if isinstance(row.get(key), str) and len(row[key]) <= 100
    }


def _fresh(observed: Any, valid_until: Any, now: datetime | None) -> bool:
    if now is None or not isinstance(observed, str) or not isinstance(valid_until, str):
        return False
    try:
        start, end = datetime.fromisoformat(observed), datetime.fromisoformat(valid_until)
        return start <= now < min(end, start + timedelta(days=1))
    except (ValueError, TypeError):
        return False


def build_enrichment_progress(
    master: dict[str, Any], *, cursor: dict[str, Any] | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Describe actual saved/current work, not approvals or promised convergence.

    A failed refresh has no new authoritative counts. A saved replay retains its
    original observation time and cannot be presented as a fresh live retrieval.
    Counts describe this master view, not a cumulative provider request ledger.
    """
    available = master.get("status") in {"REFRESHED", "REPLAYED"} and isinstance(
        master.get("stocks"), list
    )
    rows = _records(master.get("stocks")) if available else []
    candidates = [row for row in rows if row.get("provider_enrichment_input") is True]
    mapping_counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    endpoint_counts: Counter[tuple[str, str, str, str | None, int | None]] = Counter()
    states: Counter[str] = Counter()
    access_blocked = False
    deferred_budget: set[int] = set()
    deferred_backoff: set[int] = set()
    deferred: set[int] = set()
    retryable: set[int] = set()
    permanent: set[int] = set()
    cache_hits = 0
    for index, row in enumerate(rows):
        if (
            row.get("provider_enrichment_input") is True
            and row.get("eodhd_mapping_attempted") is not True
        ):
            deferred.add(index)
        if state := _code(row.get("qualification_state")):
            states[state] += 1
        reason = _mapping_reason(row)
        if reason:
            mapping_counts[reason] += 1
            permanent.add(index)
            if len(examples[reason]) < 3:
                examples[reason].append(_example(row))
        for diagnostic in _records(row.get("provider_request_diagnostics")):
            provider, endpoint = diagnostic.get("provider"), diagnostic.get("endpoint")
            if provider not in {"eodhd", "companies-house"} or not isinstance(endpoint, str):
                continue
            if not re.fullmatch(r"[a-z-]{1,40}", endpoint):
                continue
            status = diagnostic.get("http_status")
            status = status if type(status) is int and 100 <= status <= 599 else None
            outcome = _code(diagnostic.get("outcome")) or "UNKNOWN"
            error = _code(diagnostic.get("error_code"))
            original = _code(diagnostic.get("original_error_code"))
            # Correctly explain genuine legacy HTTP 402 evidence whose old
            # sanitized code was PROVIDER_UNAVAILABLE. No entitlement is inferred.
            actual_error = (
                _ACCESS_ERRORS[status]
                if status is not None and status in _ACCESS_ERRORS
                else original or error
            )
            if status in _ACCESS_ERRORS or actual_error in _ACCESS_ERRORS.values():
                access_blocked = True
            if outcome == "CACHE_HIT":
                cache_hits += 1
            if outcome in {"NOT_REQUESTED", "BACKOFF"}:
                deferred.add(index)
            if outcome == "FAILED":
                if actual_error in _RETRYABLE_ERRORS:
                    retryable.add(index)
                if status in {401, 402, 403, 404}:
                    permanent.add(index)
            if error == "PROVIDER_REQUEST_BUDGET_EXHAUSTED":
                deferred_budget.add(index)
            if error == "PROVIDER_BACKOFF_ACTIVE":
                deferred_backoff.add(index)
            endpoint_counts[(provider, endpoint, outcome, actual_error, status)] += 1
            if actual_error and outcome in {"FAILED", "BACKOFF"}:
                example_key = f"{provider}:{endpoint}:{actual_error}:{outcome}"
                if len(examples[example_key]) < 3:
                    examples[example_key].append(_example(row))
    attempted = sum(row.get("eodhd_mapping_attempted") is True for row in candidates)
    source_current = available and _fresh(master.get("observed_at"), master.get("valid_until"), now)
    serviced_current = (
        sum(
            row.get("eodhd_mapping_attempted") is True
            and _fresh(
                row.get("provider_evidence_observed_at"),
                row.get("provider_evidence_valid_until"),
                now,
            )
            for row in candidates
        )
        if source_current
        else 0
    )
    mapped_current = (
        sum(
            row.get("eodhd_mapping_state") == "MAPPED"
            and _fresh(
                row.get("provider_evidence_observed_at"),
                row.get("provider_evidence_valid_until"),
                now,
            )
            for row in candidates
        )
        if source_current
        else 0
    )
    budget = master.get("provider_request_budget")
    budget = budget if type(budget) is int and budget > 0 else None
    unattempted = len(candidates) - attempted
    network_requests = sum(
        row["provider_network_requests_used"]
        for row in rows
        if type(row.get("provider_network_requests_used")) is int
        and row["provider_network_requests_used"] >= 0
    )
    counts = {
        "universe_rows": len(rows),
        "gbp_gbx_stocks": sum(row.get("universe_member") is True for row in rows),
        "gbx_stocks": sum(row.get("universe_member") is True and row.get("quote_currency") == "GBX" for row in rows),
        "gbp_stocks": sum(row.get("universe_member") is True and row.get("quote_currency") == "GBP" for row in rows),
        "identity_valid": len(candidates),
        "identity_unresolved": sum(
            row.get("universe_member") is True and row.get("identity_valid") is not True
            for row in rows
        ),
        "eodhd_attempted": attempted,
        "eodhd_mapped": sum(row.get("eodhd_mapping_state") == "MAPPED" for row in candidates),
        "eodhd_unattempted": len(candidates) - attempted,
        "eodhd_cached_attempts": sum(
            row.get("eodhd_lookup_origin") == "CACHE" for row in candidates
        ),
        "eodhd_network_attempts": sum(
            row.get("eodhd_lookup_origin") == "NETWORK" for row in candidates
        ),
        "companies_house_mapped": sum(row.get("companies_house_state") == "MAPPED" for row in rows),
        "companies_house_not_applicable": sum(
            row.get("companies_house_state") == "NOT_APPLICABLE" for row in rows
        ),
        "companies_house_applicability_unresolved": sum(
            row.get("companies_house_state") == "UNRESOLVED_APPLICABILITY" for row in candidates
        ),
        "deferred_by_request_budget": len(deferred_budget),
        "deferred_by_backoff": len(deferred_backoff),
        "qualified": states.get("QUALIFIED", 0),
        "unresolved": sum(
            count for state, count in states.items() if state.startswith("UNRESOLVED_")
        ),
    }
    result: dict[str, Any] = {
        "schema_version": "money-universe-enrichment-progress-v1",
        "universe_policy_version": master.get("universe_policy_version"),
        "scope": "ENRICHMENT_PROGRESS_ONLY",
        "source_status": master.get("status"),
        "source_scope": master.get("scope"),
        "source_observed_at": master.get("observed_at"),
        "source_valid_until": master.get("valid_until"),
        "source_counts_available": available,
        "assessed_at": now.isoformat() if now is not None else None,
        "source_evidence_current": source_current if now is not None else None,
        "count_semantics": "CURRENT_MASTER_VIEW_NOT_CUMULATIVE_REQUEST_LOG",
        "total_provider_inputs": len(candidates) if available else None,
        "serviced_current": serviced_current if available and now is not None else None,
        "mapped_current": mapped_current if available and now is not None else None,
        "remaining_unserviced": len(candidates) - serviced_current
        if available and now is not None
        else None,
        "deferred": len(deferred) if available else None,
        "retryable_failures": len(retryable) if available else None,
        "permanent_failures": len(permanent) if available else None,
        "failure_semantics": (
            "Distinct instruments per category in the saved run; categories may overlap. "
            "Permanent means no automatic retry without access/identity change, not permanent exclusion."
        ),
        "cache_hits": cache_hits if available else None,
        "cache_hit_semantics": "HTTP cache observations in source run, including repeated cache-only normalization",
        "network_requests_this_run": network_requests if available else None,
        "network_request_semantics": "Actual requests recorded by the source master run, not this diagnostic write",
        "configured_request_budget": budget,
        "counts": counts if available else None,
        "qualification_states": dict(sorted(states.items())),
        "mapping_failure_counts": dict(sorted(mapping_counts.items())),
        "provider_endpoint_outcomes": [
            {
                "provider": provider,
                "endpoint": endpoint,
                "outcome": outcome,
                "error_code": error,
                "http_status": status,
                "diagnostic_count": count,
            }
            for (provider, endpoint, outcome, error, status), count in sorted(
                endpoint_counts.items(), key=lambda item: tuple(str(value) for value in item[0])
            )
        ],
        "failure_examples": dict(sorted(examples.items())),
        "blocked_by_access": access_blocked,
        "estimated_additional_runs": None,
        "estimated_additional_bounded_runs": None,
        "estimate_status": (
            "SOURCE_COUNTS_UNAVAILABLE"
            if not available
            else "PROVIDER_ACCESS_REVIEW_REQUIRED"
            if access_blocked
            else "REQUESTS_PER_INSTRUMENT_AND_REVIEW_OUTCOMES_UNKNOWN"
        ),
        "minimum_requests_for_unattempted_identity_lookups": (unattempted if available else None),
        "minimum_additional_bounded_runs_for_unattempted_identity_lookups": (
            (unattempted + budget - 1) // budget if available and budget is not None else None
        ),
        "minimum_requests_note": (
            "Lower bound for initial EODHD identity lookups only; excludes retry, data, "
            "Companies House, review and qualification work. Not a success estimate."
        ),
        "production_qualified": False,
    }
    result["scheduler"] = (
        {
            key: cursor.get(key)
            for key in ("version", "universe_policy_version", "resume_after", "updated_at")
        }
        if isinstance(cursor, dict)
        else None
    )
    return result


def write_enrichment_progress(ctx: QualificationContext, master: dict[str, Any]) -> dict[str, Any]:
    """Write only a derived diagnostic; never modify master, raw cache or reviews."""
    try:
        cursor = ctx.read_json(CURSOR)
    except (OSError, ValueError):
        cursor = None
    result = build_enrichment_progress(master, cursor=cursor, now=ctx.now)
    result["generated_at"] = ctx.now.isoformat()
    ctx.write_json(PROGRESS, result)
    return result
