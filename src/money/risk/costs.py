"""Versioned illustrative UK costs; applicability must be evidenced per instrument."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import Field, model_validator

from money.schemas.contracts import Contract


class CostRule(Contract):
    rule_id: str = Field(min_length=1)
    effective_from: date
    effective_to: date
    source: str = Field(min_length=1)
    applicability: str = Field(min_length=1)
    rate: Decimal = Field(default=Decimal(0), ge=0)
    fixed_gbp: Decimal = Field(default=Decimal(0), ge=0)
    threshold_gbp: Decimal = Field(default=Decimal(0), ge=0)

    @model_validator(mode="after")
    def valid_window(self) -> "CostRule":
        if self.effective_to <= self.effective_from:
            raise ValueError("COST_RULE_WINDOW_INVALID")
        return self


# This is a bounded research-rule verification window, NOT a claim that the law
# began on this date or will remain unchanged indefinitely. Past/future periods
# require their own sourced rule version. No current rule is backfilled in time.
SDRT = CostRule(
    rule_id="hmrc-sdrt-verified-2026-09-16",
    effective_from=date(2026, 9, 16),
    effective_to=date(2026, 10, 16),
    source="https://www.gov.uk/hmrc-internal-manuals/stamp-taxes-shares-manual/stsm031030",
    applicability="Electronic transfer of chargeable securities; instrument-specific exemption review required",
    rate=Decimal("0.005"),
)
PTM = CostRule(
    rule_id="ptm-verified-2026-09-16",
    effective_from=date(2026, 9, 16),
    effective_to=date(2026, 10, 16),
    source="https://www.thetakeoverpanel.org.uk/disclosure/ptm-levy",
    applicability="Relevant securities with consideration greater than £10,000",
    fixed_gbp=Decimal("1.50"),
    threshold_gbp=Decimal("10000"),
)


class CostApplicability(Contract):
    sdrt: Literal["APPLIES", "EXEMPT", "UNKNOWN"]
    evidence_source: str = Field(min_length=1)
    exemption_reason: str | None = None
    broker_round_trip_fee_gbp: Decimal | None = Field(default=None, ge=0)
    broker_fee_source: str | None = None
    other_round_trip_charges_gbp: Decimal | None = Field(default=None, ge=0)
    other_charge_source: str | None = None


class ResearchCostEstimate(Contract):
    version: str
    notional_gbp: Decimal
    spread_bps: Decimal
    slippage_per_side_bps: Decimal
    tax_gbp: Decimal
    market_levy_gbp: Decimal
    total_round_trip_gbp: Decimal
    total_round_trip_bps: Decimal
    assumptions: tuple[str, ...]
    sources: tuple[str, ...]


def estimate_costs(
    notional_gbp: Decimal,
    on_date: date,
    applicability: CostApplicability,
    *,
    spread_bps: Decimal,
    slippage_per_side_bps: Decimal,
    sdrt_rule: CostRule = SDRT,
    ptm_rule: CostRule = PTM,
) -> ResearchCostEstimate:
    numbers = (notional_gbp, spread_bps, slippage_per_side_bps)
    if (
        any(not v.is_finite() for v in numbers)
        or not 0 < notional_gbp <= 200
        or min(spread_bps, slippage_per_side_bps) < 0
    ):
        raise ValueError("COST_INPUT_INVALID")
    if any(
        not rule.effective_from <= on_date < rule.effective_to for rule in (sdrt_rule, ptm_rule)
    ):
        raise ValueError("COST_RULE_NOT_EFFECTIVE")
    if applicability.sdrt == "UNKNOWN" or (
        applicability.sdrt == "EXEMPT" and not applicability.exemption_reason
    ):
        raise ValueError("COST_APPLICABILITY_UNKNOWN")
    if (
        applicability.broker_round_trip_fee_gbp is None
        or not applicability.broker_fee_source
        or applicability.other_round_trip_charges_gbp is None
        or not applicability.other_charge_source
    ):
        raise ValueError("COST_COVERAGE_MISSING")
    tax = (
        (notional_gbp * sdrt_rule.rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if applicability.sdrt == "APPLIES"
        else Decimal(0)
    )
    levy = 2 * ptm_rule.fixed_gbp if notional_gbp > ptm_rule.threshold_gbp else Decimal(0)
    total = (
        tax
        + levy
        + notional_gbp * (spread_bps + 2 * slippage_per_side_bps) / 10000
        + applicability.broker_round_trip_fee_gbp
        + applicability.other_round_trip_charges_gbp
    )
    return ResearchCostEstimate(
        version=f"{sdrt_rule.rule_id}/{ptm_rule.rule_id}",
        notional_gbp=notional_gbp,
        spread_bps=spread_bps,
        slippage_per_side_bps=slippage_per_side_bps,
        tax_gbp=tax,
        market_levy_gbp=levy,
        total_round_trip_gbp=total,
        total_round_trip_bps=total / notional_gbp * 10000,
        assumptions=(
            "One full spread and slippage on both sides; hypothetical research only.",
            f"PTM levy {'applies on both sides' if levy else 'does not apply'} at £{notional_gbp}: configured threshold is £{ptm_rule.threshold_gbp}.",
            applicability.exemption_reason or "SDRT applies based on supplied instrument evidence.",
        ),
        sources=(
            sdrt_rule.source,
            ptm_rule.source,
            applicability.evidence_source,
            applicability.broker_fee_source,
            applicability.other_charge_source,
        ),
    )
