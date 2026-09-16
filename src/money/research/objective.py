"""Read-only comparisons AFTER publication gates; never an input to research policy.

Ranking is an explicit lexicographic ordering of cost-adjusted scenarios, not a
forecast or an additive AI score. No target, risk limit or allocation is changed
to approach the aspirational objective.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, Field

from money.policy.governance import consensus, evidence_independence
from money.schemas.contracts import (
    Contract,
    DecisionPacket,
    ResearchState,
    content_hash,
)
from money.signals.generation import SignalDesign, generate_signal


class StretchObjective(Contract):
    """The product objective, with arithmetic fixed independently of candidate selection."""

    starting_capital: Decimal = Field(default=Decimal("200"), ge=200, le=200)
    target_profit: Decimal = Field(default=Decimal("1000"), ge=1000, le=1000)
    target_end_value: Decimal = Field(default=Decimal("1200"), ge=1200, le=1200)
    target_return: Decimal = Field(default=Decimal("5.0"), ge=5, le=5)
    horizon_days: Literal[30] = 30
    can_override_risk: Literal[False] = False


class ObjectivePageQuery(Contract):
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=10000)


class OpportunityAssessment(Contract):
    research_id: str
    ticker: str
    company: str
    packet_hash: str
    snapshot_hash: str
    signal_design_hash: str
    state: Literal["RESEARCH_CANDIDATE"] = "RESEARCH_CANDIDATE"
    raw_price: Decimal
    raw_currency: Literal["GBP", "GBX"]
    normalized_price_gbp: Decimal
    conversion_method: str
    assumed_capital_gbp: Decimal = Field(gt=0, le=200)
    illustrative_allocation_gbp: Decimal = Field(gt=0, le=200)
    percentage_of_assumed_capital: Decimal = Field(gt=0, le=100)
    modelled_downside_gbp: Decimal = Field(ge=0, le=200)
    potential_upside_gbp: Decimal = Field(gt=0)
    scenario_return_fraction: Decimal
    risk_reward: Decimal = Field(gt=0)
    round_trip_cost_gbp: Decimal = Field(ge=0)
    horizon_days: int = Field(ge=1, le=30)
    issued_at: AwareDatetime
    valid_until: AwareDatetime
    evidence_independence: Literal["MEDIUM", "HIGH"]
    qlib_rank: int | None
    qlib_universe_size: int | None
    spread_bps: Decimal
    gap_to_stretch_profit_gbp: Decimal = Field(ge=0)
    calibration: Literal["UNCALIBRATED"] = "UNCALIBRATED"
    probability: None = None
    expected_payoff_gbp: None = None
    evidence_ids: tuple[str, ...]


class AsymmetryAnalyzer:
    """Recheck a persisted scenario; high volatility alone cannot qualify it."""

    @staticmethod
    def assess(
        packet: DecisionPacket,
        design: SignalDesign,
        now: datetime,
        *,
        invalidated: bool = False,
    ) -> OpportunityAssessment | None:
        # Revalidation rejects mutated Pydantic instances and modified packet hashes.
        packet = DecisionPacket.model_validate_json(packet.model_dump_json())
        design = SignalDesign.model_validate_json(design.model_dump_json())
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("objective evaluation time must be timezone-aware")
        signal, snapshot = packet.signal, packet.frozen_snapshot
        if (
            invalidated
            or packet.runtime != "live"
            or packet.final_state != ResearchState.RESEARCH_CANDIDATE
            or signal is None
            or snapshot is None
            or packet.issued_at > now
            or signal.issued_at > now
            or signal.effective_state(now) != ResearchState.RESEARCH_CANDIDATE
            or design.signal != signal
            or design.designed_at is None
            or design.designed_at > packet.issued_at
            or design.policy is None
            or design.market_quality is None
            or design.cost_applicability is None
        ):
            return None
        independence = evidence_independence(snapshot, packet.reports)
        state, _ = consensus(
            packet.mandate, snapshot, packet.reports, packet.lean, packet.audit,
            packet.red_team, independence, now, rounds=packet.cross_examination_rounds,
        )
        if state != ResearchState.RESEARCH_CANDIDATE or independence != packet.independence:
            return None
        # The persisted publication path already validates correspondence artifacts.
        # Reproduce the display scenario too: a caller cannot invent upside or costs.
        reproduced = generate_signal(
            packet.research_id, packet.mandate, snapshot, packet.reports, packet.lean,
            packet.audit, packet.red_team, state=state, issued_at=design.designed_at,
            market_quality=design.market_quality, cost_applicability=design.cost_applicability,
            policy=design.policy, rounds=packet.cross_examination_rounds,
        )
        if reproduced != design or not design.targets_gbp or design.costs is None:
            return None
        if (
            design.current_raw is None or design.current_currency is None
            or design.current_gbp is None or design.entry_high_gbp is None
            or design.risk_reward is None or design.market_quality.spread_bps is None
        ):
            return None
        allocation = signal.illustrative_allocation_gbp
        # Use the conservative entry bound and first validated target, not the
        # biggest unvalidated level. No £200 extrapolation of a smaller allocation.
        upside = (
            allocation * (design.targets_gbp[0] / design.entry_high_gbp - 1)
            - design.costs.total_round_trip_gbp
        )
        quant = next(report for report in packet.reports if report.firm == "qlib")
        return OpportunityAssessment(
            research_id=packet.research_id, ticker=signal.ticker,
            company=packet.eligibility.company, packet_hash=packet.hash,
            snapshot_hash=packet.snapshot_hash, signal_design_hash=content_hash(design),
            raw_price=design.current_raw, raw_currency=design.current_currency,
            normalized_price_gbp=design.current_gbp,
            conversion_method=design.normalization_method,
            assumed_capital_gbp=signal.assumed_capital_gbp,
            illustrative_allocation_gbp=allocation,
            percentage_of_assumed_capital=allocation / signal.assumed_capital_gbp * 100,
            modelled_downside_gbp=signal.modelled_downside_gbp,
            potential_upside_gbp=upside, scenario_return_fraction=upside / allocation,
            risk_reward=design.risk_reward,
            round_trip_cost_gbp=design.costs.total_round_trip_gbp,
            horizon_days=signal.horizon_days, issued_at=signal.issued_at,
            valid_until=signal.valid_until,
            evidence_independence="HIGH" if independence.strength == "HIGH" else "MEDIUM",
            qlib_rank=getattr(quant, "rank", None),
            qlib_universe_size=getattr(quant, "universe_size", None),
            spread_bps=design.market_quality.spread_bps,
            gap_to_stretch_profit_gbp=max(Decimal(0), StretchObjective().target_profit - upside),
            evidence_ids=design.evidence_ids,
        )


class OpportunityRanking(Contract):
    version: Literal["qualified-scenarios-v1"] = "qualified-scenarios-v1"
    objective: StretchObjective = Field(default_factory=StretchObjective)
    evaluated_at: AwareDatetime
    basis: Literal["RESEARCH_SIMULATION_NOT_ACTUAL_PORTFOLIO"] = (
        "RESEARCH_SIMULATION_NOT_ACTUAL_PORTFOLIO"
    )
    coverage: Literal["PAGINATED_WORKSPACE_PUBLICATIONS"] = "PAGINATED_WORKSPACE_PUBLICATIONS"
    examined: int = Field(ge=0)
    offset: int = Field(ge=0)
    has_more: bool
    opportunities: tuple[OpportunityAssessment, ...]
    conclusion: Literal[
        "NO_QUALIFIED_OPPORTUNITY_CURRENTLY_SUPPORTS_THE_STRETCH_OBJECTIVE",
        "MODELLED_SCENARIO_REACHES_OBJECTIVE_WITHOUT_CALIBRATED_PROBABILITY",
    ]
    order: tuple[str, ...] = (
        "Cost-adjusted risk/reward descending",
        "Modelled downside GBP ascending",
        "Cost-adjusted hypothetical upside GBP descending",
        "Ticker and research ID ascending for reproducible ties",
    )
    limitations: tuple[str, ...] = (
        "Ranked only within this page of this workspace's sealed live publications, not the entire market.",
        "Targets are hypothetical scenarios, not expected returns or calibrated probabilities.",
        "No qualified scenario is evidence that a +500% return will occur.",
        "Alternatives are not a portfolio: allocations must not be added together.",
        "No actual trades, portfolio value, countdown or reinvested compounding is inferred.",
        "Gaps can exceed modelled downside. Investment decisions remain human.",
    )
    hash: str = ""


def rank_opportunities(
    assessments: tuple[OpportunityAssessment, ...], *, now: datetime,
    examined: int, offset: int = 0, has_more: bool = False,
) -> OpportunityRanking:
    """Compare already gated alternatives; the stretch objective never affects ordering."""
    ordered = tuple(sorted(assessments, key=lambda item: (
        -item.risk_reward, item.modelled_downside_gbp, -item.potential_upside_gbp,
        item.ticker, item.research_id,
    )))
    report = OpportunityRanking(
        evaluated_at=now, examined=examined, offset=offset, has_more=has_more,
        opportunities=ordered,
        conclusion=(
            "MODELLED_SCENARIO_REACHES_OBJECTIVE_WITHOUT_CALIBRATED_PROBABILITY"
            if any(item.gap_to_stretch_profit_gbp == 0 for item in ordered)
            else "NO_QUALIFIED_OPPORTUNITY_CURRENTLY_SUPPORTS_THE_STRETCH_OBJECTIVE"
        ),
    )
    return report.model_copy(update={
        "hash": content_hash(report.model_dump(mode="json", exclude={"hash"})),
    })
