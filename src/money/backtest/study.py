"""Fixed, order-free historical hypothesis measurements used inside LEAN.

This is a predeclared momentum study, not an execution strategy. It records
hypothetical returns and first threshold observations; no positions or orders
are created. Thresholds and costs arrive in an immutable, versioned study input.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any


def _atr14(bars: list[dict[str, Any]]) -> list[float | None]:
    # Wilder ATR14 matches the native technical scanner's seed and recursion.
    # Every used bar is still subject to its original publication clock below.
    atr_values: list[float | None] = [None] * len(bars)
    tr_values = [
        max(
            float(bars[i]["high"]) - float(bars[i]["low"]),
            abs(float(bars[i]["high"]) - float(bars[i - 1]["close"])),
            abs(float(bars[i]["low"]) - float(bars[i - 1]["close"])),
        )
        for i in range(1, len(bars))
    ]
    if len(bars) > 14:
        atr_values[14] = sum(tr_values[:14]) / 14
        for i in range(15, len(bars)):
            previous_atr = atr_values[i - 1]
            assert previous_atr is not None
            atr_values[i] = (previous_atr * 13 + tr_values[i - 1]) / 14
    return atr_values


def _evaluate_study(bars: list[dict[str, Any]], parameters: dict[str, Any]) -> dict[str, Any]:
    horizon = int(parameters["horizon_days"])
    target = float(parameters["target_fraction"])
    invalidation = float(parameters["invalidation_fraction"])
    threshold = float(parameters["momentum_threshold"])
    cost = float(parameters["round_trip_cost_bps"]) / 10000
    scenario = parameters.get("scenario_policy")
    if not 1 <= horizon <= 30 or not 0 < target <= 1 or not 0 < invalidation < 1:
        raise ValueError("invalid fixed study parameters")
    if not math.isfinite(cost) or not 0 <= cost < 1:
        raise ValueError("invalid research cost assumption")
    if any(bars[i]["time"] >= bars[i + 1]["time"] for i in range(len(bars) - 1)):
        raise ValueError("study data must be unique and chronological")
    atr_values = _atr14(bars)
    timestamps = [datetime.fromisoformat(row["time"]) for row in bars]
    reserved_start = max(61, int(len(bars) * 0.6))
    cases: list[dict[str, Any]] = []
    for index in range(reserved_start, len(bars) - horizon, horizon):
        past = bars[index - 20 : index]
        regime_past = bars[index - 60 : index]
        # Data become usable only after their actual publication time. The
        # hypothesis is evaluated at the next session's open, never same-close.
        decision_time = bars[index]["time"]
        required_past = bars[:index] if scenario else regime_past
        if any(row["available_at"] > decision_time for row in required_past):
            continue
        momentum = float(past[-1]["close"]) / float(past[0]["close"]) - 1
        if momentum <= threshold:
            continue
        entry = float(bars[index]["open"])
        if entry <= 0:
            raise ValueError("non-positive historical reference value")
        target_value, invalidation_value = entry * (1 + target), entry * (1 - invalidation)
        if scenario:
            atr = atr_values[index - 1]
            if atr is None or atr <= 0:
                continue
            precision = Decimal("0.000001")
            reference = Decimal(str(past[-1]["close"]))
            atr_decimal = Decimal(str(atr)).quantize(precision)
            half_width = Decimal(str(scenario["entry_half_width_atr"])) * atr_decimal
            entry_low = (reference - half_width).quantize(precision, rounding=ROUND_FLOOR)
            entry_high = (reference + half_width).quantize(precision, rounding=ROUND_CEILING)
            # Observe next-session open only; no assumed intraday limit fill.
            if not entry_low <= Decimal(str(entry)) <= entry_high:
                continue
            target_value = float(
                (reference + Decimal(str(scenario["target_atr"])) * atr_decimal).quantize(
                    precision, rounding=ROUND_FLOOR
                )
            )
            invalidation_value = float(
                (reference - Decimal(str(scenario["invalidation_atr"])) * atr_decimal).quantize(
                    precision, rounding=ROUND_FLOOR
                )
            )
            if not 0 < invalidation_value < entry < target_value:
                continue
        horizon_end = timestamps[index] + timedelta(days=horizon)
        if timestamps[-1] < horizon_end:
            continue
        # Product horizons are calendar days, never silently trading sessions.
        future = bars[index : bisect_left(timestamps, horizon_end)]
        mae = min(float(row["low"]) / entry - 1 for row in future)
        mfe = max(float(row["high"]) / entry - 1 for row in future)
        outcome = float(future[-1]["close"]) / entry - 1
        target_day: int | None = None
        invalidation_day: int | None = None
        for row in future:
            day = (datetime.fromisoformat(row["time"]) - timestamps[index]).days + 1
            if float(row["low"]) <= invalidation_value:
                invalidation_day = day
                # On an ambiguous daily bar, invalidation precedes target. Gap
                # below threshold uses the observed open, not an optimistic fill.
                outcome = min(float(row["open"]) / entry - 1, invalidation_value / entry - 1)
                break
            if float(row["high"]) >= target_value:
                target_day, outcome = day, target_value / entry - 1
                break
        case_return = outcome - cost
        cases.append(
            {
                "time": decision_time,
                "return": case_return,
                "mae": mae,
                "mfe": mfe,
                "target_day": target_day,
                "invalidation_day": invalidation_day,
                "regime": "positive_slow_trend"
                if float(regime_past[-1]["close"]) > float(regime_past[0]["close"])
                else "nonpositive_slow_trend",
            }
        )
    returns = [row["return"] for row in cases]
    value = peak = 1.0
    maximum_drawdown = 0.0
    for change in returns:
        value *= max(0.0, 1 + change)
        peak = max(peak, value)
        maximum_drawdown = max(maximum_drawdown, (peak - value) / peak)
    folds = []
    for fold in range(3):
        section = returns[len(returns) * fold // 3 : len(returns) * (fold + 1) // 3]
        folds.append(
            {
                "observations": len(section),
                "mean_return": sum(section) / len(section) if section else None,
            }
        )
    return {
        "observations": len(cases),
        "bars_received": len(bars),
        "mean_return": sum(returns) / len(returns) if returns else None,
        "maximum_drawdown": maximum_drawdown,
        "mae": min((row["mae"] for row in cases), default=None),
        "mfe": max((row["mfe"] for row in cases), default=None),
        "target_occurrences": sum(row["target_day"] is not None for row in cases),
        "invalidation_occurrences": sum(row["invalidation_day"] is not None for row in cases),
        "walk_forward_folds": folds,
        "regimes_observed": sorted({row["regime"] for row in cases}),
        "cost_stress_mean_return": sum(returns) / len(returns) - cost if returns else None,
        "cases": cases,
    }


def evaluate_study(bars: list[dict[str, Any]], parameters: dict[str, Any]) -> dict[str, Any]:
    result = _evaluate_study(bars, parameters)
    scenario = parameters.get("scenario_policy")
    variations = (
        {"target_fraction": float(parameters["target_fraction"]) * 0.8},
        {"target_fraction": min(1.0, float(parameters["target_fraction"]) * 1.2)},
        {"invalidation_fraction": float(parameters["invalidation_fraction"]) * 0.8},
        {"invalidation_fraction": min(0.99, float(parameters["invalidation_fraction"]) * 1.2)},
        {"horizon_days": max(1, int(parameters["horizon_days"]) - 1)},
        {"horizon_days": min(30, int(parameters["horizon_days"]) + 1)},
    )
    if scenario:
        variations = (
            {"target_atr": float(scenario["target_atr"]) * 0.8},
            {"target_atr": float(scenario["target_atr"]) * 1.2},
            {"invalidation_atr": float(scenario["invalidation_atr"]) * 0.8},
            {"invalidation_atr": float(scenario["invalidation_atr"]) * 1.2},
            {"horizon_days": max(1, int(parameters["horizon_days"]) - 1)},
            {"horizon_days": min(30, int(parameters["horizon_days"]) + 1)},
        )
    sensitivity = []
    for changes in variations:
        varied = (
            {**parameters, **changes}
            if not scenario
            else {
                **parameters,
                "scenario_policy": {**scenario, **changes},
                "horizon_days": changes.get("horizon_days", parameters["horizon_days"]),
            }
        )
        measured = _evaluate_study(bars, varied)
        sensitivity.append(
            {
                "changes": changes,
                "observations": measured["observations"],
                "mean_return": measured["mean_return"],
                "maximum_drawdown": measured["maximum_drawdown"],
            }
        )
    result["parameter_sensitivity"] = sensitivity
    return result
