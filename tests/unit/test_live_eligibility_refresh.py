"""Synthetic service tests, never live eligibility qualification evidence."""

from datetime import timedelta

import pytest
from test_instrument_catalogue import NOW, fixture_entry

from money.data.live_eligibility import EligibilityReview, Trading212LiveEligibilityService
from money.data.security import ProviderFailure


def review(**updates):
    entry = fixture_entry(**updates)
    return EligibilityReview(
        metadata=entry.metadata,
        identifiers=entry.identifiers,
        eligibility_proof_hash="a" * 64,
        ethical_proof_hash="b" * 64,
    )


class Metadata:
    def __init__(self):
        self.rows = [
            {
                "ticker": "FIXTUREl_EQ",
                "isin": "GB00BH4HKS39",
                "currencyCode": "GBX",
                "type": "STOCK",
            }
        ]
        self.calls = 0
        self.error = None

    def instruments(self):
        self.calls += 1
        if self.error:
            raise self.error
        return tuple(self.rows)


def test_refresh_discovers_membership_and_removals_without_refreshing_reviews():
    metadata, clock, reviews = Metadata(), [NOW], [review()]
    service = Trading212LiveEligibilityService(
        metadata, lambda: tuple(reviews), clock=lambda: clock[0]
    )
    assert service.get_isa_universe() == (reviews[0].metadata,)
    assert service.is_available_in_isa("FIXTURE.L") is None
    assert metadata.calls == 1
    clock[0] += timedelta(minutes=10)
    metadata.rows = []
    assert service.get_isa_universe() == ()
    assert service.is_available_in_isa("FIXTURE.L") is None
    assert metadata.calls == 2
    metadata.rows = Metadata().rows
    clock[0] += timedelta(minutes=10)
    assert service.get_isa_universe()[0].verified_at == reviews[0].metadata.verified_at


def test_metadata_presence_alone_does_not_prove_ethics():
    provider = Metadata()
    service = Trading212LiveEligibilityService(provider, lambda: (), clock=lambda: NOW)
    assert service.get_isa_universe() == ()
    for updates in (
        {"currently_available": None},
        {"activities_verified": False},
        {"business_activities": ("defence",)},
        {"verified_at": NOW - timedelta(hours=24)},
    ):
        service = Trading212LiveEligibilityService(
            provider, lambda updates=updates: (review(**updates),), clock=lambda: NOW
        )
        assert service.get_isa_universe() == ()


@pytest.mark.parametrize("legacy_isa", [None, False, True])
def test_live_membership_can_progress_without_any_isa_attestation(legacy_isa):
    expected = review(isa_available=legacy_isa)
    service = Trading212LiveEligibilityService(
        Metadata(), lambda: (expected,), clock=lambda: NOW,
    )
    assert service.get_universe() == (expected.metadata,)
    # An old signature is neither interpreted nor turned into an approval.
    assert service.is_available_in_isa("FIXTURE.L") is None
    assert service.get_universe()[0].isa_available is legacy_isa


@pytest.mark.parametrize(
    "field,value", [("isin", "OTHER"), ("currencyCode", "GBP"), ("type", "ETF")]
)
def test_exact_provider_identity_is_required(field, value):
    provider = Metadata()
    provider.rows[0][field] = value
    service = Trading212LiveEligibilityService(provider, lambda: (review(),), clock=lambda: NOW)
    assert service.get_isa_universe() == ()


def test_revocations_apply_immediately_even_with_cached_broker_metadata():
    provider, reviews = Metadata(), [review()]
    service = Trading212LiveEligibilityService(provider, lambda: tuple(reviews), clock=lambda: NOW)
    assert service.get_isa_universe()
    reviews.clear()
    assert service.get_isa_universe() == ()
    assert provider.calls == 1


def test_failed_refresh_never_serves_previously_confirmed_membership():
    provider, clock = Metadata(), [NOW]
    service = Trading212LiveEligibilityService(
        provider, lambda: (review(),), clock=lambda: clock[0]
    )
    assert service.get_isa_universe()
    clock[0] += timedelta(minutes=10)
    provider.error = ProviderFailure("PROVIDER_UNAVAILABLE")
    with pytest.raises(ProviderFailure):
        service.get_isa_universe()
    provider.error, provider.rows = None, []
    assert service.get_isa_universe() == ()


def test_duplicate_broker_or_review_identities_fail_closed():
    provider = Metadata()
    provider.rows *= 2
    service = Trading212LiveEligibilityService(provider, lambda: (review(),), clock=lambda: NOW)
    with pytest.raises(ValueError, match="METADATA_AMBIGUOUS"):
        service.get_isa_universe()
    service = Trading212LiveEligibilityService(
        Metadata(), lambda: (review(), review()), clock=lambda: NOW
    )
    with pytest.raises(ValueError, match="DUPLICATE_IDENTITY"):
        service.get_isa_universe()


@pytest.mark.parametrize("delay_in", ["provider", "reviews"])
def test_identity_expiring_during_blocking_retrieval_is_rejected(delay_in):
    provider, clock = Metadata(), [NOW]
    entry = review()
    entry = entry.model_copy(
        update={
            "identifiers": entry.identifiers.model_copy(
                update={"valid_until": NOW + timedelta(seconds=1)}
            ),
        }
    )
    original = provider.instruments

    def delayed_provider():
        if delay_in == "provider":
            clock[0] += timedelta(seconds=2)
        return original()

    def delayed_reviews():
        if delay_in == "reviews":
            clock[0] += timedelta(seconds=2)
        return (entry,)

    provider.instruments = delayed_provider
    service = Trading212LiveEligibilityService(provider, delayed_reviews, clock=lambda: clock[0])
    assert service.get_isa_universe() == ()
