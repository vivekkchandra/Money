from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from money.offline_research.promotion import (
    IndependentApproval,
    OfflineValidationEvidence,
    PromotionEvidence,
    assess_promotion,
)
from money.performance.outcomes import (
    OutcomeBar,
    OutcomeSpecification,
    calculate_outcome,
    quantstats_summary,
)

ISSUED = datetime(2026, 1, 1, 12, tzinfo=UTC)
ARTIFACT = "a" * 64
HASHES = tuple(character * 64 for character in "bcde")


def bar(day, *, close="100", high="105", low="95", start_day=None, known_day=None):
    return OutcomeBar(
        evidence_id=f"bar-{day}",
        evidence_hash="1" * 64,
        start_at=ISSUED + timedelta(days=day - 1 if start_day is None else start_day),
        end_at=ISSUED + timedelta(days=day),
        available_at=ISSUED + timedelta(days=day if known_day is None else known_day),
        open_gbp="100",
        close_gbp=close,
        high_gbp=high,
        low_gbp=low,
    )


@pytest.fixture
def specification():
    return OutcomeSpecification(
        research_id="research-1",
        ticker="EXAMPLE.L",
        issued_at=ISSUED,
        reference_price_gbp="100",
        target_gbp="110",
        invalidation_gbp="90",
    )


def test_calendar_returns_extrema_and_reference_basis(specification):
    bars = (
        bar(1, close="102", high="106", low="96"),
        bar(3, close="108", high="112", low="95"),
        bar(5, close="91", high="103", low="85"),
    )
    result = calculate_outcome(specification, bars, ISSUED + timedelta(days=30))
    assert [h.calendar_days for h in result.horizons] == [1, 3, 5, 10, 30]
    assert [h.return_fraction for h in result.horizons] == [
        Decimal("0.02"),
        Decimal("0.08"),
        Decimal("-0.09"),
        None,
        None,
    ]
    assert [h.state for h in result.horizons][-2:] == ["MISSING_DATA", "MISSING_DATA"]
    assert result.mfe_fraction == Decimal("0.12")
    assert result.mae_fraction == Decimal("-0.15")
    assert result.event_order == "TARGET_FIRST"
    assert result.basis == "PUBLISHED_RESEARCH_REFERENCE"
    assert result.first_observed_target.interval_start == ISSUED + timedelta(days=2)
    assert result.first_observed_target.latest_seconds_after_issue == 3 * 86400


def test_target_and_invalidation_in_same_bar_are_ambiguous(specification):
    result = calculate_outcome(
        specification, (bar(1, high="115", low="80"),), ISSUED + timedelta(days=1)
    )
    assert result.event_order == "SAME_BAR_AMBIGUOUS"
    assert result.first_observed_target == result.first_observed_invalidation
    assert result.first_observed_target.earliest_seconds_after_issue == 0
    assert result.first_observed_target.latest_seconds_after_issue == 86400
    assert not hasattr(result.first_observed_target, "exact_time")


def test_future_late_and_preissue_extrema_are_excluded(specification):
    bars = (
        bar(0.25, start_day=-0.25, high="1000", low="1"),
        bar(1, start_day=0.25, high="110", low="90", known_day=2),
        bar(2, high="1000", low="1"),
    )
    result = calculate_outcome(specification, bars, ISSUED + timedelta(days=1))
    assert result.observation_count == 0
    assert result.mfe_fraction is None and result.mae_fraction is None
    assert result.horizons[0].state == "MISSING_DATA"
    assert result.horizons[1].state == "NOT_MATURED"
    later = calculate_outcome(specification, bars[:2], ISSUED + timedelta(days=2))
    assert later.observation_count == 1
    assert later.horizons[0].known_at == ISSUED + timedelta(days=2)


def test_horizon_never_uses_next_available_future_price(specification):
    result = calculate_outcome(
        specification,
        (bar(1.1, start_day=0, close="110", high="110"),),
        ISSUED + timedelta(days=3),
    )
    assert result.horizons[0].return_fraction is None
    assert result.horizons[1].return_fraction is None  # endpoint older than 24 hours


def test_endpoint_age_is_explicit_and_can_require_exact_time(specification):
    observation = bar(0.8, start_day=0, close="102")
    result = calculate_outcome(specification, (observation,), ISSUED + timedelta(days=1))
    assert result.horizons[0].return_fraction == Decimal("0.02")
    strict = specification.model_copy(update={"maximum_endpoint_age_hours": 0})
    assert (
        calculate_outcome(strict, (observation,), ISSUED + timedelta(days=1))
        .horizons[0]
        .return_fraction
        is None
    )


def test_invalid_or_overlapping_observations_fail(specification):
    with pytest.raises(ValueError, match="overlapping"):
        calculate_outcome(specification, (bar(1), bar(1.5)), ISSUED + timedelta(days=2))
    with pytest.raises(ValueError, match="duplicate"):
        calculate_outcome(specification, (bar(1), bar(1)), ISSUED + timedelta(days=2))
    with pytest.raises(ValueError, match="timezone"):
        calculate_outcome(specification, (), datetime(2026, 1, 2))
    with pytest.raises(ValidationError):
        bar(1, known_day=0)


def test_outcome_evidence_is_frozen(specification):
    result = calculate_outcome(specification, (), ISSUED)
    with pytest.raises(ValidationError):
        result.reference_price_gbp = Decimal("200")


@pytest.mark.parametrize(
    "values", [(), (0.1, 0.2), (0.1, 0.1, 0.1), (0.1, float("nan"), 0.2), (-1.0, 0.1, 0.2)]
)
def test_quantstats_rejects_insufficient_or_invalid_returns(values):
    with pytest.raises(ValueError):
        quantstats_summary(values, periods_per_year=252)


def test_quantstats_uses_explicit_frequency_and_preserves_initial_drawdown(monkeypatch):
    calls = {}

    def sharpe(series, *, rf, periods):
        calls["sharpe"] = (tuple(series), rf, periods)
        return 0.5

    def drawdown(equity):
        calls["equity"] = tuple(equity)
        return -0.02

    native_import = import_module

    def modules(name):
        if name == "quantstats.stats":
            return SimpleNamespace(sharpe=sharpe, max_drawdown=drawdown)
        return native_import(name)

    monkeypatch.setattr("money.performance.outcomes.import_module", modules)
    result = quantstats_summary(
        (-0.02, 0.01, 0.015), periods_per_year=52, annual_risk_free_rate=0.03
    )
    assert calls["sharpe"] == ((-0.02, 0.01, 0.015), 0.03, 52)
    assert calls["equity"][:2] == (100.0, 98.0)
    assert result.maximum_drawdown == -0.02
    assert result.periods_per_year == 52


def test_quantstats_unavailable_is_not_zero_performance(monkeypatch):
    def missing(name):
        raise ImportError("optional library unavailable")

    monkeypatch.setattr("money.performance.outcomes.import_module", missing)
    with pytest.raises(ImportError):
        quantstats_summary((0.01, 0.02, -0.01), periods_per_year=252)


@pytest.fixture
def promotion():
    validations = tuple(
        OfflineValidationEvidence(
            kind=kind,
            passed=True,
            tested_artifact_hash=ARTIFACT,
            dataset_hash="f" * 64,
            report_hash=HASHES[index],
            completed_at=ISSUED,
        )
        for index, kind in enumerate(
            ("POINT_IN_TIME", "WALK_FORWARD", "OUT_OF_SAMPLE", "REGRESSION")
        )
    )
    return PromotionEvidence(
        artifact_id="candidate-factor-v1",
        artifact_hash=ARTIFACT,
        proposed_by="rd-agent-job-1",
        validations=validations,
        approval=IndependentApproval(
            reviewer_id="research-reviewer-1",
            approved=True,
            reviewed_artifact_hash=ARTIFACT,
            reviewed_validation_hashes=HASHES,
            reviewed_at=ISSUED + timedelta(hours=1),
        ),
    )


def test_qualified_offline_artifact_is_eligible_but_never_activated(promotion):
    result = assess_promotion(promotion, ISSUED + timedelta(hours=2))
    assert result.state == "ELIGIBLE_FOR_MANUAL_PROMOTION"
    assert result.activated is False
    assert result.reasons == ()


@pytest.mark.parametrize("index", range(4))
def test_every_offline_validation_gate_is_required(promotion, index):
    failed = list(promotion.validations)
    failed[index] = failed[index].model_copy(update={"passed": False})
    result = assess_promotion(
        promotion.model_copy(update={"validations": tuple(failed)}), ISSUED + timedelta(hours=2)
    )
    assert result.state == "BLOCKED"
    assert f"{failed[index].kind}_FAILED" in result.reasons
    missing = promotion.model_copy(
        update={"validations": tuple(failed[:index] + failed[index + 1 :])}
    )
    assert (
        "REQUIRED_VALIDATION_MISSING"
        in assess_promotion(missing, ISSUED + timedelta(hours=2)).reasons
    )


@pytest.mark.parametrize(
    "approval_change,reason",
    [
        ({"reviewer_id": " RD-AGENT-JOB-1 "}, "REVIEWER_NOT_INDEPENDENT"),
        ({"approved": False}, "INDEPENDENT_APPROVAL_REQUIRED"),
        ({"reviewed_artifact_hash": "b" * 64}, "APPROVAL_ARTIFACT_MISMATCH"),
        ({"reviewed_validation_hashes": ()}, "APPROVAL_VALIDATION_MISMATCH"),
        ({"reviewed_at": ISSUED - timedelta(seconds=1)}, "APPROVAL_TIME_INVALID"),
    ],
)
def test_offline_approval_must_be_independent_and_bind_exact_artifacts(
    promotion, approval_change, reason
):
    changed = promotion.model_copy(
        update={"approval": promotion.approval.model_copy(update=approval_change)}
    )
    result = assess_promotion(changed, ISSUED + timedelta(hours=2))
    assert result.state == "BLOCKED"
    assert reason in result.reasons


def test_validation_of_another_artifact_and_future_evidence_are_blocked(promotion):
    wrong = promotion.validations[0].model_copy(
        update={
            "tested_artifact_hash": "9" * 64,
            "completed_at": ISSUED + timedelta(days=2),
        }
    )
    evidence = promotion.model_copy(update={"validations": (wrong, *promotion.validations[1:])})
    result = assess_promotion(evidence, ISSUED + timedelta(hours=2))
    assert "VALIDATION_ARTIFACT_MISMATCH" in result.reasons
    assert "VALIDATION_FROM_FUTURE" in result.reasons
    assert "APPROVAL_TIME_INVALID" in result.reasons


def test_agent_cannot_approve_itself_by_omitting_approval(promotion):
    evidence = promotion.model_copy(update={"approval": None})
    result = assess_promotion(evidence, ISSUED + timedelta(hours=2))
    assert result.state == "BLOCKED"
    assert result.activated is False
    assert "INDEPENDENT_APPROVAL_REQUIRED" in result.reasons
