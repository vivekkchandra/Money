"""Synthetic review evidence tests; no live ISA availability or licensing claim."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_instrument_catalogue import NOW, fixture_entry, fixture_qualification

from money.adapters.eligibility import Trading212EligibilityAdapter, eligibility_failures
from money.data.instruments import InstrumentCatalogue
from money.data.universe import IsaUniverseQuery, ReviewedIsaUniverse
from money.schemas.contracts import ResearchMandate, content_hash


def reviewed_entry(**updates):
    return replace(
        fixture_entry(**updates),
        eligibility_proof_hash="a" * 64,
        ethical_proof_hash="b" * 64,
    )


def universe(entries=None, qualifications=None):
    return ReviewedIsaUniverse(
        InstrumentCatalogue(
            (reviewed_entry(),) if entries is None else entries,
            "live",
            (fixture_qualification(),) if qualifications is None else qualifications,
        )
    )


def page(service, now=NOW, mandate=None, **query):
    return service.page(IsaUniverseQuery(**query), mandate=mandate or ResearchMandate(), now=now)


def test_verified_reviewed_subset_is_current_stock_only_with_provenance():
    result = page(universe())
    assert result.coverage == "reviewed_manifest"
    assert not result.complete_broker_universe
    assert result.total == 1 and result.mode == "live"
    row = result.instruments[0]
    assert row.eligibility == "VERIFIED_ELIGIBLE"
    assert row.research_allowed and not row.synthetic
    assert row.instrument_type == "STOCK" and row.currency == "GBX"
    assert row.eligibility_proof_hash == "a" * 64 and row.ethical_proof_hash == "b" * 64
    assert row.source == "Synthetic test review" and row.source_id == "fixture-instrument"
    assert row.metadata_hash == content_hash(reviewed_entry().metadata.model_dump(mode="json"))
    assert row.verified_until == NOW + timedelta(hours=1)
    assert result.evaluated_at == NOW


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"quote_currency": "USD"}, "CURRENCY_EXCLUDED"),
        ({"quote_currency": "EUR"}, "CURRENCY_EXCLUDED"),
        ({"instrument_type": "ETF"}, "INSTRUMENT_TYPE_EXCLUDED"),
        ({"instrument_type": "FUND"}, "INSTRUMENT_TYPE_EXCLUDED"),
        ({"instrument_type": "WARRANT"}, "INSTRUMENT_TYPE_EXCLUDED"),
        ({"isa_available": None}, "ISA_ELIGIBILITY_UNCONFIRMED"),
        ({"isa_available": False}, "ISA_ELIGIBILITY_UNCONFIRMED"),
        ({"currently_available": None}, "INSTRUMENT_UNAVAILABLE"),
        ({"currently_available": False}, "INSTRUMENT_UNAVAILABLE"),
        ({"verified_at": NOW - timedelta(hours=24, seconds=1)}, "ELIGIBILITY_STALE"),
        ({"verified_at": NOW - timedelta(hours=24)}, "ELIGIBILITY_STALE"),
        ({"verified_at": NOW + timedelta(seconds=1)}, "ELIGIBILITY_STALE"),
        ({"activities_verified": False}, "ETHICAL_SCREEN_UNKNOWN"),
        ({"business_activities": ()}, "ETHICAL_SCREEN_UNKNOWN"),
        ({"business_activities": ("   ",)}, "ETHICAL_SCREEN_UNKNOWN"),
        ({"business_activities": ("telecommunications", "UNKNOWN")}, "ETHICAL_SCREEN_UNKNOWN"),
        ({"business_activities": ("unknown material exposure",)}, "ETHICAL_SCREEN_UNKNOWN"),
        ({"business_activities": ("defence",)}, "PROHIBITED_ACTIVITY"),
        ({"business_activities": (" Oil Exploration ",)}, "PROHIBITED_ACTIVITY"),
        ({"business_activities": ("MATERIAL-MILITARY-CONTRACTING",)}, "PROHIBITED_ACTIVITY"),
        ({"business_activities": ("oil_services",)}, "PROHIBITED_ACTIVITY"),
        ({"source": " "}, "ELIGIBILITY_PROVENANCE_MISSING"),
        ({"provider": " "}, "ELIGIBILITY_PROVENANCE_MISSING"),
        ({"source_id": " "}, "ELIGIBILITY_PROVENANCE_MISSING"),
    ],
)
def test_universe_excludes_each_failed_hard_gate_without_relaxing_review(updates, reason):
    entry = reviewed_entry(**updates)
    assert reason in eligibility_failures(entry.metadata, ResearchMandate(), NOW)
    assert page(universe((entry,))).instruments == ()
    adapter = Trading212EligibilityAdapter((entry.metadata,), clock=lambda: NOW)
    assert adapter.get_isa_universe() == ()


@pytest.mark.parametrize("field", ["eligibility_proof_hash", "ethical_proof_hash"])
@pytest.mark.parametrize("proof", [None, "", "bad-proof", "z" * 64])
def test_review_evidence_must_be_hash_identified(field, proof):
    entry = replace(reviewed_entry(), **{field: proof})
    assert page(universe((entry,))).total == 0


def test_proof_hashes_survive_manifest_to_catalogue_boundary():
    entry = reviewed_entry()
    manifest = SimpleNamespace(
        instruments=(entry,),
        provider_qualifications=(fixture_qualification(),),
    )
    catalogue = InstrumentCatalogue.from_manifest(manifest)
    assert catalogue.entries[0].eligibility_proof_hash == entry.eligibility_proof_hash
    assert catalogue.entries[0].ethical_proof_hash == entry.ethical_proof_hash
    assert page(ReviewedIsaUniverse(catalogue)).total == 1


def test_provider_qualification_required_and_rechecked():
    with pytest.raises(ValueError, match="UNIVERSE_UNAVAILABLE"):
        universe(qualifications=())
    with pytest.raises(ValueError, match="PROVIDER_COVERAGE_MISSING"):
        page(universe(), now=NOW + timedelta(days=2))
    with pytest.raises(ValueError, match="PROVIDER_UNQUALIFIED"):
        page(
            universe(
                qualifications=(
                    fixture_qualification().model_copy(
                        update={
                            "development_only": True,
                        }
                    ),
                )
            )
        )
    with pytest.raises(ValueError, match="PROVIDER_COVERAGE_MISSING"):
        page(
            universe(qualifications=(fixture_qualification().model_copy(update={"datasets": ()}),))
        )


def test_demo_universe_cannot_be_promoted():
    with pytest.raises(ValueError, match="UNIVERSE_UNAVAILABLE"):
        ReviewedIsaUniverse(InstrumentCatalogue.demonstration(NOW))


def test_repeated_reads_do_not_refresh_old_verification_or_change_catalogue_hash():
    service = universe()
    first = page(service)
    later = page(service, now=NOW + timedelta(minutes=30))
    assert first.catalogue_hash == later.catalogue_hash
    assert first.instruments[0].verified_at == later.instruments[0].verified_at
    assert first.instruments[0].verified_until == later.instruments[0].verified_until
    assert page(service, now=NOW + timedelta(hours=1)).total == 0


def test_stale_identity_and_quote_mapping_cannot_enter_reviewed_subset():
    entry = reviewed_entry()
    for updates in ({"valid_until": NOW}, {"ticker": "OTHER.L"}, {"quote_currency": "GBP"}):
        invalid = replace(entry, identifiers=entry.identifiers.model_copy(update=updates))
        assert page(universe((invalid,))).total == 0


def test_catalogue_hash_is_order_independent_and_changes_with_review_evidence():
    first = reviewed_entry()
    second = replace(
        first,
        metadata=first.metadata.model_copy(update={"ticker": "SECOND.L"}),
        identifiers=first.identifiers.model_copy(
            update={
                "ticker": "SECOND.L",
                "trading212_id": "SECONDl_EQ",
                "exchange_ticker": "SECOND",
            }
        ),
    )
    assert (
        page(universe((first, second))).catalogue_hash
        == page(universe((second, first))).catalogue_hash
    )
    changed = replace(first, eligibility_proof_hash="c" * 64)
    assert page(universe((first,))).catalogue_hash != page(universe((changed,))).catalogue_hash


def test_search_and_pagination_stay_bounded_with_exact_identifiers():
    entry = reviewed_entry()
    assert page(universe(), query="GB00BH4HKS39").total == 1
    assert page(universe(), query="CAFÉ").total == 1
    assert page(universe(), query="unknown").total == 0
    assert page(universe((entry,)), limit=1, offset=1).instruments == ()
    assert page(universe((entry,)), limit=1, offset=1).total == 1


@pytest.mark.parametrize(
    "values",
    [
        {"query": "a" * 81},
        {"query": "company\n"},
        {"limit": 0},
        {"limit": 51},
        {"offset": -1},
        {"offset": 10001},
        {"include_unknown": True},
    ],
)
def test_universe_contract_rejects_unbounded_or_unknown_inputs(values):
    with pytest.raises(ValidationError):
        IsaUniverseQuery.model_validate(values)


def test_naive_timestamps_fail_closed():
    with pytest.raises(ValueError, match="ELIGIBILITY_TIMESTAMP_INVALID"):
        page(universe(), now=NOW.replace(tzinfo=None))
    assert "ELIGIBILITY_STALE" in eligibility_failures(
        reviewed_entry().metadata,
        ResearchMandate(),
        NOW.replace(tzinfo=None),
    )


def test_current_eligibility_methods_never_return_stale_confirmation():
    clock = [NOW]
    entry = reviewed_entry()
    adapter = Trading212EligibilityAdapter((entry.metadata,), clock=lambda: clock[0])
    assert adapter.is_available_in_isa(entry.metadata.ticker) is True
    assert adapter.is_currently_available(entry.metadata.ticker) is True
    clock[0] = NOW + timedelta(days=1)
    assert adapter.is_available_in_isa(entry.metadata.ticker) is None
    assert adapter.is_currently_available(entry.metadata.ticker) is None
    assert adapter.get_isa_universe() == ()
    assert adapter.get_instrument_metadata(entry.metadata.ticker) == entry.metadata


def test_hard_bounds_survive_unvalidated_internal_mandate_copy():
    weak = ResearchMandate().model_copy(
        update={
            "quote_currencies": ("USD",),
            "instrument_types": ("ETF",),
            "excluded_activities": (),
        }
    )
    record = reviewed_entry(
        quote_currency="USD",
        instrument_type="ETF",
        business_activities=("weapons",),
    ).metadata
    assert set(eligibility_failures(record, weak, NOW)) >= {
        "CURRENCY_EXCLUDED",
        "INSTRUMENT_TYPE_EXCLUDED",
        "PROHIBITED_ACTIVITY",
    }


def test_narrower_user_mandate_can_remove_gbx_but_cannot_expand_it():
    assert page(universe(), mandate=ResearchMandate(quote_currencies=("GBP",))).total == 0


def test_gbp_stock_keeps_raw_quote_unit_without_currency_guessing():
    entry = reviewed_entry(quote_currency="GBP")
    entry = replace(
        entry, identifiers=entry.identifiers.model_copy(update={"quote_currency": "GBP"})
    )
    row = page(universe((entry,))).instruments[0]
    assert row.currency == "GBP" and row.research_allowed


def test_blank_provenance_cannot_claim_verified_isa_status():
    entry = reviewed_entry(source=" ")
    adapter = Trading212EligibilityAdapter((entry.metadata,), clock=lambda: NOW)
    assert adapter.is_available_in_isa(entry.metadata.ticker) is None
    assert adapter.is_currently_available(entry.metadata.ticker) is None
    assert entry.result(NOW, synthetic=False).eligibility == "UNKNOWN"
