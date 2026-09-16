"""Deterministic market quality and liquidity policy, independent of LLM output."""

from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Literal

from pydantic import Field

from money.data.normalization.prices import normalize_gbp
from money.schemas.contracts import Contract, PriceBar, ResearchSnapshot


class MarketQualityPolicy(Contract):
    version: str = "uk-daily-conservative-v1"
    minimum_bars: int = Field(default=60, ge=30, le=5000)
    maximum_latest_age_hours: int = Field(default=96, ge=1, le=168)
    maximum_zero_volume_bars: int = Field(default=0, ge=0, le=5)
    minimum_average_daily_value_gbp: Decimal = Field(default=Decimal("100000"), gt=0)
    maximum_spread_bps: Decimal = Field(default=Decimal("100"), gt=0, le=1000)
    maximum_one_day_move: Decimal = Field(default=Decimal("0.5"), gt=0, le=1)
    rationale: str = "Conservative research policy: 60 observations, £100k daily value and 1% spread; policy thresholds require empirical review and are not claims of calibrated optimality."


class MarketQuality(Contract):
    policy_version: str
    passed: bool
    reasons: tuple[str, ...]
    observations: int
    average_daily_value_gbp: Decimal | None
    median_daily_volume: Decimal | None
    spread_bps: Decimal | None
    adjustment_basis: str


def evaluate_market_quality(
    snapshot: ResearchSnapshot,
    now: datetime,
    *,
    spread_bps: Decimal | None,
    adjustment_basis: Literal["RAW", "SPLIT_ADJUSTED", "TOTAL_RETURN", "UNKNOWN"],
    corporate_actions_complete: bool,
    policy: MarketQualityPolicy | None = None,
) -> MarketQuality:
    policy = policy or MarketQualityPolicy()
    reasons = []
    records = [e for e in snapshot.evidence if isinstance(e.payload, PriceBar)]
    ordered = sorted(records, key=lambda e: e.observation_time)
    if snapshot.created_at > now or any(not r.available_at(snapshot.price_cutoff) for r in records):
        reasons.append("PIT_VIOLATION")
    if any(r.conflicting for r in records):
        reasons.append("CRITICAL_DATA_CONFLICT")
    if any(r.fresh_until <= now for r in records):
        reasons.append("CRITICAL_DATA_STALE")
    if records != ordered:
        reasons.append("NON_MONOTONIC_PRICES")
    if len({r.observation_time for r in records}) != len(records):
        reasons.append("DUPLICATE_PRICE_TIMESTAMP")
    if len(records) < policy.minimum_bars:
        reasons.append("INSUFFICIENT_PRICE_OBSERVATIONS")
    if not records or now - ordered[-1].observation_time > timedelta(
        hours=policy.maximum_latest_age_hours
    ):
        reasons.append("CRITICAL_DATA_STALE")
    bars = [e.payload for e in ordered if isinstance(e.payload, PriceBar)]
    if any(b.currency != snapshot.instrument.quote_currency for b in bars):
        reasons.append("PRICE_CURRENCY_MISMATCH")
    if adjustment_basis == "UNKNOWN" or not corporate_actions_complete:
        reasons.append("CORPORATE_ACTION_COVERAGE_UNKNOWN")
    if (
        any(e.payload.kind == "corporate_action" for e in snapshot.evidence)
        and adjustment_basis == "RAW"
    ):
        reasons.append("CORPORATE_ACTION_REQUIRES_ADJUSTMENT_REVIEW")
    values = [normalize_gbp(b.close, b.currency).gbp * b.volume for b in bars[-20:]]
    average = sum(values, Decimal(0)) / len(values) if values else None
    if average is None or average < policy.minimum_average_daily_value_gbp:
        reasons.append("LIQUIDITY_INSUFFICIENT")
    if sum(b.volume == 0 for b in bars[-20:]) > policy.maximum_zero_volume_bars:
        reasons.append("ZERO_VOLUME_BARS")
    if spread_bps is None:
        reasons.append("SPREAD_EVIDENCE_MISSING")
    elif not spread_bps.is_finite():
        reasons.append("SPREAD_EVIDENCE_INVALID")
        spread_bps = None
    elif spread_bps < 0 or spread_bps > policy.maximum_spread_bps:
        reasons.append("SPREAD_EXCEEDS_POLICY")
    if any(
        abs(right.close / left.close - 1) > policy.maximum_one_day_move
        for left, right in zip(bars, bars[1:], strict=False)
    ):
        reasons.append("EXTREME_PRICE_MOVE_REQUIRES_REVIEW")
    return MarketQuality(
        policy_version=policy.version,
        passed=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        observations=len(bars),
        average_daily_value_gbp=average,
        median_daily_volume=Decimal(str(median(b.volume for b in bars[-20:]))) if bars else None,
        spread_bps=spread_bps,
        adjustment_basis=adjustment_basis,
    )
