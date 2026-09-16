"""Offline numeric baseline experiments; no registry writes or automatic promotion.

The solver is Money-owned NumPy ridge, not native Qlib training. Its raw-space
coefficients use exactly the features consumed by the native Qlib inference seam.
Chronology checks verify the supplied provenance; independent source/PIT review,
regression qualification and manual model promotion remain separate requirements.
"""

from __future__ import annotations

import hashlib
import inspect
import math
import os
import platform
import stat
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Self

import numpy as np
from numpy.typing import NDArray
from pydantic import AwareDatetime, Field, field_validator, model_validator

from money.adapters.native_qlib import FEATURES, LinearModelArtifact, feature_values
from money.adapters.upstream import QuantBar, QuantResearchInput
from money.schemas.contracts import Contract, EvidenceRecord, PriceBar, Ticker, content_hash

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NumericFeature = Annotated[float, Field(ge=-1_000_000, le=1_000_000)]
MAXIMUM_DATASET_BYTES = 32_000_000
MAXIMUM_ROWS = 20_000


class TrainingObservation(Contract):
    ticker: Ticker
    feature_time: AwareDatetime
    feature_available_at: AwareDatetime
    prediction_time: AwareDatetime
    features: tuple[NumericFeature, ...] = Field(min_length=len(FEATURES), max_length=len(FEATURES))
    feature_evidence_hashes: tuple[Digest, ...] = Field(min_length=21, max_length=21)
    label_start: AwareDatetime
    label_end: AwareDatetime
    label_available_at: AwareDatetime
    label_return: float = Field(ge=-1, le=1_000_000)
    label_evidence_hashes: tuple[Digest, ...] = Field(min_length=1, max_length=2)

    @field_validator("feature_time", "feature_available_at", "prediction_time", "label_start", "label_end", "label_available_at")
    @classmethod
    def utc_times(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def chronological(self) -> Self:
        if not (self.feature_time <= self.feature_available_at <= self.prediction_time
                < self.label_start <= self.label_end <= self.label_available_at):
            raise ValueError("features or labels violate their point-in-time chronology")
        if len(set(self.feature_evidence_hashes)) != 21:
            raise ValueError("feature evidence must contain 21 distinct observations")
        if len(set(self.label_evidence_hashes)) != len(self.label_evidence_hashes):
            raise ValueError("duplicate label evidence")
        if set(self.feature_evidence_hashes).intersection(self.label_evidence_hashes):
            raise ValueError("future label evidence cannot enter features")
        return self

    @property
    def row_hash(self) -> str:
        return content_hash(self)


class TrainingDataset(Contract):
    version: str = Field(min_length=1, max_length=200)
    feature_set_version: Literal["money-ohlcv-v1"] = "money-ohlcv-v1"
    features: tuple[str, ...] = FEATURES
    origin: Literal["SYNTHETIC_TEST", "ARCHIVED_EVIDENCE"]
    source_archive_hash: Digest
    historical_universe_hash: Digest
    corporate_action_review_hash: Digest
    adjustment_policy: Literal["unadjusted_no_actions", "split_adjusted_total_return"]
    label_definition: Literal["next_open_to_horizon_close_return_v1"] = "next_open_to_horizon_close_return_v1"
    maximum_horizon_days: int = Field(ge=1, le=30)
    observations: tuple[TrainingObservation, ...] = Field(min_length=1, max_length=MAXIMUM_ROWS)
    hash: str = ""

    @model_validator(mode="after")
    def seal(self) -> Self:
        if self.features != FEATURES:
            raise ValueError("training features must match Money's fixed inference feature order")
        keys = [(row.prediction_time, row.ticker) for row in self.observations]
        if keys != sorted(set(keys)):
            raise ValueError("observations must be uniquely ordered by UTC prediction timestamp and ticker")
        feature_keys = {(row.ticker, row.feature_time) for row in self.observations}
        if len(feature_keys) != len(self.observations):
            raise ValueError("duplicate instrument feature windows cannot inflate the dataset")
        if any(row.label_end - row.prediction_time > timedelta(days=self.maximum_horizon_days)
               for row in self.observations):
            raise ValueError("a training label exceeds the declared research horizon")
        digest = content_hash(self.model_dump(mode="json", exclude={"hash"}))
        if self.hash and self.hash != digest:
            raise ValueError("training dataset hash differs")
        object.__setattr__(self, "hash", digest)
        return self


class TrainingConfiguration(Contract):
    model_id: str = Field(min_length=1, max_length=60)
    model_version: str = Field(min_length=1, max_length=60)
    training_cutoff: AwareDatetime
    evaluation_cutoff: AwareDatetime
    ridge_alpha: float = Field(default=1.0, ge=0.00000001, le=1_000_000)
    embargo_days: int = Field(default=2, ge=1, le=30)
    walk_forward_folds: int = Field(default=3, ge=3, le=10)
    minimum_training_observations: int = Field(default=60, ge=30, le=MAXIMUM_ROWS)
    minimum_fold_observations: int = Field(default=10, ge=10, le=MAXIMUM_ROWS)
    minimum_oos_observations: int = Field(default=30, ge=30, le=MAXIMUM_ROWS)

    @field_validator("training_cutoff", "evaluation_cutoff")
    @classmethod
    def utc_times(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def chronology(self) -> Self:
        if self.training_cutoff >= self.evaluation_cutoff:
            raise ValueError("training cutoff must precede final evaluation cutoff")
        return self

    @property
    def config_hash(self) -> str:
        return content_hash(self)


class RegressionMetrics(Contract):
    observations: int = Field(ge=1)
    unique_prediction_times: int = Field(ge=1)
    instruments: int = Field(ge=1)
    rmse: float = Field(ge=0)
    mae: float = Field(ge=0)
    training_mean_baseline_rmse: float = Field(ge=0)
    improves_training_mean_baseline: bool
    directional_accuracy: float = Field(ge=0, le=1)
    r_squared: float | None


class FitEvaluation(Contract):
    name: str
    fit_cutoff: AwareDatetime
    training_start: AwareDatetime
    training_latest_label_availability: AwareDatetime
    training_observations: int
    training_rows_hash: Digest
    purged_training_observations: int
    validation_start: AwareDatetime
    validation_end: AwareDatetime
    validation_rows_hash: Digest
    excluded_unavailable_labels: int
    excluded_overlapping_labels: int
    coefficients: tuple[float, ...]
    intercept: float
    metrics: RegressionMetrics


class TrainingValidationReport(Contract):
    status: Literal["UNPROMOTED"] = "UNPROMOTED"
    solver: Literal["money-numpy-standardized-ridge-v1"] = "money-numpy-standardized-ridge-v1"
    dataset_hash: Digest
    dataset_origin: Literal["SYNTHETIC_TEST", "ARCHIVED_EVIDENCE"]
    configuration: TrainingConfiguration
    configuration_hash: Digest
    artifact_hash: Digest
    implementation_hash: Digest
    feature_implementation_hash: Digest
    numpy_version: str
    python_version: str
    walk_forward: tuple[FitEvaluation, ...]
    held_out: FitEvaluation
    chronology_checks_passed: Literal[True] = True
    production_qualified: Literal[False] = False
    independent_approval: Literal[False] = False
    limitations: tuple[str, ...]

    @property
    def report_hash(self) -> str:
        """SHA256 of exactly the compact report bytes emitted by the CLI."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class TrainingResult(Contract):
    status: Literal["UNPROMOTED"] = "UNPROMOTED"
    artifact: LinearModelArtifact
    validation: TrainingValidationReport
    artifact_hash: Digest
    validation_report_hash: Digest

    @model_validator(mode="after")
    def identities(self) -> Self:
        if (self.artifact_hash != self.artifact.artifact_hash
                or self.validation.artifact_hash != self.artifact_hash
                or self.validation.dataset_hash != self.artifact.dataset_hash
                or self.validation_report_hash != self.validation.report_hash):
            raise ValueError("training result artifact/report identity differs")
        return self


def observation_from_evidence(
    ticker: str, prediction_time: datetime, feature_records: Sequence[EvidenceRecord],
    label_open: EvidenceRecord, label_close: EvidenceRecord,
) -> TrainingObservation:
    """Recompute fixed Money features and a future return from archived bars.

    All feature original-publication times must precede the actual prediction.
    Later labels are allowed here, but not in a fit until their availability.
    Archive/source/adjustment qualification remains an independent review input.
    """
    if prediction_time.tzinfo is None or prediction_time.utcoffset() is None:
        raise ValueError("prediction timestamp must be timezone-aware")
    if len(feature_records) != 21:
        raise ValueError("feature construction needs exactly 21 historical bars")
    records = tuple(EvidenceRecord.model_validate_json(row.model_dump_json()) for row in feature_records)
    future = tuple(EvidenceRecord.model_validate_json(row.model_dump_json()) for row in (label_open, label_close))
    if any(not isinstance(row.payload, PriceBar) or not row.pit_safe or row.conflicting
           or row.publication_time is None for row in (*records, *future)):
        raise ValueError("training requires conflict-free price bars with original availability")
    if any(not row.available_at(prediction_time) for row in records):
        raise ValueError("feature evidence was unavailable at prediction time")
    currencies = {row.payload.currency for row in (*records, *future) if isinstance(row.payload, PriceBar)}
    if len(currencies) != 1:
        raise ValueError("training source raw currencies must be consistent")
    bars = []
    for row in records:
        assert isinstance(row.payload, PriceBar)
        scale = 100 if row.payload.currency == "GBX" else 1
        bars.append(QuantBar(evidence_id=row.evidence_id, observed_at=row.observation_time,
            open_gbp=row.payload.open / scale, high_gbp=row.payload.high / scale,
            low_gbp=row.payload.low / scale, close_gbp=row.payload.close / scale, volume=row.payload.volume))
    values = feature_values(QuantResearchInput(snapshot_id="offline-training", snapshot_hash="offline-training",
        ticker=ticker, cutoff=prediction_time, minimum_horizon_days=1, maximum_horizon_days=30, bars=tuple(bars)))
    opening, closing = future
    assert isinstance(opening.payload, PriceBar) and isinstance(closing.payload, PriceBar)
    publications = [row.publication_time for row in records if row.publication_time is not None]
    label_publications = [row.publication_time for row in future if row.publication_time is not None]
    return TrainingObservation(ticker=ticker, feature_time=records[-1].observation_time,
        feature_available_at=max(publications), prediction_time=prediction_time, features=values,
        feature_evidence_hashes=tuple(row.hash for row in records), label_start=opening.observation_time,
        label_end=closing.observation_time, label_available_at=max(label_publications),
        label_return=float(closing.payload.close / opening.payload.open - 1),
        label_evidence_hashes=tuple(dict.fromkeys(row.hash for row in future)))


def _fit(rows: Sequence[TrainingObservation], alpha: float) -> tuple[NDArray[np.float64], float, float]:
    matrix = np.asarray([row.features for row in rows], dtype=np.float64)
    labels = np.asarray([row.label_return for row in rows], dtype=np.float64)
    center, target_mean = matrix.mean(axis=0), float(labels.mean())
    scale = matrix.std(axis=0)
    scale[scale == 0] = 1  # deterministic constant-feature handling; no data imputation
    standardized = (matrix - center) / scale
    # Train-only centering/scaling; never include validation rows in preprocessing.
    try:
        weights = np.linalg.solve(standardized.T @ standardized + alpha * np.eye(len(FEATURES)),
                                  standardized.T @ (labels - target_mean))
    except np.linalg.LinAlgError as exc:
        raise ValueError("baseline fit is numerically unstable") from exc
    raw_weights = weights / scale
    intercept = target_mean - float(center @ raw_weights)
    if not np.isfinite(raw_weights).all() or not math.isfinite(intercept):
        raise ValueError("baseline fitting produced a non-finite coefficient")
    return raw_weights, intercept, target_mean


def _metrics(rows: Sequence[TrainingObservation], weights: NDArray[np.float64],
             intercept: float, training_mean: float) -> RegressionMetrics:
    labels = np.asarray([row.label_return for row in rows], dtype=np.float64)
    predictions = np.asarray([row.features for row in rows], dtype=np.float64) @ weights + intercept
    errors = predictions - labels
    rmse = float(np.sqrt(np.mean(errors ** 2)))
    baseline = float(np.sqrt(np.mean((labels - training_mean) ** 2)))
    total_squares = float(np.sum((labels - labels.mean()) ** 2))
    return RegressionMetrics(observations=len(rows), unique_prediction_times=len({row.prediction_time for row in rows}),
        instruments=len({row.ticker for row in rows}), rmse=rmse, mae=float(np.mean(np.abs(errors))),
        training_mean_baseline_rmse=baseline, improves_training_mean_baseline=rmse < baseline,
        directional_accuracy=float(np.mean(np.sign(predictions) == np.sign(labels))),
        r_squared=None if total_squares == 0 else 1 - float(np.sum(errors ** 2)) / total_squares)


def _evaluate(
    name: str, all_rows: Sequence[TrainingObservation], validation_rows: Sequence[TrainingObservation],
    fit_cutoff: datetime, evaluation_cutoff: datetime, configuration: TrainingConfiguration,
    minimum_validation: int,
) -> FitEvaluation:
    if not validation_rows:
        raise ValueError(f"{name}: validation partition is empty")
    # Both known outcome and its publication must strictly precede the fit.
    # The caller places each fit before the validation start by the embargo.
    candidates = [row for row in all_rows if row.prediction_time < validation_rows[0].prediction_time]
    train = [row for row in candidates if row.prediction_time < fit_cutoff
             and row.label_end < fit_cutoff and row.label_available_at < fit_cutoff]
    if len(train) < configuration.minimum_training_observations:
        raise ValueError(f"{name}: insufficient training observations after purge and embargo")
    available = [row for row in validation_rows if row.label_available_at <= evaluation_cutoff]
    test: list[TrainingObservation] = []
    last_label_end: dict[str, datetime] = {}
    for row in available:
        if row.prediction_time > last_label_end.get(row.ticker, datetime.min.replace(tzinfo=UTC)):
            test.append(row)
            last_label_end[row.ticker] = row.label_end
    if len(test) < minimum_validation:
        raise ValueError(f"{name}: insufficient non-overlapping available validation observations")
    weights, intercept, mean = _fit(train, configuration.ridge_alpha)
    return FitEvaluation(name=name, fit_cutoff=fit_cutoff,
        training_start=min(row.prediction_time for row in train),
        training_latest_label_availability=max(row.label_available_at for row in train),
        training_observations=len(train), training_rows_hash=content_hash([row.row_hash for row in train]),
        purged_training_observations=len(candidates) - len(train),
        validation_start=test[0].prediction_time, validation_end=max(row.label_available_at for row in test),
        validation_rows_hash=content_hash([row.row_hash for row in test]),
        excluded_unavailable_labels=len(validation_rows) - len(available),
        excluded_overlapping_labels=len(available) - len(test),
        coefficients=tuple(float(value) for value in weights), intercept=intercept,
        metrics=_metrics(test, weights, intercept, mean))


def train_baseline(dataset: TrainingDataset, configuration: TrainingConfiguration) -> TrainingResult:
    """Fixed-configuration chronological experiment. Never tune on or refit after OOS."""
    dataset = TrainingDataset.model_validate_json(dataset.model_dump_json())
    configuration = TrainingConfiguration.model_validate_json(configuration.model_dump_json())
    rows = dataset.observations
    pre_oos = [row for row in rows if row.prediction_time < configuration.training_cutoff]
    times = sorted({row.prediction_time for row in pre_oos})
    initial = max(configuration.minimum_training_observations, len(times) // 2)
    validation_times = times[initial:]
    if len(validation_times) < configuration.walk_forward_folds:
        raise ValueError("insufficient chronological timestamp groups for walk-forward validation")
    embargo = timedelta(days=configuration.embargo_days)
    folds = []
    for index in range(configuration.walk_forward_folds):
        start = index * len(validation_times) // configuration.walk_forward_folds
        end = (index + 1) * len(validation_times) // configuration.walk_forward_folds
        selected = set(validation_times[start:end])
        validation_rows = [row for row in pre_oos if row.prediction_time in selected]
        # Outcomes from this fold may not overlap the next fold's fit/embargo.
        outcome_cutoff = (validation_times[end] - embargo if end < len(validation_times)
                          else configuration.training_cutoff)
        folds.append(_evaluate(f"walk-forward-{index + 1}", pre_oos, validation_rows,
            min(selected) - embargo, outcome_cutoff, configuration,
            configuration.minimum_fold_observations))
    # The final holdout is not used for parameter selection, preprocessing, fitting,
    # or fold design. A changed OOS label can change only evaluation/identity fields.
    holdout = [row for row in rows if configuration.training_cutoff + embargo <= row.prediction_time
               <= configuration.evaluation_cutoff]
    evaluation = _evaluate("held-out", rows, holdout, configuration.training_cutoff,
        configuration.evaluation_cutoff, configuration, configuration.minimum_oos_observations)
    artifact = LinearModelArtifact(model_id=configuration.model_id, model_version=configuration.model_version,
        coefficients=evaluation.coefficients, intercept=evaluation.intercept,
        training_data_version=dataset.version, dataset_hash=dataset.hash,
        training_start=evaluation.training_start, training_cutoff=configuration.training_cutoff,
        validation_start=evaluation.validation_start, validation_end=evaluation.validation_end,
        oos_observations=evaluation.metrics.observations, walk_forward_folds=len(folds),
        oos_rmse=evaluation.metrics.rmse, pit_validated=False)
    implementation = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = TrainingValidationReport(dataset_hash=dataset.hash, dataset_origin=dataset.origin,
        configuration=configuration, configuration_hash=configuration.config_hash,
        artifact_hash=artifact.artifact_hash, implementation_hash=implementation,
        feature_implementation_hash=hashlib.sha256(inspect.getsource(feature_values).encode()).hexdigest(),
        numpy_version=np.__version__, python_version=platform.python_version(),
        walk_forward=tuple(folds), held_out=evaluation,
        limitations=(
            "UNPROMOTED: no registry registration, approval or production activation was performed.",
            "PIT checks validate declared timestamps; source authenticity, historical universe, adjustment policy and archive hashes require independent review.",
            "Artifact pit_validated remains false until independent source/PIT review; reviewing a final true artifact changes its hash and requires newly bound validation evidence.",
            "Money-owned NumPy ridge training; native Qlib training and native runtime qualification are not claimed.",
            "Fixed predeclared alpha; repeated experiments on this holdout invalidate its untouched status and require a new independent holdout.",
            "Non-overlapping labels per instrument reduce overlap; cross-instrument dependence and regime coverage are not certified.",
            "Regression qualification, independent manual review and separately attested source/PIT validation remain required.",
            "Returns are price-return prediction targets, not after-cost trading outcomes or calibrated confidence.",
        ))
    return TrainingResult(artifact=artifact, validation=report, artifact_hash=artifact.artifact_hash,
                          validation_report_hash=report.report_hash)


def load_training_json[T: Contract](path: Path, model: type[T], *, maximum_bytes: int) -> T:
    """Bound regular-file reads before decoding; never deserialize executable objects."""
    if not 0 < maximum_bytes <= MAXIMUM_DATASET_BYTES:
        raise ValueError("training JSON byte limit is out of bounds")
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size > maximum_bytes:
            raise ValueError("training input must be a bounded regular JSON file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            raise ValueError("training input exceeded its byte limit")
        return model.model_validate_json(payload)
    finally:
        os.close(descriptor)


def write_training_result(result: TrainingResult, directory: Path) -> None:
    """New directory only: never replace a prior experiment or write to the registry."""
    result = TrainingResult.model_validate_json(result.model_dump_json())
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    files = {"model-artifact.json": result.artifact.model_dump_json(),
             "validation-report.json": result.validation.model_dump_json(),
             "training-result.json": result.model_dump_json()}
    for name, payload in files.items():
        with (directory / name).open("xb") as handle:
            handle.write(payload.encode())
            handle.flush()
            os.fsync(handle.fileno())
