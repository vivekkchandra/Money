"""Synthetic mechanics only: no fixture is real issuer qualification evidence."""

from datetime import UTC, datetime, timedelta

import pytest

from money.adapters.eligibility import Trading212EligibilityAdapter, eligibility_failures
from money.data.identifiers import InstrumentIdentifiers
from money.data.live_eligibility import EligibilityReview, Trading212LiveEligibilityService
from money.research.ethics import ethical_policy_hash
from money.scanner.universe import QualifiedUniverseSnapshot, freeze_qualified_universe
from money.schemas.contracts import (
    EXCLUDED_ACTIVITIES,
    EthicalClearance,
    InstrumentMetadata,
    ResearchMandate,
    content_hash,
)

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)
ISIN = "GB0006389398"


def screening(**updates):
    values = {
        "result": "PASS",
        "issuer_key": "test-issuer-not-real-evidence",
        "isins": [ISIN],
        "screened_at": (NOW - timedelta(days=10)).isoformat().replace("+00:00", "Z"),
        "valid_until": (NOW + timedelta(days=20)).isoformat().replace("+00:00", "Z"),
        "policy_hash": ethical_policy_hash(),
        "evidence_hashes": [content_hash({"synthetic": "business disclosures"})],
        "assessed_exclusions": list(EXCLUDED_ACTIVITIES),
        "reasons": [],
        "business_activities": ["soft_drinks"],
    } | updates
    for field in ("screened_at", "valid_until"):
        values[field] = values[field].replace("+00:00", "Z")
    values["screening_hash"] = content_hash(values)
    return EthicalClearance.model_validate(values)


def metadata(clearance=None, **updates):
    return InstrumentMetadata(
        ticker="SYNTHETIC",
        company="Synthetic beverage issuer fixture",
        instrument_type="STOCK",
        quote_currency="GBX",
        currently_available=True,
        business_activities=("soft_drinks",),
        activities_verified=True,
        verified_at=NOW - timedelta(hours=1),
        source="Synthetic unit-test broker metadata",
        provider="fixture-broker",
        source_id="SYNTHETIC_EQ",
        ethical_clearance=screening() if clearance is None else clearance,
    ).model_copy(update=updates)


def review(clearance=None, **metadata_updates):
    item = metadata(clearance, **metadata_updates)
    return EligibilityReview(
        metadata=item,
        identifiers=InstrumentIdentifiers(
            ticker=item.ticker,
            company_name=item.company,
            trading212_id=item.source_id,
            exchange_ticker=item.ticker,
            exchange="XLON",
            isin=ISIN,
            quote_currency="GBX",
            provider_symbols=(("eodhd", "SYNTHETIC.LSE"),),
            verified_at=NOW - timedelta(hours=1),
            valid_until=NOW + timedelta(hours=1),
            source="Synthetic exact unit-test identity",
        ),
        eligibility_proof_hash=content_hash({"synthetic": "broker proof"}),
        ethical_proof_hash=content_hash(item.ethical_clearance),
    )


class Broker:
    """No trading or account methods exist on this test transport."""

    def instruments(self):
        return ({
            "ticker": "SYNTHETIC_EQ", "isin": ISIN,
            "currencyCode": "GBX", "type": "STOCK",
        },)


def test_single_pass_without_any_reviewer_is_reused_by_live_and_frozen_universe():
    item = review()
    assert not any("reviewer" in name for name in EthicalClearance.model_fields)
    assert eligibility_failures(item.metadata, ResearchMandate(), NOW) == ()
    service = Trading212LiveEligibilityService(Broker(), lambda: (item,), clock=lambda: NOW)
    assert service.get_universe() == (item.metadata,)
    frozen = freeze_qualified_universe((item,), (), NOW)
    restored = QualifiedUniverseSnapshot.model_validate_json(frozen.model_dump_json())
    assert restored.members[0].instrument.ethical_clearance == item.metadata.ethical_clearance
    assert restored.members[0].evidence_status == "RESEARCH_EVIDENCE_REQUIRED"
    # Ethics is ten days old; live membership remains fresh, separately.
    assert restored.members[0].instrument.ethical_clearance.screened_at < NOW - timedelta(days=1)


def test_verified_share_lines_reuse_the_identical_issuer_clearance():
    shared = screening(isins=[ISIN, "GB00BH4HKS39"])
    first = review(shared)
    second_data = first.model_dump()
    second_data["metadata"] |= {"ticker": "SYNTHETICB", "source_id": "SYNTHETICB_EQ"}
    second_data["identifiers"] |= {
        "ticker": "SYNTHETICB", "trading212_id": "SYNTHETICB_EQ",
        "exchange_ticker": "SYNTHETICB", "isin": "GB00BH4HKS39",
    }
    second = EligibilityReview.model_validate(second_data)
    frozen = freeze_qualified_universe((first, second), (), NOW)
    assert len(frozen.members) == 2
    assert {member.instrument.ethical_clearance.screening_hash for member in frozen.members} == {
        shared.screening_hash,
    }
    assert first.ethical_proof_hash == second.ethical_proof_hash


@pytest.mark.parametrize("result,reason", [
    ("FAIL", "PROHIBITED_ACTIVITY"), ("UNKNOWN", "ETHICAL_SCREEN_UNKNOWN"),
])
def test_non_pass_cannot_progress(result, reason):
    item = metadata(screening(result=result))
    assert reason in eligibility_failures(item, ResearchMandate(), NOW)
    assert Trading212EligibilityAdapter((item,), clock=lambda: NOW).get_universe() == ()


def test_pass_without_evidence_is_invalid():
    with pytest.raises(ValueError, match="EVIDENCE_INCOMPLETE"):
        screening(evidence_hashes=[])


def test_expiry_requires_a_new_screen_without_extending_old_screen_time():
    clearance = screening(valid_until=NOW.isoformat())
    assert "ETHICAL_CLEARANCE_EXPIRED" in eligibility_failures(
        metadata(clearance), ResearchMandate(), NOW,
    )
    assert clearance.screened_at == NOW - timedelta(days=10)


def test_frozen_member_cannot_outlive_ethical_clearance_near_expiry():
    expiry = NOW + timedelta(minutes=5)
    item = review(screening(valid_until=expiry.isoformat()))
    frozen = freeze_qualified_universe((item,), (), NOW)
    assert frozen.valid_until == expiry
    values = frozen.model_dump()
    values["hash"] = ""
    values["valid_until"] = NOW + timedelta(minutes=10)
    values["members"][0]["valid_until"] = values["valid_until"]
    with pytest.raises(ValueError, match="UNIVERSE_MEMBER_ETHICAL_EXPIRY_EXCEEDED"):
        QualifiedUniverseSnapshot.model_validate(values)


def test_policy_changes_invalidate_previously_passing_screen():
    mandate = ResearchMandate(excluded_activities=(*EXCLUDED_ACTIVITIES, "new_activity"))
    assert "ETHICAL_POLICY_CHANGED" in eligibility_failures(metadata(), mandate, NOW)


def test_known_excluded_activity_still_blocks_even_a_pass_clearance():
    item = metadata(screening(business_activities=["weapons"]), business_activities=("weapons",))
    assert "PROHIBITED_ACTIVITY" in eligibility_failures(item, ResearchMandate(), NOW)


def test_unvalidated_mutation_of_clearance_fails_closed():
    changed = screening(result="UNKNOWN").model_copy(update={"result": "PASS"})
    assert "ETHICAL_CLEARANCE_INVALID" in eligibility_failures(
        metadata().model_copy(update={"ethical_clearance": changed}), ResearchMandate(), NOW,
    )


def test_current_clearance_never_refreshes_stale_broker_membership():
    item = metadata(verified_at=NOW - timedelta(hours=24))
    assert "ELIGIBILITY_STALE" in eligibility_failures(item, ResearchMandate(), NOW)


def test_clearance_cannot_be_copied_to_an_unrelated_isin_or_changed_proof():
    with pytest.raises(ValueError, match="ETHICAL_CLEARANCE_IDENTITY_MISMATCH"):
        review(screening(isins=["GB00BH4HKS39"]))
    values = review().model_dump()
    values["ethical_proof_hash"] = content_hash({"unrelated": "proof"})
    with pytest.raises(ValueError, match="ETHICAL_CLEARANCE_PROOF_MISMATCH"):
        EligibilityReview.model_validate(values)


def test_live_callback_rechecks_contradictions_even_when_broker_response_is_cached():
    reviews = [review()]
    service = Trading212LiveEligibilityService(Broker(), lambda: tuple(reviews), clock=lambda: NOW)
    assert service.get_universe()
    reviews[:] = [review(screening(result="UNKNOWN", reasons=["CONTRADICTORY_EVIDENCE"]))]
    assert service.get_universe() == ()


def test_absent_optional_clearance_does_not_change_legacy_snapshot_serialization():
    old = metadata().model_dump(mode="json", exclude={"ethical_clearance"})
    restored = InstrumentMetadata.model_validate(old)
    assert restored.ethical_clearance is None
    assert restored.model_dump(mode="json") == old
