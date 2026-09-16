from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
import talib

from money.backtest.lean import LeanStudyParameters
from money.backtest.study import _atr14, evaluate_study
from money.signals.generation import SignalPolicy


def bars():
    start = datetime(2020, 1, 1, tzinfo=UTC)
    return [
        dict(
            time=(start + timedelta(days=i)).isoformat(),
            available_at=(start + timedelta(days=i, hours=18)).isoformat(),
            open=100 + i / 10,
            close=100 + i / 10,
            high=102 + i / 10,
            low=98 + i / 10,
        )
        for i in range(240)
    ]


def parameters():
    return LeanStudyParameters(scenario_policy=SignalPolicy()).model_dump(mode="json") | {
        "round_trip_cost_bps": 10
    }


def test_study_atr_matches_actual_talib_not_an_approximate_range():
    rows = bars()
    for index, row in enumerate(rows):
        row["high"] += (index % 13) / 3
        row["low"] -= (index % 7) / 2
    expected = talib.ATR(
        *(np.asarray([row[key] for row in rows], dtype=float) for key in ("high", "low", "close")),
        timeperiod=14,
    )
    assert _atr14(rows)[14:] == pytest.approx(expected[14:])


def test_study_observes_policy_interval_and_not_assumed_fills():
    rows = bars()
    first = max(61, int(len(rows) * 0.6))
    baseline = evaluate_study(rows, parameters())
    assert baseline["cases"][0]["time"] == rows[first]["time"]
    rows[first]["open"] += 20
    assert evaluate_study(rows, parameters())["cases"][0]["time"] != rows[first]["time"]


def test_study_uses_atr_levels_and_conservative_ambiguous_bar():
    rows = bars()
    first = max(61, int(len(rows) * 0.6))
    reference = rows[first - 1]["close"]
    rows[first]["high"], rows[first]["low"] = reference + 30, reference - 10
    measured = evaluate_study(rows, parameters())["cases"][0]
    assert measured["invalidation_day"] == 1 and measured["target_day"] is None
    assert measured["return"] == pytest.approx((reference - 8) / rows[first]["open"] - 1 - 0.001)


def test_scenario_checks_full_recursive_atr_history_publication_and_real_sensitivities():
    rows = bars()
    result = evaluate_study(rows, parameters())
    assert set(result["parameter_sensitivity"][0]["changes"]) == {"target_atr"}
    rows[0]["available_at"] = (datetime(2030, 1, 1, tzinfo=UTC)).isoformat()
    assert evaluate_study(rows, parameters())["observations"] == 0


def test_horizon_cannot_differ_from_hashed_policy():
    with pytest.raises(ValueError, match="horizon"):
        LeanStudyParameters(horizon_days=10, scenario_policy=SignalPolicy())


def test_calendar_horizon_does_not_extend_across_weekends():
    rows = bars()
    stamp = datetime(2020, 1, 1, tzinfo=UTC)
    for row in rows:
        while stamp.weekday() >= 5:
            stamp += timedelta(days=1)
        row["time"], row["available_at"] = (
            stamp.isoformat(),
            (stamp + timedelta(hours=18)).isoformat(),
        )
        stamp += timedelta(days=1)
    first = max(61, int(len(rows) * 0.6))
    deadline = datetime.fromisoformat(rows[first]["time"]) + timedelta(days=5)
    future = [row for row in rows[first:] if datetime.fromisoformat(row["time"]) < deadline]
    measured = evaluate_study(rows, parameters())["cases"][0]
    assert len(future) < 5
    assert measured["return"] == pytest.approx(
        future[-1]["close"] / rows[first]["open"] - 1 - 0.001
    )
