"""Synthetic-only statistical/chronology tests. No production model qualification."""

from __future__ import annotations

import hashlib
import math
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from money.adapters.native_qlib import FEATURES, LinearModelArtifact, QualifiedLinearModel
from money.adapters.upstream import UpstreamUnavailable
from money.models import qlib_training
from money.models.training import (
    TrainingConfiguration,
    TrainingDataset,
    TrainingObservation,
    TrainingResult,
    load_training_json,
    observation_from_evidence,
    train_baseline,
    write_training_result,
)
from money.offline_research.promotion import PromotionEvidence, assess_promotion
from money.schemas.contracts import EvidenceRecord, PriceBar

START = datetime(2020, 1, 1, tzinfo=UTC)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def dataset() -> TrainingDataset:
    rows = []
    for index in range(420):
        features = (math.sin(index / 11), math.cos(index / 7), 0.01 + index / 100000,
                    1 + math.sin(index / 19) / 2, 0.05)
        stamp = START + timedelta(days=index)
        rows.append(TrainingObservation(ticker="TEST.L", feature_time=stamp,
            feature_available_at=stamp + timedelta(hours=1), prediction_time=stamp + timedelta(hours=2),
            features=features, feature_evidence_hashes=tuple(digest(f"{index}-feature-{j}") for j in range(21)),
            label_start=stamp + timedelta(days=1), label_end=stamp + timedelta(days=1, hours=12),
            label_available_at=stamp + timedelta(days=1, hours=13),
            label_return=0.02 * features[0] + 0.01 * features[1] + 0.005 * features[3],
            label_evidence_hashes=(digest(f"{index}-label"),)))
    return TrainingDataset(version="SYNTHETIC-TEST-v1", origin="SYNTHETIC_TEST",
        source_archive_hash=digest("synthetic-source"), historical_universe_hash=digest("synthetic-universe"),
        corporate_action_review_hash=digest("synthetic-actions"), adjustment_policy="unadjusted_no_actions",
        maximum_horizon_days=2, observations=tuple(rows))


@pytest.fixture
def configuration() -> TrainingConfiguration:
    return TrainingConfiguration(model_id="synthetic-test-linear", model_version="1",
        training_cutoff=START + timedelta(days=280), evaluation_cutoff=START + timedelta(days=440))


def changed_dataset(dataset: TrainingDataset, rows: tuple[TrainingObservation, ...]) -> TrainingDataset:
    return TrainingDataset.model_validate({**dataset.model_dump(exclude={"hash", "observations"}), "observations": rows})


def test_baseline_is_reproducible_unpromoted_and_uses_raw_inference_coefficients(
    dataset: TrainingDataset, configuration: TrainingConfiguration,
) -> None:
    result = train_baseline(dataset, configuration)
    assert result == train_baseline(dataset, configuration)
    assert result.status == "UNPROMOTED" and not result.validation.production_qualified
    assert not result.validation.independent_approval
    assert result.artifact.pit_validated is False
    assert result.artifact.features == FEATURES
    assert len(result.validation.walk_forward) == 3 and result.artifact.oos_observations >= 30
    assert result.validation.held_out.metrics.improves_training_mean_baseline
    assert result.validation.held_out.metrics.rmse < 0.001
    row = dataset.observations[-1]
    raw_prediction = sum(a * b for a, b in zip(result.artifact.coefficients, row.features, strict=True)) + result.artifact.intercept
    assert raw_prediction == pytest.approx(row.label_return, abs=0.001)
    assert result.artifact.training_cutoff < result.artifact.validation_start
    for fit in (*result.validation.walk_forward, result.validation.held_out):
        assert fit.training_latest_label_availability < fit.fit_cutoff
        assert fit.fit_cutoff <= fit.validation_start - timedelta(days=configuration.embargo_days)
        assert fit.purged_training_observations > 0 and fit.excluded_overlapping_labels > 0
    assessment = assess_promotion(PromotionEvidence(artifact_id="offline-test", artifact_hash=result.artifact_hash,
        proposed_by="trainer", validations=()), configuration.evaluation_cutoff)
    assert assessment.state == "BLOCKED"
    assert "INDEPENDENT_APPROVAL_REQUIRED" in assessment.reasons
    unqualified = QualifiedLinearModel(artifact=result.artifact, promotion=PromotionEvidence(
        artifact_id="offline-test", artifact_hash=result.artifact_hash, proposed_by="trainer", validations=()))
    with pytest.raises(UpstreamUnavailable, match="point-in-time qualification"):
        unqualified.require_qualified(configuration.evaluation_cutoff + timedelta(days=1))
    for earlier, later in zip(result.validation.walk_forward, result.validation.walk_forward[1:], strict=False):
        assert earlier.validation_end <= later.fit_cutoff


def test_held_out_labels_never_change_fit_or_walk_forward(
    dataset: TrainingDataset, configuration: TrainingConfiguration,
) -> None:
    original = train_baseline(dataset, configuration)
    changed = tuple(row.model_copy(update={"label_return": 0.9}) if row.prediction_time > configuration.training_cutoff else row
                    for row in dataset.observations)
    result = train_baseline(changed_dataset(dataset, changed), configuration)
    assert result.artifact.coefficients == original.artifact.coefficients
    assert result.artifact.intercept == original.artifact.intercept
    assert result.artifact.training_cutoff == original.artifact.training_cutoff
    assert result.validation.walk_forward == original.validation.walk_forward
    assert result.validation.held_out.metrics.rmse != original.validation.held_out.metrics.rmse
    assert result.artifact_hash != original.artifact_hash


def test_native_training_never_falls_back_when_pinned_runtime_is_unavailable(
    dataset: TrainingDataset, configuration: TrainingConfiguration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(package: str) -> None:
        assert package == "qlib"
        raise UpstreamUnavailable("pinned native source package is unavailable")

    monkeypatch.setattr(qlib_training, "require_pinned_source", unavailable)
    with pytest.raises(UpstreamUnavailable, match="pinned native source"):
        qlib_training.train_qlib(dataset, configuration)


def test_native_solver_receives_only_purged_training_rows_and_stays_unpromoted(
    dataset: TrainingDataset, configuration: TrainingConfiguration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A unit spy validates the partition boundary; it is not native qualification.
    from money.models.training import _fit

    seen = []

    def spy(rows, alpha):
        seen.append(tuple(rows))
        return _fit(rows, alpha)

    monkeypatch.setattr(qlib_training, "require_pinned_source", lambda _: None)
    monkeypatch.setattr(qlib_training, "import_module", lambda _: None)
    monkeypatch.setattr(qlib_training.metadata, "version", lambda _: "unit-test-only")
    monkeypatch.setattr(qlib_training, "_fit_native", spy)
    result = qlib_training.train_qlib(dataset, configuration)
    assert len(seen) == configuration.walk_forward_folds + 1
    for rows, evaluation in zip(seen, (*result.validation.walk_forward, result.validation.held_out), strict=True):
        assert len(rows) == evaluation.training_observations
        assert all(row.prediction_time < evaluation.fit_cutoff and row.label_available_at < evaluation.fit_cutoff
                   for row in rows)
    assert result.validation.solver == "pinned-qlib-standardized-ridge-v1"
    assert result.validation.native_source_hash == qlib_training.SOURCE_DIGESTS["qlib"]
    assert result.status == "UNPROMOTED"
    assert not result.artifact.pit_validated
    assert not result.validation.production_qualified
    assert not result.validation.independent_approval
    assert result.validation_report_hash == hashlib.sha256(result.validation.model_dump_json().encode()).hexdigest()
    changed = tuple(row.model_copy(update={"label_return": 0.9}) if row.prediction_time > configuration.training_cutoff else row
                    for row in dataset.observations)
    revised = qlib_training.train_qlib(changed_dataset(dataset, changed), configuration)
    assert revised.artifact.coefficients == result.artifact.coefficients
    assert revised.artifact.intercept == result.artifact.intercept
    assert revised.validation.walk_forward == result.validation.walk_forward
    assert revised.validation.held_out.metrics.rmse != result.validation.held_out.metrics.rmse


def test_post_fold_features_and_labels_cannot_change_that_fold_fit(
    dataset: TrainingDataset, configuration: TrainingConfiguration,
) -> None:
    original = train_baseline(dataset, configuration)
    first = original.validation.walk_forward[0]
    changed = tuple(row.model_copy(update={"label_return": 0.7, "features": (9, 8, 7, 6, 5)})
        if row.prediction_time >= first.fit_cutoff else row for row in dataset.observations)
    result = train_baseline(changed_dataset(dataset, changed), configuration)
    revised = result.validation.walk_forward[0]
    assert revised.coefficients == first.coefficients and revised.intercept == first.intercept
    assert revised.training_rows_hash == first.training_rows_hash


def test_late_label_publication_and_exact_embargo_boundary_are_purged(
    dataset: TrainingDataset, configuration: TrainingConfiguration,
) -> None:
    original = train_baseline(dataset, configuration)
    first = original.validation.walk_forward[0]
    modified = []
    for index, row in enumerate(dataset.observations):
        if index in {1, 2}:
            row = row.model_copy(update={"label_available_at": first.fit_cutoff + timedelta(days=index - 1)})
        modified.append(row)
    result = train_baseline(changed_dataset(dataset, tuple(modified)), configuration)
    assert result.validation.walk_forward[0].training_observations == first.training_observations - 2
    assert result.validation.walk_forward[0].training_latest_label_availability < first.fit_cutoff


def test_same_time_instruments_are_grouped_and_never_split_across_fold_boundary(
    dataset: TrainingDataset, configuration: TrainingConfiguration,
) -> None:
    rows = tuple(item for row in dataset.observations for item in (
        row.model_copy(update={"ticker": "AAA.L"}), row.model_copy(update={"ticker": "ZZZ.L"})))
    result = train_baseline(changed_dataset(dataset, rows), configuration)
    for fit in result.validation.walk_forward:
        assert fit.training_observations % 2 == 0 and fit.metrics.observations % 2 == 0
        assert fit.metrics.instruments == 2


@pytest.mark.parametrize("field,value", [
    ("features", (float("nan"), 0, 0, 0, 0)), ("label_return", float("inf")),
    ("features", (1, 2)), ("features", (1e20, 0, 0, 0, 0)),
    ("feature_available_at", START + timedelta(days=10)),
    ("prediction_time", START - timedelta(days=1)),
    ("label_available_at", START), ("label_end", START),
])
def test_malformed_nonfinite_and_leaky_rows_are_rejected(dataset: TrainingDataset, field: str, value: object) -> None:
    with pytest.raises(ValueError):
        TrainingObservation.model_validate({**dataset.observations[0].model_dump(), field: value})


def test_equal_instants_with_different_timezone_offsets_are_duplicate(dataset: TrainingDataset) -> None:
    row = dataset.observations[0]
    duplicate = TrainingObservation.model_validate({**row.model_dump(),
        "prediction_time": row.prediction_time.astimezone(timezone(timedelta(hours=5)))})
    with pytest.raises(ValueError, match="uniquely ordered"):
        changed_dataset(dataset, (row, duplicate, *dataset.observations[1:]))
    repeated_window = row.model_copy(update={"prediction_time": row.prediction_time + timedelta(hours=1)})
    with pytest.raises(ValueError, match="duplicate instrument feature"):
        changed_dataset(dataset, (row, repeated_window, *dataset.observations[1:]))


def test_constant_features_are_stable_but_never_claim_predictive_skill(
    dataset: TrainingDataset, configuration: TrainingConfiguration,
) -> None:
    constant = tuple(row.model_copy(update={"features": (1, 1, 1, 1, 1), "label_return": 0.01}) for row in dataset.observations)
    result = train_baseline(changed_dataset(dataset, constant), configuration)
    assert result.artifact.coefficients == (0, 0, 0, 0, 0)
    assert result.artifact.intercept == pytest.approx(0.01)
    assert not result.validation.held_out.metrics.improves_training_mean_baseline


def test_insufficient_available_or_nonoverlapping_samples_fail_closed(
    dataset: TrainingDataset, configuration: TrainingConfiguration,
) -> None:
    late = tuple(row.model_copy(update={"label_available_at": configuration.evaluation_cutoff + timedelta(days=1)})
                 for row in dataset.observations)
    with pytest.raises(ValueError, match="insufficient training"):
        train_baseline(changed_dataset(dataset, late), configuration)
    short = changed_dataset(dataset, dataset.observations[:300])
    with pytest.raises(ValueError, match="non-overlapping available"):
        train_baseline(short, configuration)


def test_tampered_hashes_and_unknown_fields_are_rejected(dataset: TrainingDataset, configuration: TrainingConfiguration) -> None:
    with pytest.raises(ValueError, match="dataset hash"):
        TrainingDataset.model_validate({**dataset.model_dump(), "version": "tampered"})
    with pytest.raises(ValueError):
        TrainingConfiguration.model_validate({**configuration.model_dump(), "auto_promote": True})
    result = train_baseline(dataset, configuration)
    with pytest.raises(ValueError):
        TrainingResult.model_validate({**result.model_dump(), "artifact_hash": "0" * 64})


def test_files_are_bounded_symlink_safe_and_never_overwritten(
    dataset: TrainingDataset, configuration: TrainingConfiguration, tmp_path: Path,
) -> None:
    dataset_file = tmp_path / "dataset.json"
    dataset_file.write_text(dataset.model_dump_json())
    assert load_training_json(dataset_file, TrainingDataset, maximum_bytes=2_000_000) == dataset
    with pytest.raises(ValueError, match="bounded regular"):
        load_training_json(dataset_file, TrainingDataset, maximum_bytes=20)
    link = tmp_path / "linked.json"
    link.symlink_to(dataset_file)
    with pytest.raises(OSError):
        load_training_json(link, TrainingDataset, maximum_bytes=2_000_000)
    result = train_baseline(dataset, configuration)
    output = tmp_path / "experiment"
    write_training_result(result, output)
    artifact_bytes = (output / "model-artifact.json").read_bytes()
    report_bytes = (output / "validation-report.json").read_bytes()
    assert hashlib.sha256(artifact_bytes).hexdigest() == result.artifact_hash
    assert hashlib.sha256(report_bytes).hexdigest() == result.validation_report_hash
    assert LinearModelArtifact.model_validate_json(artifact_bytes) == result.artifact
    with pytest.raises(FileExistsError):
        write_training_result(result, output)


def test_cli_runs_only_offline_and_produces_unpromoted_artifact(
    dataset: TrainingDataset, configuration: TrainingConfiguration, tmp_path: Path,
) -> None:
    data_path, config_path = tmp_path / "dataset.json", tmp_path / "config.json"
    data_path.write_text(dataset.model_dump_json())
    config_path.write_text(configuration.model_dump_json())
    command = [sys.executable, "scripts/train_baseline.py", "--dataset", str(data_path),
               "--config", str(config_path), "--output", str(tmp_path / "result")]
    result = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
    assert result.returncode == 0 and "UNPROMOTED" in result.stdout
    repeated = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
    assert repeated.returncode == 2 and "TRAINING_FAILED" in repeated.stderr
    assert "Traceback" not in repeated.stderr


def test_native_cli_rejects_synthetic_test_data_without_writing_an_artifact(
    dataset: TrainingDataset, configuration: TrainingConfiguration, tmp_path: Path,
) -> None:
    data_path, config_path, output = tmp_path / "dataset.json", tmp_path / "config.json", tmp_path / "result"
    data_path.write_text(dataset.model_dump_json())
    config_path.write_text(configuration.model_dump_json())
    result = subprocess.run(
        [sys.executable, "scripts/train_qlib.py", "--dataset", str(data_path),
         "--config", str(config_path), "--output", str(output)],
        text=True, capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 2 and "QLIB_TRAINING_FAILED" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


def bar(index: int, currency: str = "GBX", late: bool = False) -> EvidenceRecord:
    observed = START + timedelta(days=index)
    return EvidenceRecord(snapshot_id="synthetic-archive", source="synthetic", provider="synthetic",
        source_id=f"{index}", canonical_source_id=f"{index}", evidence_id=f"bar-{index}",
        observation_time=observed, publication_time=START + timedelta(days=40) if late else observed,
        retrieval_time=START + timedelta(days=40), fresh_until=START + timedelta(days=50), pit_safe=True,
        payload=PriceBar(open=100, high=110, low=95, close=105, volume=1000, currency=currency))


def test_evidence_builder_recomputes_numeric_features_and_gbx_label_return() -> None:
    row = observation_from_evidence("TEST.L", START + timedelta(days=20, hours=1),
                                    tuple(bar(index) for index in range(21)), bar(21), bar(22))
    assert row.features == pytest.approx((0, 0, 0, 1, 15 / 105))
    assert row.label_return == pytest.approx(0.05)
    with pytest.raises(ValueError, match="unavailable"):
        observation_from_evidence("TEST.L", START + timedelta(days=20, hours=1),
            (*tuple(bar(index) for index in range(20)), bar(20, late=True)), bar(21), bar(22))
    with pytest.raises(ValueError, match="currencies"):
        observation_from_evidence("TEST.L", START + timedelta(days=20, hours=1),
            tuple(bar(index) for index in range(21)), bar(21, currency="GBP"), bar(22))
