from datetime import timedelta
from decimal import Decimal

import pytest

from money.adapters.eligibility import eligibility_failures
from money.data.normalization.prices import normalize_gbp
from money.data.quality.market import evaluate_market_quality
from money.flows.research import DemoFirm, build_runtime, demo_snapshot
from money.policy.governance import consensus, evidence_independence
from money.risk.costs import CostApplicability
from money.scanner.technical import calculate_technical
from money.schemas.contracts import (
    AuditFinding,
    CIOAuditReport,
    EvidenceRecord,
    LeanValidationReport,
    PriceBar,
    RedTeamReport,
    ResearchMandate,
    ResearchSnapshot,
    ResearchState,
    content_hash,
    utc_now,
)
from money.signals.generation import SignalPolicy, generate_signal


def fixture(currency="GBX"):
    instrument = build_runtime("demo").eligibility.get_instrument_metadata("DEMO.L")
    instrument = instrument.model_copy(update={"quote_currency": currency})
    original = demo_snapshot(instrument)
    reference = next(e for e in original.evidence if e.payload.kind == "ohlcv")
    bars = []
    divisor = Decimal(100) if currency == "GBP" else Decimal(1)
    for index in range(60):
        observed = original.price_cutoff - timedelta(days=59 - index)
        values = dict(reference.model_dump())
        values.update(
            evidence_id=f"bar-{index}",
            source_id=f"price-{index}",
            canonical_source_id=f"price-{index}",
            observation_time=observed,
            publication_time=observed,
            hash="",
            payload=PriceBar(
                open=Decimal(99 + index) / divisor,
                high=Decimal(105 + index) / divisor,
                low=Decimal(95 + index) / divisor,
                close=Decimal(100 + index) / divisor,
                volume=1_000_000,
                currency=currency,
            ),
        )
        bars.append(EvidenceRecord.model_validate(values))
    data = original.model_dump()
    data.update(
        evidence=tuple(bars) + tuple(e for e in original.evidence if e.payload.kind != "ohlcv"),
        hash="",
    )
    snapshot = ResearchSnapshot.model_validate(data)
    reports = tuple(
        DemoFirm(firm).research(ResearchMandate(), snapshot).model_copy(update={"runtime": "live"})
        for firm in ("tradingagents", "ai_hedge_fund", "qlib")
    )
    lean = LeanValidationReport(
        state="PASS",
        snapshot_id=snapshot.snapshot_id,
        runner_version="test-only-fixture",
        observations=60,
        walk_forward=True,
        out_of_sample=True,
        pit_safe=True,
        survivorship_checked=True,
        costs_included=True,
        sensitivity_checked=True,
        spread_bps=20,
        slippage_bps=10,
        scenario_policy_hash=content_hash(SignalPolicy()),
    )
    audit = CIOAuditReport(
        snapshot_id=snapshot.snapshot_id,
        completed=True,
        active_specialists=("test-only",),
        findings=tuple(
            AuditFinding(
                auditor="test-only",
                claim_id=claim.claim_id,
                state="VERIFIED",
                explanation="Test evidence linkage",
                evidence_ids=claim.evidence_ids,
            )
            for report in reports
            for claim in report.claims
        ),
    )
    red = RedTeamReport(state="PASS", findings=())
    now = utc_now()
    quality = evaluate_market_quality(
        snapshot,
        now,
        spread_bps=Decimal(20),
        adjustment_basis="RAW",
        corporate_actions_complete=True,
    )
    costs = CostApplicability(
        sdrt="APPLIES",
        evidence_source="test fixture",
        broker_round_trip_fee_gbp=Decimal(0),
        broker_fee_source="test fixture",
        other_round_trip_charges_gbp=Decimal(0),
        other_charge_source="test fixture",
    )
    state, _ = consensus(
        ResearchMandate(),
        snapshot,
        reports,
        lean,
        audit,
        red,
        evidence_independence(snapshot, reports),
        now,
    )
    return dict(
        research_id="fixture-job",
        mandate=ResearchMandate(),
        snapshot=snapshot,
        reports=reports,
        lean=lean,
        audit=audit,
        red_team=red,
        state=state,
        issued_at=now,
        market_quality=quality,
        cost_applicability=costs,
    )


def test_real_atr_scenario_preserves_raw_gbx_and_normalized_gbp():
    params = fixture()
    design = generate_signal(**params)
    assert design.signal is not None, design.reasons
    signal = design.signal
    assert signal.quote_currency == "GBX"
    assert design.current_raw == Decimal(159)
    assert design.current_gbp == Decimal("1.59")
    assert signal.entry_low / 100 == design.entry_low_gbp
    assert signal.potential_targets[0] / 100 == design.targets_gbp[0]
    assert signal.illustrative_allocation_gbp <= signal.assumed_capital_gbp <= 200
    assert signal.modelled_downside_gbp <= 4
    assert design.costs.total_round_trip_gbp > 0
    assert design.risk_reward >= Decimal("1.5")
    assert signal.calibrated_confidence is None
    assert design.confidence == "uncalibrated"
    assert signal.state != "STRONG_RESEARCH_CANDIDATE"
    assert signal.valid_until <= min(
        e.fresh_until for e in params["snapshot"].evidence if e.critical
    )


def test_gbp_gbx_numeric_normalization_does_not_admit_gbp_stocks():
    pounds, pence = fixture("GBP"), fixture("GBX")
    # Numeric evidence/scanner support for both units is independent of the
    # current GBX-only stock admission policy. Do not bypass that policy to
    # test normalization by publishing an excluded GBP security.
    assert calculate_technical(pounds["snapshot"]).values == calculate_technical(pence["snapshot"]).values
    for params in (pounds, pence):
        latest = [item for item in params["snapshot"].evidence if item.payload.kind == "ohlcv"][-1]
        assert normalize_gbp(latest.payload.close, latest.payload.currency).gbp == Decimal("1.59")
    assert "CURRENCY_EXCLUDED" in eligibility_failures(
        pounds["snapshot"].instrument, pounds["mandate"], pounds["issued_at"],
    )
    assert generate_signal(**pounds).signal is None
    assert generate_signal(**pence).signal is not None


def test_stretch_never_changes_qualified_gbx_scenario_risk():
    params = fixture("GBX")
    baseline = generate_signal(**params)
    assert baseline.signal is not None
    params["mandate"] = params["mandate"].model_copy(update={"stretch_profit_gbp": Decimal("0")})
    ambitious = generate_signal(**params)
    assert ambitious.signal is not None
    assert ambitious.entry_high_gbp == baseline.entry_high_gbp
    assert ambitious.signal.illustrative_allocation_gbp == baseline.signal.illustrative_allocation_gbp
    assert ambitious.signal.modelled_downside_gbp == baseline.signal.modelled_downside_gbp


@pytest.mark.parametrize("failure", ["demo", "lean", "audit", "veto", "costs", "strong", "spread"])
def test_no_signal_when_any_required_gate_is_missing(failure):
    params = fixture()
    if failure == "demo":
        params["reports"] = tuple(
            report.model_copy(update={"runtime": "demo"}) for report in params["reports"]
        )
    elif failure == "lean":
        params["lean"] = params["lean"].model_copy(update={"state": "INSUFFICIENT_EVIDENCE"})
    elif failure == "audit":
        params["audit"] = params["audit"].model_copy(update={"completed": False})
    elif failure == "veto":
        params["red_team"] = RedTeamReport(state="VETO", findings=("test veto",))
    elif failure == "costs":
        params["cost_applicability"] = params["cost_applicability"].model_copy(
            update={"sdrt": "UNKNOWN"}
        )
    elif failure == "strong":
        params["state"] = ResearchState.STRONG_RESEARCH_CANDIDATE
    else:
        params["market_quality"] = params["market_quality"].model_copy(
            update={"spread_bps": Decimal(21)}
        )
    result = generate_signal(**params)
    assert result.signal is None and result.reasons


def test_scenario_costs_can_consume_the_risk_budget():
    params = fixture()
    params["cost_applicability"] = params["cost_applicability"].model_copy(
        update={"broker_round_trip_fee_gbp": Decimal(5)}
    )
    result = generate_signal(**params)
    assert result.signal is None
    assert result.reasons == ("ILLUSTRATIVE_RISK_BUDGET_INSUFFICIENT",)


def test_policy_cannot_raise_capital_or_empirical_confidence():
    from pydantic import ValidationError

    for change in (
        {"maximum_allocation_gbp": 201},
        {"maximum_downside_fraction": ".03"},
        {"confidence": 0.87},
    ):
        with pytest.raises(ValidationError):
            SignalPolicy.model_validate(change)


@pytest.mark.parametrize(
    "change",
    [None, {"horizon_days": 10}, {"target_atr": Decimal(5)}, {"invalidation_atr": Decimal(3)}],
)
def test_displayed_scenarios_require_exact_lean_policy_binding(change):
    params = fixture()
    if change is None:
        params["lean"] = params["lean"].model_copy(update={"scenario_policy_hash": None})
    else:
        params["policy"] = SignalPolicy.model_validate(change)
    result = generate_signal(**params)
    assert result.signal is None
    assert result.reasons == ("LEAN_SCENARIO_POLICY_UNQUALIFIED",)


def test_mandate_cannot_silently_change_validated_scenario_horizon():
    params = fixture()
    params["mandate"] = ResearchMandate(minimum_horizon_days=10)
    result = generate_signal(**params)
    assert result.signal is None
    assert result.reasons == ("LEAN_SCENARIO_POLICY_UNQUALIFIED",)


def test_explicitly_validated_nondefault_horizon_can_generate_same_policy():
    params = fixture()
    policy = SignalPolicy(horizon_days=10)
    params["policy"] = policy
    params["lean"] = params["lean"].model_copy(
        update={"scenario_policy_hash": content_hash(policy)}
    )
    result = generate_signal(**params)
    assert result.signal is not None, result.reasons
    assert result.signal.horizon_days == 10


def test_optional_lean_policy_hash_preserves_legacy_report_bytes():
    lean = fixture()["lean"]
    legacy = lean.model_copy(update={"scenario_policy_hash": None}).model_dump(mode="json")
    assert "scenario_policy_hash" not in legacy
    assert LeanValidationReport.model_validate(legacy).model_dump(mode="json") == legacy
