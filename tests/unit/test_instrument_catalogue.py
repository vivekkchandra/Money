"""Synthetic catalogue contracts, not live instrument qualification evidence."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from money.data.identifiers import InstrumentIdentifiers
from money.data.instruments import InstrumentCatalogue, InstrumentSearchQuery, ReviewedInstrument
from money.data.qualification import ProviderQualification
from money.schemas.contracts import InstrumentMetadata, ResearchMandate

NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)


def fixture_entry(now=NOW, **updates):
    metadata = InstrumentMetadata(
        ticker="FIXTURE.L",
        company="Café Research Fixture",
        instrument_type="STOCK",
        quote_currency="GBX",
        isa_available=True,
        currently_available=True,
        business_activities=("telecommunications",),
        activities_verified=True,
        verified_at=now - timedelta(hours=1),
        source="Synthetic test review",
        provider="fixture-review",
        source_id="fixture-instrument",
    ).model_copy(update=updates)
    identifiers = InstrumentIdentifiers(
        ticker=metadata.ticker,
        company_name=metadata.company,
        trading212_id="FIXTUREl_EQ",
        exchange_ticker="FIXTURE",
        exchange="XLON",
        isin="GB00BH4HKS39",
        quote_currency="GBX",
        verified_at=now - timedelta(hours=1),
        valid_until=now + timedelta(hours=1),
        source="Synthetic test identity",
    )
    return ReviewedInstrument(metadata, identifiers)


def fixture_qualification(now=NOW):
    return ProviderQualification(
        provider="fixture-review",
        datasets=("instrument",),
        earliest_observation=now - timedelta(days=1),
        publication_times="ORIGINAL_PUBLICATION_VERIFIED",
        maximum_age_seconds=3600,
        production_qualified=True,
        qualified_by="Synthetic test reviewer",
        qualification_report_hash="a" * 64,
        verified_at=now - timedelta(days=1),
        valid_until=now + timedelta(days=1),
        attribution="Test only",
        source_documentation="https://example.test/qualification",
    )


def fixture_catalogue(now=NOW, **updates):
    return InstrumentCatalogue(
        (fixture_entry(now, **updates),), "live", (fixture_qualification(now),)
    )


@pytest.mark.parametrize("query", ["fixture.l", "CAFÉ", "fixturel_eq", "GB00BH4HKS39"])
def test_company_ticker_broker_and_isin_search_are_casefolded(query):
    page = fixture_catalogue().search(InstrumentSearchQuery(query=query), NOW)
    assert page.total == 1 and page.mode == "live"
    assert page.coverage == "reviewed_catalogue"
    result = page.instruments[0]
    assert result.instrument_id == result.ticker == "FIXTURE.L"
    assert result.eligibility == "VERIFIED_ELIGIBLE" and result.research_allowed
    assert result.exchange == "XLON" and not result.synthetic
    assert result.verified_at == NOW - timedelta(hours=1)


@pytest.mark.parametrize(
    "updates,eligibility",
    [
        ({"isa_available": None}, "UNKNOWN"),
        ({"isa_available": False}, "VERIFIED_INELIGIBLE"),
        ({"currently_available": None}, "UNKNOWN"),
        ({"currently_available": False}, "VERIFIED_INELIGIBLE"),
        ({"verified_at": NOW - timedelta(hours=24, seconds=1)}, "UNKNOWN"),
        ({"verified_at": NOW + timedelta(seconds=1)}, "UNKNOWN"),
        ({"business_activities": ("defence",)}, "VERIFIED_ELIGIBLE"),
        ({"activities_verified": False}, "VERIFIED_ELIGIBLE"),
        ({"business_activities": ()}, "VERIFIED_ELIGIBLE"),
        ({"instrument_type": "ETF"}, "VERIFIED_ELIGIBLE"),
        ({"quote_currency": "USD"}, "UNKNOWN"),
    ],
)
def test_display_never_turns_stale_unknown_or_excluded_into_research_permission(
    updates, eligibility
):
    catalogue = fixture_catalogue(**updates)
    result = catalogue.search(InstrumentSearchQuery(query="fixture"), NOW).instruments[0]
    assert result.eligibility == eligibility
    assert not result.research_allowed
    assert catalogue.admission_failures("FIXTURE.L", ResearchMandate(), NOW)


@pytest.mark.parametrize(
    "field,value", [("verified_at", NOW + timedelta(seconds=1)), ("valid_until", NOW)]
)
def test_identifier_freshness_is_checked_independently(field, value):
    entry = fixture_entry()
    entry = replace(entry, identifiers=entry.identifiers.model_copy(update={field: value}))
    catalogue = InstrumentCatalogue((entry,), "live")
    result = catalogue.search(InstrumentSearchQuery(query="fixture"), NOW).instruments[0]
    assert result.eligibility == "UNKNOWN" and not result.research_allowed


def test_custom_mandate_checked_again_before_admission():
    assert fixture_catalogue().admission_failures(
        "FIXTURE.L", ResearchMandate(quote_currencies=("GBP",)), NOW
    ) == ("CURRENCY_EXCLUDED",)
    assert fixture_catalogue().admission_failures("MISSING.L", ResearchMandate(), NOW) == (
        "ISA_ELIGIBILITY_UNKNOWN",
    )


def test_qualified_provider_expiry_is_checked_on_each_read():
    catalogue = fixture_catalogue()
    with pytest.raises(ValueError, match="PROVIDER_COVERAGE_MISSING"):
        catalogue.search(InstrumentSearchQuery(query="fixture"), NOW + timedelta(days=2))
    with pytest.raises(ValueError, match="PROVIDER_COVERAGE_MISSING"):
        catalogue.admission_failures("FIXTURE.L", ResearchMandate(), NOW + timedelta(days=2))


def test_pagination_is_bounded_and_stably_sorted_with_exact_ticker_first():
    original = fixture_entry()
    entries = tuple(
        replace(
            original,
            metadata=original.metadata.model_copy(update={"ticker": f"FIXTURE{index}.L"}),
            identifiers=original.identifiers.model_copy(
                update={
                    "ticker": f"FIXTURE{index}.L",
                    "trading212_id": f"ID{index}",
                    "exchange_ticker": f"F{index}",
                }
            ),
        )
        for index in range(15)
    )
    catalogue = InstrumentCatalogue(entries[::-1], "live")
    page = catalogue.search(InstrumentSearchQuery(query="fixture", limit=5, offset=5), NOW)
    assert page.total == 15 and page.limit == 5 and page.offset == 5
    assert [item.ticker for item in page.instruments] == sorted(
        item.metadata.ticker for item in entries
    )[5:10]
    assert catalogue.search(InstrumentSearchQuery(query="nonsense"), NOW).total == 0


@pytest.mark.parametrize(
    "values",
    [
        {"query": ""},
        {"query": "  "},
        {"query": "a" * 81},
        {"query": "a\n"},
        {"query": "a", "limit": 21},
        {"query": "a", "limit": 0},
        {"query": "a", "offset": -1},
        {"query": "a", "offset": 1001},
        {"query": "a", "untrusted": "unexpected"},
    ],
)
def test_query_contract_rejects_oversized_malformed_and_unknown_fields(values):
    with pytest.raises(ValidationError):
        InstrumentSearchQuery.model_validate(values)


@pytest.mark.parametrize("duplicate", ["ticker", "trading212_id", "exchange_ticker"])
def test_ambiguous_catalogue_identifiers_fail_closed(duplicate):
    first = fixture_entry()
    second = replace(
        first,
        metadata=first.metadata.model_copy(update={"ticker": "OTHER.L"}),
        identifiers=first.identifiers.model_copy(
            update={
                "ticker": "OTHER.L",
                "trading212_id": "OTHERl_EQ",
                "exchange_ticker": "OTHER",
            }
        ),
    )
    if duplicate == "ticker":
        second = replace(second, metadata=first.metadata)
    else:
        second = replace(
            second,
            identifiers=second.identifiers.model_copy(
                update={
                    duplicate: getattr(first.identifiers, duplicate),
                }
            ),
        )
    with pytest.raises(ValueError, match="CATALOGUE_AMBIGUOUS"):
        InstrumentCatalogue((first, second), "live")


def test_demo_is_explicit_and_cannot_be_promoted_to_live():
    demo = InstrumentCatalogue.demonstration(NOW)
    page = demo.search(InstrumentSearchQuery(query="demo"), NOW)
    assert page.mode == "demo" and page.instruments[0].synthetic
    assert [entry.ticker for entry in page.instruments] == ["DEMO.L"]
    with pytest.raises(ValueError, match="LIVE_INSTRUMENT_INVALID"):
        InstrumentCatalogue(demo.entries, "live")
