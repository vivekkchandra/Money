"""Normalize current live STOCK/GBP/GBX membership without granting qualification.

Trading 212's accessible-instrument response alone establishes initial market
membership. Exchange, country, and MIC are optional enrichment, never membership
gates. The caller validates any optional ``venue_reviews`` and their source
bytes. Neither membership nor an ISIN prefix establishes incorporation,
purchase availability, provider identity, or ethical clearance.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any


def metadata_row_hash(row: Any) -> str:
    """Hash canonical provider row bytes, not a guessed identifier."""
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _text(value: Any, maximum: int = 200) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        return None
    if any(ord(character) < 32 for character in value):
        return None
    return value.strip()


def _identifier(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return str(value)
    text = _text(value, 100)
    return text if text and re.fullmatch(r"[A-Za-z0-9_.-]+", text) else None


def _isin(value: Any) -> str | None:
    text = _text(value, 12)
    if text is None or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", text):
        return None
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in text)
    total = sum(
        (int(c) * (2 if i % 2 else 1)) // 10 + (int(c) * (2 if i % 2 else 1)) % 10
        for i, c in enumerate(reversed(digits))
    )
    return text if total % 10 == 0 else None


def _country(value: Any) -> str | None:
    text = _text(value, 100)
    if text is None:
        return None
    if text.upper() in {"GB", "UK", "UNITED KINGDOM", "GREAT BRITAIN"}:
        return "GB"
    return text.upper() if re.fullmatch(r"[A-Za-z]{2}", text) else None


def _mic(value: Any) -> str | None:
    text = _text(value, 4)
    return text if text and re.fullmatch(r"[A-Z0-9]{4}", text) else None


def _datetime(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        return result if isinstance(result, datetime) and result.utcoffset() is not None else None
    except ValueError:
        return None


def _venue(
    exchange: Mapping[str, Any],
    reviews: Sequence[Mapping[str, Any]],
    observed_at: datetime,
) -> tuple[str | None, str | None, str | None]:
    country = _country(exchange.get("countryCode", exchange.get("country")))
    mic = _mic(exchange.get("mic", exchange.get("MIC")))
    matching = []
    row_hash = metadata_row_hash(exchange)
    for review in reviews:
        reviewed_at = _datetime(review.get("reviewed_at"))
        valid_until = _datetime(review.get("valid_until"))
        if (
            _identifier(review.get("exchange_id")) == _identifier(exchange.get("id"))
            and review.get("exchange_name") == exchange.get("name")
            and review.get("exchange_row_sha256") == row_hash
            and re.fullmatch(r"[0-9a-f]{64}", str(review.get("source_evidence_hash", "")))
            and reviewed_at is not None
            and valid_until is not None
            and reviewed_at <= observed_at < valid_until
        ):
            matching.append(review)
    if len(matching) > 1:
        return None, None, "AMBIGUOUS_VENUE_REVIEW"
    if matching:
        reviewed_country = _country(matching[0].get("country"))
        reviewed_mic = _mic(matching[0].get("mic"))
        if (country and reviewed_country != country) or (mic and reviewed_mic != mic):
            return None, None, "VENUE_REVIEW_CONFLICT"
        country, mic = reviewed_country, reviewed_mic
    # Missing enrichment is not a failed verification or a review requirement.
    # Retain the nullable facts; only actual conflicts need diagnostic reasons.
    return country, mic, None


def _enrich_venue(
    row: dict[str, Any],
    source: Mapping[str, Any],
    by_id: Mapping[str, list[dict[str, Any]]],
    by_schedule: Mapping[str, list[dict[str, Any]]],
    reviews: Sequence[Mapping[str, Any]],
    observed_at: datetime,
) -> None:
    """Record optional venue facts and uncertainty separately from identity."""
    reasons = row["venue_reasons"]
    schedule_id = row["working_schedule_id"]
    candidates = by_schedule.get(schedule_id, []) if schedule_id is not None else []
    direct_id = _identifier(source.get("exchangeId"))
    if "exchangeId" in source and direct_id is None:
        reasons.append("EXCHANGE_IDENTIFIER_INVALID")
        return
    if direct_id is not None:
        row["exchange_id"] = direct_id
        direct = by_id.get(direct_id, [])
        if len(direct) != 1 or (candidates and direct != candidates):
            reasons.append("EXCHANGE_IDENTIFIER_CONFLICT")
            return
        candidates = direct
    if len(candidates) != 1:
        reasons.append(
            "EXCHANGE_MAPPING_MISSING" if not candidates else "EXCHANGE_MAPPING_AMBIGUOUS"
        )
        return
    exchange = candidates[0]
    row["exchange_id"] = _identifier(exchange.get("id"))
    row["exchange_name"] = _text(exchange.get("name"))
    row["exchange_row_sha256"] = metadata_row_hash(exchange)
    if row["exchange_name"] is None or len(by_id[str(row["exchange_id"])]) != 1:
        reasons.append("EXCHANGE_IDENTITY_INVALID")
        return
    country, mic, venue_problem = _venue(exchange, reviews, observed_at)
    row["country"], row["mic"] = country, mic
    row["uk_venue"] = country == "GB"
    row["venue_status"] = "PARTIAL" if venue_problem or not country or not mic else "RESOLVED"
    if venue_problem:
        reasons.append(venue_problem)


def normalize_universe(
    instruments: Sequence[Any],
    exchanges: Sequence[Any],
    *,
    observed_at: datetime,
    venue_reviews: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Normalize every row deterministically; quarantine uncertainty individually.

    ``universe_member`` records the exact live type/currency market filter even
    when a subsequent identity check fails. ``identity_valid`` is independently
    established by the broker identifier and non-conflicting optional identity;
    provider enrichment may run before final qualification. Valid candidates remain
    ``UNRESOLVED_PROVIDER_MAPPING`` until providers, ethics, and freshness
    gates are verified. Venue facts are informational.
    Never strips or invents ticker suffixes and never filters by ISIN prefix.
    """
    if observed_at.utcoffset() is None:
        raise ValueError("UNIVERSE_TIMESTAMP_INVALID")
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_schedule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in exchanges:
        if not isinstance(item, dict):
            continue
        exchange_id = _identifier(item.get("id"))
        if exchange_id is None:
            continue
        by_id[exchange_id].append(item)
        schedules = item.get("workingSchedules")
        if not isinstance(schedules, list):
            continue
        for schedule in schedules:
            schedule_id = _identifier(schedule.get("id")) if isinstance(schedule, dict) else None
            if schedule_id is not None:
                by_schedule[schedule_id].append(item)

    rows: list[dict[str, Any]] = []
    broker_keys: dict[str, list[dict[str, Any]]] = defaultdict(list)
    economic_keys: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, raw in enumerate(instruments):
        source = raw if isinstance(raw, dict) else {}
        broker_id = _identifier(source.get("ticker"))
        currency = _text(source.get("currencyCode"), 3)
        instrument_type = _text(source.get("type"), 50)
        isin = _isin(source.get("isin"))
        schedule_id = _identifier(source.get("workingScheduleId"))
        reasons: list[str] = []
        row: dict[str, Any] = {
            "source_index": index,
            "trading212_id": broker_id,
            "t212_ticker": broker_id,
            "short_ticker": _text(source.get("shortName"), 32),
            "name": _text(source.get("name")),
            "isin": isin,
            "quote_currency": currency,
            "instrument_type": instrument_type,
            "universe_member": source.get("type") == "STOCK"
            and source.get("currencyCode") in ("GBP", "GBX"),
            "identity_valid": False,
            "exchange_id": None,
            "exchange_name": None,
            "mic": None,
            "country": None,
            "working_schedule_id": schedule_id,
            "added_on": source.get("addedOn"),
            "max_open_quantity": source.get("maxOpenQuantity"),
            "extended_hours": source.get("extendedHours"),
            "observed_at": observed_at.isoformat(),
            "valid_until": (observed_at + timedelta(hours=24)).isoformat(),
            "broker_valid_until": (observed_at + timedelta(hours=24)).isoformat(),
            "instrument_row_sha256": metadata_row_hash(raw),
            "exchange_row_sha256": None,
            "companies_house_number": None,
            "eodhd_symbol": None,
            "uk_venue": False,
            "venue_status": "UNRESOLVED",
            "venue_reasons": [],
            "ethical_state": "NOT_YET_SCREENED",
            "evidence_freshness": "FRESH_MEMBERSHIP_ONLY",
            "qualification_state": "UNRESOLVED_IDENTITY",
            "research_state": "DISCOVERED",
            "reasons": reasons,
        }
        rows.append(row)
        if not isinstance(raw, dict):
            reasons.append("MALFORMED_INSTRUMENT_ROW")
            continue
        _enrich_venue(row, source, by_id, by_schedule, venue_reviews, observed_at)
        if broker_id is not None:
            broker_keys[broker_id].append(row)
        if instrument_type is None:
            reasons.append("INSTRUMENT_TYPE_MISSING")
            continue
        if source.get("type") != "STOCK":
            row["qualification_state"] = "EXCLUDED_INSTRUMENT_TYPE"
            reasons.append("NOT_INDIVIDUAL_STOCK_TYPE")
            continue
        if source.get("currencyCode") not in ("GBP", "GBX"):
            row["qualification_state"] = "EXCLUDED_NON_GBP_GBX"
            reasons.append("QUOTE_CURRENCY_NOT_GBP_GBX")
            continue
        # Absent enrichment is not corrupt identity. A supplied malformed ISIN,
        # however, must not be silently discarded to evade an identity conflict.
        if broker_id is None or (source.get("isin") not in (None, "") and isin is None):
            reasons.append("INSTRUMENT_IDENTIFIER_INVALID")
            continue
        # A different venue does not by itself turn
        # the same ISIN into a separate economic security. Quarantine aliases;
        # never select a preferred line using provider order or ticker heuristics.
        if isin is not None:
            economic_keys[isin].append(row)
        row["identity_valid"] = True
        row["qualification_state"] = "UNRESOLVED_PROVIDER_MAPPING"

    for groups, reason in (
        (broker_keys.values(), "DUPLICATE_BROKER_ID"),
        (economic_keys.values(), "DUPLICATE_ECONOMIC_SHARE_LINE"),
    ):
        for duplicates in groups:
            if len(duplicates) > 1:
                for row in duplicates:
                    row["identity_valid"] = False
                    if row["universe_member"]:
                        row["qualification_state"] = "UNRESOLVED_IDENTITY"
                    if reason not in row["reasons"]:
                        row["reasons"].append(reason)
    for row in rows:
        row["basic_identity_valid"] = row["identity_valid"]
        row["basic_identity_reasons"] = list(row["reasons"])
    return rows
