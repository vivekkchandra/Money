"""Pure research-outcome calculations; no trading, scheduler, or storage effects."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from decimal import Decimal
from importlib import import_module
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from money.schemas.contracts import Contract, PositiveMoney, Ticker

HORIZONS = (1, 3, 5, 10, 30)


class OutcomeBar(Contract):
    """A complete, normalized GBP bar with provenance and availability time."""

    evidence_id: str = Field(min_length=1)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_at: AwareDatetime
    end_at: AwareDatetime
    available_at: AwareDatetime
    open_gbp: PositiveMoney
    high_gbp: PositiveMoney
    low_gbp: PositiveMoney
    close_gbp: PositiveMoney

    @model_validator(mode="after")
    def valid_bar(self) -> Self:
        if not self.start_at < self.end_at <= self.available_at:
            raise ValueError("bar must be complete before it becomes available")
        if not (
            self.low_gbp
            <= min(self.open_gbp, self.close_gbp)
            <= max(self.open_gbp, self.close_gbp)
            <= self.high_gbp
        ):
            raise ValueError("invalid normalized OHLC range")
        return self


class OutcomeSpecification(Contract):
    research_id: str = Field(min_length=1)
    ticker: Ticker
    issued_at: AwareDatetime
    reference_price_gbp: PositiveMoney
    target_gbp: PositiveMoney | None = None
    invalidation_gbp: PositiveMoney | None = None
    maximum_endpoint_age_hours: int = Field(default=24, ge=0, le=72)

    @model_validator(mode="after")
    def level_order(self) -> Self:
        if self.target_gbp is not None and self.target_gbp <= self.reference_price_gbp:
            raise ValueError("long research target must exceed its reference price")
        if self.invalidation_gbp is not None and self.invalidation_gbp >= self.reference_price_gbp:
            raise ValueError("long research invalidation must be below its reference price")
        return self


class HorizonOutcome(Contract):
    calendar_days: int
    deadline: AwareDatetime
    state: Literal["OBSERVED", "NOT_MATURED", "MISSING_DATA"]
    return_fraction: Decimal | None = None
    price_observed_at: AwareDatetime | None = None
    known_at: AwareDatetime | None = None
    evidence_id: str | None = None


class LevelOccurrence(Contract):
    """First observed touch interval, never an invented exact intrabar time."""

    evidence_id: str
    interval_start: AwareDatetime
    interval_end: AwareDatetime
    known_at: AwareDatetime
    earliest_seconds_after_issue: float
    latest_seconds_after_issue: float


class ResearchOutcome(Contract):
    research_id: str
    ticker: Ticker
    basis: Literal["PUBLISHED_RESEARCH_REFERENCE"] = "PUBLISHED_RESEARCH_REFERENCE"
    issued_at: AwareDatetime
    evaluated_at: AwareDatetime
    reference_price_gbp: PositiveMoney
    target_gbp: PositiveMoney | None
    invalidation_gbp: PositiveMoney | None
    maximum_endpoint_age_hours: int
    horizons: tuple[HorizonOutcome, ...]
    mfe_fraction: Decimal | None
    mae_fraction: Decimal | None
    first_observed_target: LevelOccurrence | None
    first_observed_invalidation: LevelOccurrence | None
    event_order: Literal[
        "TARGET_FIRST",
        "INVALIDATION_FIRST",
        "SAME_BAR_AMBIGUOUS",
        "TARGET_ONLY",
        "INVALIDATION_ONLY",
        "NONE_OBSERVED",
    ]
    observation_count: int = Field(ge=0)
    source_hashes: tuple[str, ...]
    limitations: tuple[str, ...] = (
        "Research-reference price changes are not executed-trade returns and exclude costs.",
        "Extrema and first touches cover supplied complete bars only; missing intervals are unknown.",
        "Horizon endpoints use the latest close no later than the calendar deadline within the stated age limit.",
    )


def _touch(bar: OutcomeBar, issued_at: datetime) -> LevelOccurrence:
    return LevelOccurrence(
        evidence_id=bar.evidence_id,
        interval_start=bar.start_at,
        interval_end=bar.end_at,
        known_at=bar.available_at,
        earliest_seconds_after_issue=(bar.start_at - issued_at).total_seconds(),
        latest_seconds_after_issue=(bar.end_at - issued_at).total_seconds(),
    )


def calculate_outcome(
    specification: OutcomeSpecification, bars: tuple[OutcomeBar, ...], as_of: datetime
) -> ResearchOutcome:
    """Calculate 1/3/5/10/30-calendar-day outcomes from data available by as_of.

    Bars spanning issuance are excluded: their highs/lows may precede the signal.
    Endpoints may carry back only by the explicit maximum_endpoint_age_hours;
    zero requires an exact endpoint. No next-session/future price is substituted.
    """
    spec = OutcomeSpecification.model_validate_json(specification.model_dump_json())
    if as_of.tzinfo is None or as_of.utcoffset() is None or as_of < spec.issued_at:
        raise ValueError("evaluation time must be timezone-aware and no earlier than issuance")
    checked = sorted(
        (OutcomeBar.model_validate_json(bar.model_dump_json()) for bar in bars),
        key=lambda bar: bar.start_at,
    )
    if len({bar.evidence_id for bar in checked}) != len(checked):
        raise ValueError("duplicate outcome evidence identity")
    if any(right.start_at < left.end_at for left, right in zip(checked, checked[1:], strict=False)):
        raise ValueError("overlapping outcome bars are ambiguous")
    final_deadline = spec.issued_at + timedelta(days=30)
    usable = tuple(
        bar
        for bar in checked
        if (
            bar.start_at >= spec.issued_at
            and bar.end_at <= min(as_of, final_deadline)
            and bar.available_at <= as_of
        )
    )
    horizons = []
    for days in HORIZONS:
        deadline = spec.issued_at + timedelta(days=days)
        if as_of < deadline:
            horizons.append(
                HorizonOutcome(calendar_days=days, deadline=deadline, state="NOT_MATURED")
            )
            continue
        eligible = [
            bar
            for bar in usable
            if bar.end_at <= deadline
            and (deadline - bar.end_at <= timedelta(hours=spec.maximum_endpoint_age_hours))
        ]
        if not eligible:
            horizons.append(
                HorizonOutcome(calendar_days=days, deadline=deadline, state="MISSING_DATA")
            )
            continue
        endpoint = eligible[-1]
        horizons.append(
            HorizonOutcome(
                calendar_days=days,
                deadline=deadline,
                state="OBSERVED",
                return_fraction=endpoint.close_gbp / spec.reference_price_gbp - 1,
                price_observed_at=endpoint.end_at,
                known_at=endpoint.available_at,
                evidence_id=endpoint.evidence_id,
            )
        )
    target_bar = next(
        (
            bar
            for bar in usable
            if (spec.target_gbp is not None and bar.high_gbp >= spec.target_gbp)
        ),
        None,
    )
    failure_bar = next(
        (
            bar
            for bar in usable
            if (spec.invalidation_gbp is not None and bar.low_gbp <= spec.invalidation_gbp)
        ),
        None,
    )
    order: Literal[
        "TARGET_FIRST",
        "INVALIDATION_FIRST",
        "SAME_BAR_AMBIGUOUS",
        "TARGET_ONLY",
        "INVALIDATION_ONLY",
        "NONE_OBSERVED",
    ] = "NONE_OBSERVED"
    if target_bar and failure_bar:
        if target_bar.evidence_id == failure_bar.evidence_id:
            order = "SAME_BAR_AMBIGUOUS"
        else:
            order = (
                "TARGET_FIRST" if target_bar.end_at < failure_bar.end_at else "INVALIDATION_FIRST"
            )
    elif target_bar:
        order = "TARGET_ONLY"
    elif failure_bar:
        order = "INVALIDATION_ONLY"
    return ResearchOutcome(
        research_id=spec.research_id,
        ticker=spec.ticker,
        issued_at=spec.issued_at,
        evaluated_at=as_of,
        reference_price_gbp=spec.reference_price_gbp,
        target_gbp=spec.target_gbp,
        invalidation_gbp=spec.invalidation_gbp,
        maximum_endpoint_age_hours=spec.maximum_endpoint_age_hours,
        horizons=tuple(horizons),
        mfe_fraction=max(Decimal(0), max(b.high_gbp for b in usable) / spec.reference_price_gbp - 1)
        if usable
        else None,
        mae_fraction=min(Decimal(0), min(b.low_gbp for b in usable) / spec.reference_price_gbp - 1)
        if usable
        else None,
        first_observed_target=_touch(target_bar, spec.issued_at) if target_bar else None,
        first_observed_invalidation=_touch(failure_bar, spec.issued_at) if failure_bar else None,
        event_order=order,
        observation_count=len(usable),
        source_hashes=tuple(bar.evidence_hash for bar in usable),
    )


class QuantStatsSummary(Contract):
    observations: int
    periods_per_year: int
    annual_risk_free_rate: float
    sharpe: float
    maximum_drawdown: float


def quantstats_summary(
    returns: tuple[float, ...], *, periods_per_year: int, annual_risk_free_rate: float = 0.0
) -> QuantStatsSummary:
    """Optional native metrics for an evenly spaced, ordered research return series.

    Callers must align the calendar and missing observations before invocation.
    ImportError means the optional dependency is unavailable, not zero performance.
    """
    if (
        periods_per_year < 1
        or not math.isfinite(annual_risk_free_rate)
        or annual_risk_free_rate <= -1
    ):
        raise ValueError("explicit frequency and finite risk-free assumptions are required")
    if len(returns) < 3 or any(not math.isfinite(value) or value <= -1 for value in returns):
        raise ValueError("at least three finite returns greater than -1 are required")
    if len(set(returns)) < 2:
        raise ValueError("constant returns cannot support a finite Sharpe estimate")
    pandas = import_module("pandas")
    stats = import_module("quantstats.stats")
    series = pandas.Series(
        returns, index=pandas.date_range("2000-01-01", periods=len(returns), freq="D")
    )
    sharpe = float(stats.sharpe(series, rf=annual_risk_free_rate, periods=periods_per_year))
    equity_returns = pandas.Series(
        (0.0, *returns), index=pandas.date_range("2000-01-01", periods=len(returns) + 1, freq="D")
    )
    # An explicit 100 baseline prevents native return/price heuristics from
    # misclassifying an equity curve wholly below 1 after an initial loss.
    equity = (1 + equity_returns).cumprod() * 100
    drawdown = float(stats.max_drawdown(equity))
    if not math.isfinite(sharpe) or not math.isfinite(drawdown):
        raise ValueError("QuantStats returned non-finite metrics")
    return QuantStatsSummary(
        observations=len(returns),
        periods_per_year=periods_per_year,
        annual_risk_free_rate=annual_risk_free_rate,
        sharpe=sharpe,
        maximum_drawdown=drawdown,
    )
