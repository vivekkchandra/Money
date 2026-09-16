from datetime import date
from decimal import Decimal

import pytest

from money.research.budgets import research_depth
from money.risk.costs import PTM, CostApplicability, CostRule, estimate_costs


def applicable():
    return CostApplicability(
        sdrt="APPLIES",
        evidence_source="test-company-register",
        broker_round_trip_fee_gbp=Decimal(0),
        broker_fee_source="test-reviewed-fee-schedule",
        other_round_trip_charges_gbp=Decimal(0),
        other_charge_source="test-reviewed-exchange-schedule",
    )


def test_sourced_costs_small_capital_and_expired_rules():
    result = estimate_costs(
        Decimal(200),
        date(2026, 9, 16),
        applicable(),
        spread_bps=Decimal(20),
        slippage_per_side_bps=Decimal(5),
    )
    assert result.tax_gbp == Decimal(1)
    assert result.market_levy_gbp == 0
    assert result.total_round_trip_gbp == Decimal("1.60")
    assert result.total_round_trip_bps == 80
    with pytest.raises(ValueError, match="NOT_EFFECTIVE"):
        estimate_costs(
            Decimal(200),
            date(2025, 1, 1),
            applicable(),
            spread_bps=Decimal(20),
            slippage_per_side_bps=Decimal(5),
        )


def test_unknown_tax_or_charges_never_zero():
    for update in ({"sdrt": "UNKNOWN"}, {"sdrt": "EXEMPT"}, {"broker_round_trip_fee_gbp": None}):
        with pytest.raises(ValueError):
            estimate_costs(
                Decimal(200),
                date(2026, 9, 16),
                applicable().model_copy(update=update),
                spread_bps=Decimal(20),
                slippage_per_side_bps=Decimal(5),
            )


def test_adaptive_depth_rejects_before_llm():
    assert (
        research_depth(deterministic_passed=False, discovery_channels=4, qualified_quant=True)
        == "REJECT"
    )
    assert (
        research_depth(deterministic_passed=True, discovery_channels=0, qualified_quant=True)
        == "DETERMINISTIC_ONLY"
    )
    assert (
        research_depth(deterministic_passed=True, discovery_channels=1, qualified_quant=True)
        == "QUANT_ONLY"
    )


def test_cost_rule_window_and_configured_levy_explanation():
    with pytest.raises(ValueError, match="WINDOW_INVALID"):
        CostRule.model_validate(PTM.model_dump() | {"effective_to": PTM.effective_from})
    changed = CostRule.model_validate(PTM.model_dump() | {"threshold_gbp": Decimal(100)})
    result = estimate_costs(Decimal(200), date(2026, 9, 16), applicable(), spread_bps=Decimal(20), slippage_per_side_bps=Decimal(5), ptm_rule=changed)
    assert result.market_levy_gbp == Decimal(3)
    assert "applies on both sides" in result.assumptions[1]
    assert "£100" in result.assumptions[1]
