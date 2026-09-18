from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from money.adapters.eligibility import Trading212EligibilityAdapter, eligibility_failures
from money.data.normalization.prices import price_gbp
from money.flows.research import DemoFirm, build_runtime, demo_snapshot
from money.policy.governance import consensus, evidence_independence, snapshot_failures
from money.scanner.discovery import union_candidates
from money.schemas.contracts import (
    Candidate,
    DiscoveryReason,
    EvidenceRecord,
    ResearchMandate,
    ResearchSignal,
    ResearchSnapshot,
    ResearchState,
    utc_now,
)


@pytest.fixture
def snapshot():
    instrument = build_runtime("demo").eligibility.get_instrument_metadata("DEMO.L")
    assert instrument is not None
    return demo_snapshot(instrument)


@pytest.mark.parametrize(
    "change",
    [
        {"maximum_capital_gbp": 201},
        {"quote_currencies": ["USD"]},
        {"excluded_activities": []},
        {"stretch_may_override_risk": True},
        {"minimum_horizon_days": 10, "maximum_horizon_days": 2},
        {"get_positions": True},
        {"maximum_horizon_days": 31},
    ],
)
def test_mandate_cannot_expand_risk(change):
    with pytest.raises(ValidationError):
        ResearchMandate(**change)


def test_eligibility_unknown_and_business_exclusion_fail_closed(snapshot):
    mandate = ResearchMandate()
    assert eligibility_failures(None, mandate, utc_now()) == ("INSTRUMENT_ELIGIBILITY_UNKNOWN",)
    for change, failure in [
        ({"quote_currency": "USD"}, "CURRENCY_EXCLUDED"),
        ({"quote_currency": "GBP"}, "CURRENCY_EXCLUDED"),
        ({"currently_available": None}, "INSTRUMENT_UNAVAILABLE"),
        ({"business_activities": ("weapons",)}, "PROHIBITED_ACTIVITY"),
        ({"activities_verified": False}, "ETHICAL_SCREEN_UNKNOWN"),
        ({"verified_at": utc_now() - timedelta(days=2)}, "ELIGIBILITY_STALE"),
    ]:
        instrument = snapshot.instrument.model_copy(update=change)
        assert failure in eligibility_failures(instrument, mandate, utc_now())
    adapter = Trading212EligibilityAdapter((snapshot.instrument,))
    assert adapter.get_quote_currency("DEMO.L") == "GBX"
    assert adapter.is_available_in_isa("UNKNOWN.L") is None
    assert not any(
        hasattr(adapter, name)
        for name in (
            "get_positions",
            "get_orders",
            "get_account_balance",
            "place_order",
            "rebalance",
        )
    )


def test_exact_gbx_normalization():
    assert price_gbp(Decimal("123.45"), "GBX") == Decimal("1.2345")
    assert price_gbp(Decimal("123.45"), "GBP") == Decimal("123.45")
    with pytest.raises(ValueError):
        price_gbp(Decimal("2"), "USD")


def test_snapshot_hash_and_no_opinion_fields(snapshot):
    assert ResearchSnapshot.model_validate_json(snapshot.model_dump_json()).hash == snapshot.hash
    with pytest.raises(ValidationError):
        ResearchSnapshot(**(snapshot.model_dump() | {"peer_report": "buy"}))
    with pytest.raises(ValidationError, match="hash mismatch"):
        ResearchSnapshot(**(snapshot.model_dump() | {"ticker": "DEMO.L", "historical": True}))
    with pytest.raises(ValidationError):
        snapshot.ticker = "OTHER.L"
    with pytest.raises(ValidationError):
        snapshot.evidence[0].payload.close = Decimal("200")


def test_legacy_snapshot_isa_field_remains_hash_compatible(snapshot):
    historical = demo_snapshot(snapshot.instrument.model_copy(update={"isa_available": True}))
    serialized = historical.model_dump_json()
    restored = ResearchSnapshot.model_validate_json(serialized)
    assert restored.hash == historical.hash
    assert restored.model_dump_json() == serialized
    # Historical bytes are retained, but do not become a current account claim.
    service = Trading212EligibilityAdapter((restored.instrument,))
    assert service.is_available_in_isa(restored.ticker) is None


def test_lookahead_and_unknown_publication_are_rejected(snapshot):
    original = snapshot.evidence[0].model_dump(exclude={"hash"})
    original["publication_time"] = snapshot.created_at + timedelta(seconds=1)
    original["retrieval_time"] = snapshot.created_at + timedelta(seconds=1)
    record = EvidenceRecord(**original)
    data = snapshot.model_dump(exclude={"hash"})
    data["evidence"] = (record,)
    with pytest.raises(ValidationError, match="look-ahead publication"):
        ResearchSnapshot(**data)
    original.update(publication_time=None, pit_safe=False, retrieval_time=snapshot.created_at)
    data.update(evidence=(EvidenceRecord(**original),), historical=True)
    with pytest.raises(ValidationError, match="unsafe historical evidence"):
        ResearchSnapshot(**data)


def test_stale_and_conflicting_critical_evidence(snapshot):
    stale = snapshot.model_copy(
        update={
            "evidence": (
                snapshot.evidence[0].model_copy(
                    update={"fresh_until": utc_now() - timedelta(seconds=1), "conflicting": True}
                ),
            )
        }
    )
    assert set(snapshot_failures(stale, utc_now())) == {
        "CRITICAL_EVIDENCE_STALE",
        "CRITICAL_EVIDENCE_CONFLICT",
    }


def test_discovery_is_union_not_technical_veto():
    catalyst = DiscoveryReason(channel="catalyst", reason="new filing", evidence_ids=("a",))
    quant = DiscoveryReason(channel="quantitative", reason="rank", evidence_ids=("b",))
    result = union_candidates(
        (
            (),
            (Candidate(ticker="ABC.L", discovery=(catalyst,)),),
            (Candidate(ticker="ABC.L", discovery=(quant, catalyst)),),
        )
    )
    assert result == (Candidate(ticker="ABC.L", discovery=(catalyst, quant)),)


def test_three_firms_using_one_article_are_one_source(snapshot):
    reports = tuple(
        DemoFirm(firm).research(ResearchMandate(), snapshot)
        for firm in ("tradingagents", "ai_hedge_fund", "qlib")
    )
    reports = tuple(
        report.model_copy(
            update={
                "claims": (report.claims[0].model_copy(update={"evidence_ids": ("demo-news",)}),)
            }
        )
        for report in reports
    )
    independence = evidence_independence(snapshot, reports)
    assert independence.unique_sources == 1
    assert independence.source_overlap == pytest.approx(2 / 3)
    assert independence.strength == "LOW"


def test_hard_gates_and_stretch_never_become_voting(snapshot):
    runtime = build_runtime("demo")
    mandate = ResearchMandate()
    reports = tuple(f.research(mandate, snapshot) for f in runtime.firms)
    lean = runtime.validate(snapshot, reports)
    audit = runtime.audit(snapshot, reports, lean)
    red = runtime.red_team(snapshot, reports)
    independence = evidence_independence(snapshot, reports)
    args = (snapshot, reports, lean, audit, red, independence, utc_now())
    result = consensus(mandate, *args)
    assert result[0] == ResearchState.INSUFFICIENT_EVIDENCE
    assert "LEAN_INSUFFICIENT_EVIDENCE" in result[1]
    assert (
        consensus(mandate.model_copy(update={"stretch_profit_gbp": Decimal("0")}), *args) == result
    )
    with pytest.raises(ValueError, match="at most two"):
        consensus(mandate, *args, rounds=3)


def test_signal_expires_at_boundary_and_on_invalidator():
    now = utc_now()
    signal = ResearchSignal(
        research_id="example",
        ticker="DEMO.L",
        state="WATCH",
        issued_at=now,
        valid_until=now + timedelta(hours=1),
        entry_low="100",
        entry_high="102",
        quote_currency="GBX",
        invalidation_conditions=("thesis disproved",),
        event_invalidators=("dilution announcement",),
        potential_targets=("110",),
        assumed_capital_gbp="200",
        illustrative_allocation_gbp="100",
        modelled_downside_gbp="10",
        horizon_days=1,
    )
    assert signal.effective_state(now) == ResearchState.WATCH
    assert signal.effective_state(signal.valid_until) == ResearchState.EXPIRED
    invalidated = signal.model_copy(update={"invalidated_at": now})
    assert invalidated.effective_state(now) == ResearchState.EXPIRED
