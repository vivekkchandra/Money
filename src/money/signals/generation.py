"""Deterministic hypothetical research scenarios after every evidence gate passes."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Literal

from pydantic import AwareDatetime, Field

from money.data.normalization.prices import normalize_gbp
from money.data.quality.market import MarketQuality, evaluate_market_quality
from money.policy.governance import consensus, evidence_independence
from money.risk.costs import CostApplicability, ResearchCostEstimate, estimate_costs
from money.scanner.technical import calculate_technical
from money.schemas.contracts import (
    CIOAuditReport,
    Contract,
    FirmReport,
    LeanValidationReport,
    PriceBar,
    RedTeamReport,
    ResearchMandate,
    ResearchSignal,
    ResearchSnapshot,
    ResearchState,
    content_hash,
)


class SignalPolicy(Contract):
    version: str = "atr-research-scenarios-v1"
    maximum_allocation_gbp: Decimal = Field(default=Decimal("200"), gt=0, le=200)
    maximum_downside_gbp: Decimal = Field(default=Decimal("4"), gt=0, le=4)
    maximum_downside_fraction: Decimal = Field(default=Decimal("0.02"), gt=0, le=Decimal("0.02"))
    entry_half_width_atr: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)
    invalidation_atr: Decimal = Field(default=Decimal("2"), ge=1, le=5)
    target_atr: Decimal = Field(default=Decimal("6"), ge=2, le=10)
    minimum_risk_reward: Decimal = Field(default=Decimal("1.5"), ge=Decimal("1.5"), le=10)
    horizon_days: int = Field(default=5, ge=1, le=30)
    rationale: str = "Illustrative ATR scenarios: 2 ATR invalidation, 6 ATR target, quarter-ATR entry interval, at most 2%/£4 modelled capital downside including sourced costs. These conservative policy limits are not empirically calibrated forecasts."


class SignalDesign(Contract):
    policy_version: str
    policy: SignalPolicy | None = None
    market_quality: MarketQuality | None = None
    cost_applicability: CostApplicability | None = None
    designed_at: AwareDatetime | None = None
    signal: ResearchSignal | None = None
    reasons: tuple[str, ...] = ()
    current_raw: Decimal | None = None
    current_currency: Literal["GBP", "GBX"] | None = None
    current_gbp: Decimal | None = None
    entry_low_gbp: Decimal | None = None
    entry_high_gbp: Decimal | None = None
    invalidation_gbp: Decimal | None = None
    targets_gbp: tuple[Decimal, ...] = ()
    atr_gbp: Decimal | None = None
    costs: ResearchCostEstimate | None = None
    risk_reward: Decimal | None = None
    confidence: Literal["uncalibrated"] = "uncalibrated"
    normalization_method: Literal["GBP unchanged; GBX divided by 100"] = (
        "GBP unchanged; GBX divided by 100"
    )
    evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = (
        "ATR-derived levels are hypothetical research scenarios, not price predictions or execution instructions.",
        "Illustrative allocation uses the mandate's assumed capital, never a broker balance or position.",
        "Modelled downside is a scenario, not a guaranteed loss limit; gaps can exceed it.",
        "No calibrated probability or strong-candidate status is available.",
    )


def generate_signal(
    research_id: str,
    mandate: ResearchMandate,
    snapshot: ResearchSnapshot,
    reports: tuple[FirmReport, ...],
    lean: LeanValidationReport,
    audit: CIOAuditReport,
    red_team: RedTeamReport,
    *,
    state: ResearchState,
    issued_at: datetime,
    market_quality: MarketQuality,
    cost_applicability: CostApplicability,
    policy: SignalPolicy | None = None,
    rounds: int = 0,
) -> SignalDesign:
    policy = SignalPolicy.model_validate_json((policy or SignalPolicy()).model_dump_json())

    def unavailable(*reasons: str) -> SignalDesign:
        return SignalDesign(
            policy_version=policy.version,
            reasons=tuple(reasons),
            policy=policy,
            market_quality=market_quality,
            cost_applicability=cost_applicability,
            designed_at=issued_at,
        )

    if state not in {ResearchState.RESEARCH_CANDIDATE, ResearchState.WATCH}:
        return unavailable("SIGNAL_STATE_NOT_ADMISSIBLE")
    expected, failures = consensus(
        mandate,
        snapshot,
        reports,
        lean,
        audit,
        red_team,
        evidence_independence(snapshot, reports),
        issued_at,
        rounds=rounds,
    )
    if expected != state:
        return unavailable("SIGNAL_CONSENSUS_MISMATCH", *failures)
    if (
        lean.scenario_policy_hash != content_hash(policy)
        or not mandate.minimum_horizon_days <= policy.horizon_days <= mandate.maximum_horizon_days
    ):
        # A generic historical PASS cannot qualify different displayed scenarios.
        # Never silently clamp the validated horizon to a different mandate horizon.
        return unavailable("LEAN_SCENARIO_POLICY_UNQUALIFIED")
    if not market_quality.passed or market_quality.spread_bps is None:
        return unavailable("MARKET_QUALITY_NOT_QUALIFIED", *market_quality.reasons)
    basis = market_quality.adjustment_basis
    if basis not in {"RAW", "SPLIT_ADJUSTED", "TOTAL_RETURN"}:
        return unavailable("CORPORATE_ACTION_COVERAGE_UNKNOWN")
    # Recheck current freshness/liquidity; an earlier successful quality artifact
    # must not make an old snapshot eligible when signal publication occurs later.
    checked_quality = evaluate_market_quality(
        snapshot,
        issued_at,
        spread_bps=market_quality.spread_bps,
        adjustment_basis="RAW"
        if basis == "RAW"
        else "SPLIT_ADJUSTED"
        if basis == "SPLIT_ADJUSTED"
        else "TOTAL_RETURN",
        corporate_actions_complete=True,
    )
    if not checked_quality.passed:
        return unavailable("MARKET_QUALITY_EXPIRED_OR_CHANGED", *checked_quality.reasons)
    if lean.spread_bps is None or lean.slippage_bps is None:
        return unavailable("TRANSACTION_COST_EVIDENCE_MISSING")
    spread = Decimal(str(lean.spread_bps))
    slippage = Decimal(str(lean.slippage_bps))
    if spread != market_quality.spread_bps:
        return unavailable("VALIDATION_SPREAD_MISMATCH")
    try:
        technical = calculate_technical(snapshot)
    except (ValueError, ImportError):
        return unavailable("TECHNICAL_SCENARIO_UNAVAILABLE")
    if not technical.available:
        return unavailable(*technical.limitations)
    precision = Decimal("0.000001")
    atr = Decimal(str(dict(technical.values)["atr_14_gbp"])).quantize(precision)
    prices = sorted(
        (e for e in snapshot.evidence if isinstance(e.payload, PriceBar)),
        key=lambda record: record.observation_time,
    )
    latest = prices[-1].payload
    assert isinstance(latest, PriceBar)
    price = normalize_gbp(latest.close, latest.currency).gbp
    low = (price - policy.entry_half_width_atr * atr).quantize(precision, rounding=ROUND_FLOOR)
    high = (price + policy.entry_half_width_atr * atr).quantize(precision, rounding=ROUND_CEILING)
    invalidation = (price - policy.invalidation_atr * atr).quantize(precision, rounding=ROUND_FLOOR)
    target = (price + policy.target_atr * atr).quantize(precision, rounding=ROUND_FLOOR)
    if atr <= 0 or invalidation <= 0 or not invalidation < low <= high < target:
        return unavailable("SCENARIO_LEVELS_INVALID")
    capital = mandate.maximum_capital_gbp
    downside_limit = min(policy.maximum_downside_gbp, capital * policy.maximum_downside_fraction)
    maximum = min(capital, policy.maximum_allocation_gbp)
    # Search whole pennies of illustrative capital. No share quantity or executable
    # order object is constructed; fixed fees and tax penny-rounding stay included.
    left, right = 1, int((maximum * 100).to_integral_value(rounding=ROUND_FLOOR))
    best: tuple[Decimal, Decimal, ResearchCostEstimate] | None = None
    try:
        while left <= right:
            middle = (left + right) // 2
            allocation = Decimal(middle) / 100
            costs = estimate_costs(
                allocation,
                issued_at.date(),
                cost_applicability,
                spread_bps=spread,
                slippage_per_side_bps=slippage,
            )
            downside = allocation * (high - invalidation) / high + costs.total_round_trip_gbp
            if downside <= downside_limit:
                best = allocation, downside, costs
                left = middle + 1
            else:
                right = middle - 1
    except ValueError as error:
        # Cost errors have bounded stable Money-owned codes, never provider responses.
        return unavailable(str(error))
    if best is None:
        return unavailable("ILLUSTRATIVE_RISK_BUDGET_INSUFFICIENT")
    allocation, downside, costs = best
    reward = allocation * (target - high) / high - costs.total_round_trip_gbp
    risk_reward = reward / downside
    if risk_reward < policy.minimum_risk_reward:
        return unavailable("COST_ADJUSTED_RISK_REWARD_INSUFFICIENT")
    horizon = policy.horizon_days
    valid_until = min(
        issued_at + timedelta(days=horizon),
        snapshot.instrument.verified_at + timedelta(hours=24),
        *(e.fresh_until for e in snapshot.evidence if e.critical),
    )
    if valid_until <= issued_at:
        return unavailable("SIGNAL_EVIDENCE_ALREADY_EXPIRED")
    divisor = Decimal(100) if latest.currency == "GBX" else Decimal(1)
    signal = ResearchSignal(
        research_id=research_id,
        ticker=snapshot.ticker,
        state="WATCH" if state == ResearchState.WATCH else "RESEARCH_CANDIDATE",
        issued_at=issued_at,
        valid_until=valid_until,
        entry_low=low * divisor,
        entry_high=high * divisor,
        quote_currency=latest.currency,
        potential_targets=(target * divisor,),
        invalidation_conditions=(
            f"Hypothetical price invalidation at {invalidation * divisor} {latest.currency}.",
            "Material source contradiction or ethical/ISA eligibility change.",
        ),
        event_invalidators=(
            "New corporate action, dilution, or material adverse filing.",
            "Critical evidence becomes stale, conflicted, or unavailable.",
        ),
        assumed_capital_gbp=capital,
        illustrative_allocation_gbp=allocation,
        modelled_downside_gbp=downside,
        horizon_days=horizon,
    )
    return SignalDesign(
        policy_version=policy.version,
        policy=policy,
        market_quality=market_quality,
        cost_applicability=cost_applicability,
        designed_at=issued_at,
        signal=signal,
        current_raw=latest.close,
        current_currency=latest.currency,
        current_gbp=price,
        entry_low_gbp=low,
        entry_high_gbp=high,
        invalidation_gbp=invalidation,
        targets_gbp=(target,),
        atr_gbp=atr,
        costs=costs,
        risk_reward=risk_reward,
        evidence_ids=tuple(record.evidence_id for record in prices),
    )


# These codes can only suppress a positive research state. They cannot create a
# signal, bypass a veto or turn an insufficient state into a positive candidate.
DOWNGRADE_REASONS = frozenset(
    {
        "SPREAD_EVIDENCE_STALE",
        "LEAN_SCENARIO_POLICY_UNQUALIFIED",
        "MARKET_QUALITY_NOT_QUALIFIED",
        "MARKET_QUALITY_EXPIRED_OR_CHANGED",
        "TRANSACTION_COST_EVIDENCE_MISSING",
        "VALIDATION_SPREAD_MISMATCH",
        "TECHNICAL_SCENARIO_UNAVAILABLE",
        "TA_LIB_WARMUP_INSUFFICIENT",
        "SCENARIO_LEVELS_INVALID",
        "COST_INPUT_INVALID",
        "COST_RULE_NOT_EFFECTIVE",
        "COST_APPLICABILITY_UNKNOWN",
        "COST_COVERAGE_MISSING",
        "ILLUSTRATIVE_RISK_BUDGET_INSUFFICIENT",
        "COST_ADJUSTED_RISK_REWARD_INSUFFICIENT",
        "SIGNAL_EVIDENCE_ALREADY_EXPIRED",
        "PIT_VIOLATION",
        "CRITICAL_DATA_CONFLICT",
        "CRITICAL_DATA_STALE",
        "NON_MONOTONIC_PRICES",
        "DUPLICATE_PRICE_TIMESTAMP",
        "INSUFFICIENT_PRICE_OBSERVATIONS",
        "PRICE_CURRENCY_MISMATCH",
        "CORPORATE_ACTION_COVERAGE_UNKNOWN",
        "CORPORATE_ACTION_REQUIRES_ADJUSTMENT_REVIEW",
        "LIQUIDITY_INSUFFICIENT",
        "ZERO_VOLUME_BARS",
        "SPREAD_EVIDENCE_MISSING",
        "SPREAD_EVIDENCE_INVALID",
        "SPREAD_EXCEEDS_POLICY",
        "EXTREME_PRICE_MOVE_REQUIRES_REVIEW",
    }
)
