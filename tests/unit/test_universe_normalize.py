"""Broker catalogue normalization never grants ISA or ethical approval."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from money.data.security import FetchResult
from money.data.uk.live import Trading212MetadataProvider
from money.qualification.universe_normalize import metadata_row_hash, normalize_universe

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)


def instrument(**changes: Any) -> dict[str, Any]:
    return {
        "ticker": "TESTl_EQ",
        "shortName": "TEST",
        "name": "Example ordinary shares",
        "type": "STOCK",
        "currencyCode": "GBX",
        "isin": "US0378331005",
        "workingScheduleId": 11,
        "addedOn": "2020-01-01T00:00:00Z",
        "maxOpenQuantity": 1000,
        **changes,
    }


def exchange(**changes: Any) -> dict[str, Any]:
    return {
        "id": 7,
        "name": "A genuine UK venue",
        "countryCode": "GB",
        "mic": "XABC",
        "workingSchedules": [{"id": 11}],
        **changes,
    }


def normalized(*rows: Any, exchanges: list[Any] | None = None) -> list[dict[str, Any]]:
    return normalize_universe(
        rows, exchanges if exchanges is not None else [exchange()], observed_at=NOW
    )


def test_gbx_stock_is_normalized_without_issuer_country_filter() -> None:
    row = normalized(instrument())[0]
    assert row["isin"] == "US0378331005"
    assert row["quote_currency"] == "GBX"
    assert row["universe_member"] is True
    assert row["identity_valid"] is True
    assert row["uk_venue"] is True
    assert row["mic"] == "XABC"  # No hard-coded XLON gate.
    assert row["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert row["ethical_state"] == "ETHICAL_REVIEW_REQUIRED"
    assert row["valid_until"] == (NOW + timedelta(hours=24)).isoformat()
    assert row["short_ticker"] == "TEST"
    assert row["max_open_quantity"] == 1000
    assert row["venue_status"] == "RESOLVED"


def test_non_uk_venue_does_not_exclude_gbx_stock() -> None:
    row = normalized(instrument(isin="GB00BH4HKS39"), exchanges=[exchange(countryCode="US")])[0]
    assert row["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert row["universe_member"] is True
    assert row["identity_valid"] is True
    assert row["country"] == "US"
    assert row["uk_venue"] is False
    assert row["venue_status"] == "RESOLVED"


@pytest.mark.parametrize("kind", ["ETF", "ETC", "ETN", "FUND", "BOND", "WARRANT", "RIGHT", "CFD"])
def test_non_stock_types_are_excluded(kind: str) -> None:
    row = normalized(instrument(type=kind))[0]
    assert row["qualification_state"] == "EXCLUDED_INSTRUMENT_TYPE"
    assert row["universe_member"] is False
    assert row["identity_valid"] is False


def test_names_do_not_replace_provider_classification() -> None:
    row = normalized(instrument(name="Military Oil Investment Fund Ordinary Shares"))[0]
    assert row["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert row["ethical_state"] == "ETHICAL_REVIEW_REQUIRED"


@pytest.mark.parametrize("currency", ["GBP", "USD", "EUR", "GBx", " GBX ", None])
def test_every_non_gbx_quote_is_excluded(currency: str | None) -> None:
    row = normalized(instrument(currencyCode=currency))[0]
    assert row["qualification_state"] == "EXCLUDED_NON_GBX"
    assert row["universe_member"] is False
    assert row["identity_valid"] is False


def test_missing_country_is_only_informational_even_if_exchange_is_named_london() -> None:
    row = normalized(
        instrument(),
        exchanges=[exchange(countryCode=None, name="London Stock Exchange", mic="XLON")],
    )[0]
    assert row["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert row["universe_member"] is True
    assert row["identity_valid"] is True
    assert row["venue_status"] == "PARTIAL"
    assert row["country"] is None
    assert row["venue_reasons"] == []


def test_missing_mic_is_not_invented() -> None:
    row = normalized(instrument(), exchanges=[exchange(mic=None)])[0]
    assert row["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert row["universe_member"] is True
    assert row["venue_status"] == "PARTIAL"
    assert row["mic"] is None
    assert row["identity_valid"] is True
    assert row["venue_reasons"] == []


@pytest.mark.parametrize(
    "venues", [[], [None], [exchange(workingSchedules=[])], [exchange(id=None)]]
)
def test_gbx_stock_without_resolvable_exchange_metadata_enters_universe(venues: list[Any]) -> None:
    row = normalized(instrument(), exchanges=venues)[0]
    assert row["universe_member"] is True
    assert row["identity_valid"] is True
    assert row["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert row["venue_status"] == "UNRESOLVED"
    assert row["mic"] is None
    assert row["reasons"] == ["ACCOUNT_ISA_SCOPE_REVIEW_REQUIRED"]
    assert row["venue_reasons"] == ["EXCHANGE_MAPPING_MISSING"]


def test_all_venue_metadata_is_optional_for_initial_membership() -> None:
    raw = instrument()
    del raw["workingScheduleId"]
    row = normalized(raw, exchanges=[])[0]
    assert row["universe_member"] is True
    assert row["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert row["instrument_row_sha256"] == metadata_row_hash(raw)
    assert row["exchange_row_sha256"] is None
    assert row["identity_valid"] is True


@pytest.mark.parametrize(
    "venues",
    [[], [exchange(countryCode=None)], [exchange(mic=None)], [exchange(countryCode=None, mic=None)]],
)
def test_missing_venue_facts_never_emit_obsolete_review_or_qualification_reasons(
    venues: list[Any],
) -> None:
    row = normalized(instrument(), exchanges=venues)[0]
    assert row["identity_valid"] is True
    assert row["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    reasons = set(row["reasons"] + row["venue_reasons"])
    assert reasons.isdisjoint(
        {
            "VENUE_COUNTRY_NOT_VERIFIED",
            "VENUE_MIC_NOT_VERIFIED",
            "EXCLUDED_NOT_UK_VENUE",
            "UK_VENUE_REQUIRED",
            "VENUE_REVIEW_REQUIRED",
        }
    )


def test_preverified_venue_review_joins_exact_response_identity() -> None:
    venue = exchange(countryCode=None, mic=None)
    review = {
        "exchange_id": 7,
        "exchange_name": venue["name"],
        "exchange_row_sha256": metadata_row_hash(venue),
        "country": "GB",
        "mic": "XABC",
        "reviewed_at": (NOW - timedelta(hours=1)).isoformat(),
        "valid_until": (NOW + timedelta(days=30)).isoformat(),
        "source_evidence_hash": metadata_row_hash({"genuine_in_tests_only": "venue reference"}),
    }
    result = normalize_universe([instrument()], [venue], observed_at=NOW, venue_reviews=[review])[0]
    assert result["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert result["venue_status"] == "RESOLVED"
    changed = {**review, "exchange_name": "Wrong venue"}
    result = normalize_universe([instrument()], [venue], observed_at=NOW, venue_reviews=[changed])[
        0
    ]
    assert result["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert result["venue_status"] == "PARTIAL"
    stale = {**review, "valid_until": NOW.isoformat()}
    result = normalize_universe([instrument()], [venue], observed_at=NOW, venue_reviews=[stale])[0]
    assert result["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert result["venue_status"] == "PARTIAL"


def test_conflicting_or_duplicate_venue_reviews_do_not_approve_venue_or_block_membership() -> None:
    venue = exchange()
    review = {
        "exchange_id": 7,
        "exchange_name": venue["name"],
        "exchange_row_sha256": metadata_row_hash(venue),
        "country": "US",
        "mic": "XABC",
        "reviewed_at": NOW.isoformat(),
        "valid_until": (NOW + timedelta(days=1)).isoformat(),
        "source_evidence_hash": metadata_row_hash({"source": "test"}),
    }
    result = normalize_universe([instrument()], [venue], observed_at=NOW, venue_reviews=[review])[0]
    assert "VENUE_REVIEW_CONFLICT" in result["venue_reasons"]
    assert result["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert result["country"] is None
    result = normalize_universe(
        [instrument()], [venue], observed_at=NOW, venue_reviews=[review, review]
    )[0]
    assert "AMBIGUOUS_VENUE_REVIEW" in result["venue_reasons"]
    assert result["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert result["mic"] is None


@pytest.mark.parametrize(
    "changes", [{"isin": "GB00B63QSB30"}, {"isin": None}, {"ticker": None}, {"name": ""}]
)
def test_one_invalid_instrument_does_not_block_valid_rows(changes: dict[str, Any]) -> None:
    rows = normalized(instrument(ticker="GOODl_EQ"), instrument(**changes), None, "malformed")
    assert len(rows) == 4
    assert rows[0]["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert rows[0]["identity_valid"] is True
    assert all(row["qualification_state"] == "UNRESOLVED_IDENTITY" for row in rows[1:])
    assert all(row["identity_valid"] is False for row in rows[1:])
    assert rows[1]["universe_member"] is True  # Membership is not identity qualification.
    assert rows[2]["universe_member"] is False


@pytest.mark.parametrize("changes", [{}, {"ticker": "SEPARATEl_EQ"}])
def test_duplicate_economic_identity_is_quarantined(changes: dict[str, Any]) -> None:
    rows = normalized(instrument(), instrument(**changes))
    assert all(row["qualification_state"] == "UNRESOLVED_IDENTITY" for row in rows)
    assert all("DUPLICATE_ECONOMIC_SHARE_LINE" in row["reasons"] for row in rows)
    assert all(row["universe_member"] for row in rows)
    assert all(row["identity_valid"] is False for row in rows)


def test_same_broker_id_conflicting_type_is_not_admitted() -> None:
    rows = normalized(instrument(), instrument(type="ETF"))
    assert all("DUPLICATE_BROKER_ID" in row["reasons"] for row in rows)
    assert rows[0]["qualification_state"] == "UNRESOLVED_IDENTITY"
    assert rows[1]["qualification_state"] == "EXCLUDED_INSTRUMENT_TYPE"
    assert all(row["identity_valid"] is False for row in rows)


def test_different_securities_remain_distinct() -> None:
    rows = normalized(instrument(), instrument(ticker="OTHERl_EQ", isin="GB00BH4HKS39"))
    assert all(row["qualification_state"] == "UNRESOLVED_ISA_SCOPE" for row in rows)
    assert all(row["identity_valid"] is True for row in rows)


def test_same_isin_on_other_venue_is_not_a_new_economic_security() -> None:
    rows = normalized(
        instrument(),
        instrument(ticker="OTHERl_EQ", workingScheduleId=22),
        exchanges=[exchange(), exchange(id=8, mic="XDEF", workingSchedules=[{"id": 22}])],
    )
    assert all(row["qualification_state"] == "UNRESOLVED_IDENTITY" for row in rows)
    assert all("DUPLICATE_ECONOMIC_SHARE_LINE" in row["reasons"] for row in rows)
    assert all(row["identity_valid"] is False for row in rows)


def test_ambiguous_working_schedule_and_exchange_id_conflict_are_enrichment_only() -> None:
    rows = normalized(instrument(), exchanges=[exchange(), exchange(id=8)])
    assert "EXCHANGE_MAPPING_AMBIGUOUS" in rows[0]["venue_reasons"]
    assert rows[0]["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    rows = normalized(instrument(exchangeId=8))
    assert "EXCHANGE_IDENTIFIER_CONFLICT" in rows[0]["venue_reasons"]
    assert rows[0]["qualification_state"] == "UNRESOLVED_ISA_SCOPE"


def test_direct_exchange_identifier_requires_real_exchange_record() -> None:
    row = normalized(instrument(exchangeId=7, workingScheduleId=None))[0]
    assert row["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    row = normalized(instrument(exchangeId="bad id"))[0]
    assert "EXCHANGE_IDENTIFIER_INVALID" in row["venue_reasons"]
    assert row["qualification_state"] == "UNRESOLVED_ISA_SCOPE"


def test_venue_changes_do_not_grant_downstream_approvals() -> None:
    for venues in ([], [exchange()], [exchange(countryCode="US")]):
        row = normalized(instrument(), exchanges=venues)[0]
        assert row["isa_provenance_state"] == "UNRESOLVED_ISA_SCOPE"
        assert row["ethical_state"] == "ETHICAL_REVIEW_REQUIRED"
        assert row["eodhd_symbol"] is None
        assert row["companies_house_number"] is None
        assert row["qualification_state"] != "QUALIFIED"


def test_naive_observation_timestamp_rejected() -> None:
    with pytest.raises(ValueError, match="UNIVERSE_TIMESTAMP_INVALID"):
        normalize_universe([], [], observed_at=NOW.replace(tzinfo=None))


class MetadataFetcher:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> FetchResult:
        assert kwargs["headers"]["Authorization"].startswith("Basic ")
        self.calls.append(url)
        return FetchResult(self.raw, "application/json")


def test_metadata_responses_preserve_exact_bytes_and_only_metadata_urls() -> None:
    raw = b' [ {"id": 7, "name": "UK venue"} ]\n'
    fetcher = MetadataFetcher(raw)
    provider = Trading212MetadataProvider("test-key", "test-secret", fetcher)  # type: ignore[arg-type]
    returned, rows = provider.metadata_response("exchanges")
    assert returned == raw
    assert rows == ({"id": 7, "name": "UK venue"},)
    provider.metadata_response("instruments")
    assert fetcher.calls == [
        "https://live.trading212.com/api/v0/equity/metadata/exchanges",
        "https://live.trading212.com/api/v0/equity/metadata/instruments",
    ]
    assert b"test-key" not in returned and b"test-secret" not in returned
    for denied in ("orders", "account/cash", "portfolio", "history/orders", "../orders"):
        with pytest.raises(ValueError, match="TRADING212_METADATA_RESOURCE_DENIED"):
            provider.metadata_response(denied)  # type: ignore[arg-type]
    assert len(fetcher.calls) == 2


@pytest.mark.parametrize("raw", [b"{}", b'[{"id":1,"id":2}]', b"not json", b'[{"id":NaN}]'])
def test_invalid_metadata_response_fails_closed(raw: bytes) -> None:
    provider = Trading212MetadataProvider("test-key", "test-secret", MetadataFetcher(raw))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ELIGIBILITY_METADATA_INVALID"):
        provider.metadata_response("instruments")
