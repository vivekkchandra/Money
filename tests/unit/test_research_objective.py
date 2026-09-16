"""Synthetic inputs test arithmetic/governance only, never live qualification."""

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from test_signal_generation import fixture

from money.policy.governance import consensus, evidence_independence
from money.research.objective import AsymmetryAnalyzer, StretchObjective, rank_opportunities
from money.schemas.contracts import Candidate, DecisionPacket, ResearchMandate, utc_now
from money.signals.generation import generate_signal


def published(currency="GBX", **mandate_changes):
    params = fixture(currency)
    params["mandate"] = ResearchMandate(**mandate_changes)
    # A third independently identified fixture source earns MEDIUM independence;
    # the base signal fixture deliberately has only two sources and yields WATCH.
    quant = params["reports"][2]
    claim = quant.claims[0].model_copy(update={"evidence_ids": ("bar-59",)})
    params["reports"] = (*params["reports"][:2], quant.model_copy(update={"claims": (claim,)}))
    params["audit"] = params["audit"].model_copy(update={"findings": tuple(
        finding.model_copy(update={"evidence_ids": claim.evidence_ids})
        if finding.claim_id == claim.claim_id else finding
        for finding in params["audit"].findings
    )})
    params["state"], _ = consensus(
        params["mandate"], params["snapshot"], params["reports"], params["lean"],
        params["audit"], params["red_team"],
        evidence_independence(params["snapshot"], params["reports"]), utc_now(),
    )
    design = generate_signal(**params)
    assert design.signal is not None, design.reasons
    packet = DecisionPacket(
        research_id=params["research_id"], mandate=params["mandate"],
        snapshot_id=params["snapshot"].snapshot_id, snapshot_hash=params["snapshot"].hash,
        candidate=Candidate(ticker=params["snapshot"].ticker, discovery=()),
        eligibility=params["snapshot"].instrument, reports=params["reports"],
        lean=params["lean"], audit=params["audit"], red_team=params["red_team"],
        independence=evidence_independence(params["snapshot"], params["reports"]),
        final_state=params["state"], reasons=("TEST_FIXTURE",),
        sources=params["snapshot"].evidence, issued_at=utc_now(), signal=design.signal,
        runtime="live", frozen_snapshot=params["snapshot"],
    )
    return packet, design


def test_objective_is_aspirational_fixed_arithmetic_and_roundtrips():
    objective = StretchObjective()
    assert objective.target_end_value == objective.starting_capital + objective.target_profit
    assert objective.target_return == objective.target_profit / objective.starting_capital == 5
    assert StretchObjective.model_validate_json(objective.model_dump_json()) == objective
    for changes in ({"can_override_risk": True}, {"starting_capital": 201}, {"horizon_days": 31}):
        with pytest.raises(ValidationError):
            StretchObjective(**changes)


@pytest.mark.parametrize("currency,raw", [("GBP", Decimal("1.59")), ("GBX", Decimal("159"))])
def test_only_verified_scenario_is_compared_without_capital_extrapolation(currency, raw):
    packet, design = published(currency)
    result = AsymmetryAnalyzer.assess(packet, design, utc_now())
    assert result is not None
    assert result.raw_price == raw and result.normalized_price_gbp == Decimal("1.59")
    assert result.illustrative_allocation_gbp <= result.assumed_capital_gbp <= 200
    expected = result.illustrative_allocation_gbp * (
        design.targets_gbp[0] / design.entry_high_gbp - 1
    ) - design.costs.total_round_trip_gbp
    assert result.potential_upside_gbp == expected
    assert result.gap_to_stretch_profit_gbp == 1000 - expected
    assert result.probability is None and result.expected_payoff_gbp is None
    assert result.calibration == "UNCALIBRATED"


def test_empty_objective_does_not_invent_candidate_portfolio_or_countdown():
    now = utc_now()
    report = rank_opportunities((), now=now, examined=0)
    assert report.opportunities == ()
    assert report.conclusion == "NO_QUALIFIED_OPPORTUNITY_CURRENTLY_SUPPORTS_THE_STRETCH_OBJECTIVE"
    assert "time_remaining" not in report.model_dump()
    assert report.basis == "RESEARCH_SIMULATION_NOT_ACTUAL_PORTFOLIO"
    assert report == rank_opportunities((), now=now, examined=0)


def test_stretch_does_not_change_sizing_targets_or_risk():
    packet, design = published(stretch_profit_gbp=0)
    zero = AsymmetryAnalyzer.assess(packet, design, utc_now())
    assert zero is not None
    assert zero.gap_to_stretch_profit_gbp == 1000 - zero.potential_upside_gbp
    assert zero.modelled_downside_gbp == design.signal.modelled_downside_gbp <= 4
    # The post-publication objective is not sent back into gate/scenario policy.
    assert packet.mandate.stretch_profit_gbp == 0


def test_expired_or_invalidated_scenario_cannot_be_ranked():
    packet, design = published()
    assert AsymmetryAnalyzer.assess(packet, design, packet.signal.valid_until) is None
    assert AsymmetryAnalyzer.assess(packet, design, utc_now(), invalidated=True) is None
    assert AsymmetryAnalyzer.assess(packet, design, packet.issued_at - timedelta(seconds=1)) is None


@pytest.mark.parametrize("change", [
    {"risk_reward": Decimal(500)}, {"targets_gbp": (Decimal(9999),)},
    {"current_gbp": Decimal(159)}, {"current_raw": Decimal(1)},
])
def test_modified_scenario_cannot_inflate_objective(change):
    packet, design = published()
    assert AsymmetryAnalyzer.assess(packet, design.model_copy(update=change), utc_now()) is None


def test_packet_hash_tampering_is_not_silently_repaired():
    packet, design = published()
    with pytest.raises(ValidationError, match="hash"):
        AsymmetryAnalyzer.assess(packet.model_copy(update={"reasons": ("forged",)}), design, utc_now())


def test_missing_pit_snapshot_or_watch_state_cannot_be_ranked():
    packet, design = published()
    data = packet.model_dump(mode="json")
    data.update(hash="", frozen_snapshot=None)
    assert AsymmetryAnalyzer.assess(DecisionPacket.model_validate(data), design, utc_now()) is None
    data.update(hash="", final_state="WATCH")
    data["signal"]["state"] = "WATCH"
    assert AsymmetryAnalyzer.assess(DecisionPacket.model_validate(data), design, utc_now()) is None


def test_ranking_is_transparent_not_target_seeking_and_remains_page_scoped():
    packet, design = published()
    base = AsymmetryAnalyzer.assess(packet, design, utc_now())
    assert base is not None
    # Ranking inputs are already gated; only test ordering here.
    risk_first = base.model_copy(update={"research_id": "risk-first", "risk_reward": Decimal(3)})
    bigger_target = base.model_copy(update={"research_id": "big-target", "risk_reward": Decimal(2),
                                           "potential_upside_gbp": Decimal(1000),
                                           "gap_to_stretch_profit_gbp": Decimal(0)})
    report = rank_opportunities((bigger_target, risk_first), now=utc_now(), examined=2, has_more=True)
    assert report.opportunities[0].research_id == "risk-first"
    assert report.has_more and report.coverage == "PAGINATED_WORKSPACE_PUBLICATIONS"
    assert "WITHOUT_CALIBRATED_PROBABILITY" in report.conclusion
