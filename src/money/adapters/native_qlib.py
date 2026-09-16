"""Qlib inference from a manually qualified, non-executable JSON artifact."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from money.adapters.native_attestation import require_pinned_source
from money.adapters.upstream import (
    UPSTREAM_SHAS,
    InvalidUpstreamReport,
    QuantResearchInput,
    UnsupportedSnapshotData,
    UpstreamUnavailable,
)
from money.offline_research.promotion import PromotionEvidence, assess_promotion
from money.schemas.contracts import Claim, Contract, QlibQuantResearchReport

FEATURES = ("momentum_5", "momentum_20", "volatility_20", "relative_volume_20", "range_fraction")


class LinearModelArtifact(Contract):
    model_id: str = Field(min_length=1, max_length=100)
    model_version: str = Field(min_length=1, max_length=100)
    model_kind: Literal["qlib-linear-v1"] = "qlib-linear-v1"
    feature_set_version: Literal["money-ohlcv-v1"] = "money-ohlcv-v1"
    features: tuple[str, ...] = FEATURES
    coefficients: tuple[float, ...]
    intercept: float
    training_data_version: str = Field(min_length=1, max_length=200)
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    training_start: AwareDatetime
    training_cutoff: AwareDatetime
    validation_start: AwareDatetime
    validation_end: AwareDatetime
    oos_observations: int = Field(ge=30)
    walk_forward_folds: int = Field(ge=3)
    oos_rmse: float = Field(ge=0)
    pit_validated: bool
    upstream_sha: Literal["be725493eb1a6bbb42bf11b37aa7669f59610ff1"] = UPSTREAM_SHAS["qlib"]  # type: ignore[assignment]

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        if self.features != FEATURES or len(self.coefficients) != len(FEATURES):
            raise ValueError("model features do not match the Money feature implementation")
        if not self.training_start <= self.training_cutoff < self.validation_start <= self.validation_end:
            raise ValueError("training and validation windows overlap or are reversed")
        return self

    @property
    def artifact_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class QualifiedLinearModel(Contract):
    artifact: LinearModelArtifact
    promotion: PromotionEvidence

    def require_qualified(self, cutoff: datetime) -> None:
        if self.artifact.pit_validated is not True:
            raise UpstreamUnavailable("Qlib artifact requires independently reviewed point-in-time qualification")
        if self.promotion.artifact_hash != self.artifact.artifact_hash:
            raise InvalidUpstreamReport("promoted artifact hash differs from model bytes")
        if any(v.dataset_hash != self.artifact.dataset_hash for v in self.promotion.validations):
            raise InvalidUpstreamReport("model validation dataset hash mismatch")
        if self.artifact.validation_end >= cutoff:
            raise UnsupportedSnapshotData("model validation was unavailable at prediction cutoff")
        assessment = assess_promotion(self.promotion, cutoff)
        if assessment.state != "ELIGIBLE_FOR_MANUAL_PROMOTION":
            raise UpstreamUnavailable("Qlib model is not manually approved with complete validation")


def load_qualified_model(path: Path, expected_hash: str) -> QualifiedLinearModel:
    """Only administrator-selected regular JSON; never deserialize pickle/code."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64_000:
        raise UpstreamUnavailable("Qlib artifact is not a bounded regular JSON file")
    model = QualifiedLinearModel.model_validate_json(path.read_bytes())
    if model.artifact.artifact_hash != expected_hash:
        raise InvalidUpstreamReport("Qlib artifact does not match the selected registry hash")
    return model


def feature_values(data: QuantResearchInput) -> tuple[float, ...]:
    if len(data.bars) < 21:
        raise UnsupportedSnapshotData("Money OHLCV features require at least 21 observations")
    observed = [bar.observed_at for bar in data.bars]
    if observed != sorted(set(observed)) or observed[-1] > data.cutoff:
        raise UnsupportedSnapshotData("Qlib observations are duplicate, unordered or in the future")
    closes = [float(b.close_gbp) for b in data.bars[-21:]]
    if any(value <= 0 or not math.isfinite(value) for value in closes):
        raise UnsupportedSnapshotData("Qlib requires positive finite prices")
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, 21)]
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    average_volume = sum(bar.volume for bar in data.bars[-21:-1]) / 20
    if average_volume <= 0:
        raise UnsupportedSnapshotData("Qlib requires historical traded volume")
    latest = data.bars[-1]
    return (
        closes[-1] / closes[-6] - 1,
        closes[-1] / closes[-21] - 1,
        math.sqrt(variance),
        latest.volume / average_volume,
        float((latest.high_gbp - latest.low_gbp) / latest.close_gbp),
    )


class QlibNativeRunner:
    def __init__(self, model: QualifiedLinearModel) -> None:
        self.model = QualifiedLinearModel.model_validate_json(model.model_dump_json())

    def __call__(self, data: QuantResearchInput) -> QlibQuantResearchReport:
        self.model.require_qualified(data.cutoff)
        values = feature_values(data)
        require_pinned_source("qlib")
        try:
            np = import_module("numpy")
            pd = import_module("pandas")
            linear = import_module("qlib.contrib.model.linear")
            loaders = import_module("qlib.data.dataset.loader")
            handlers = import_module("qlib.data.dataset.handler")
            datasets = import_module("qlib.data.dataset")
        except ImportError as exc:
            raise UpstreamUnavailable("the pinned Qlib inference runtime is not installed") from exc
        artifact = self.model.artifact
        timestamp = pd.Timestamp(data.cutoff).tz_localize(None)
        frame = pd.DataFrame(
            [values],
            index=pd.MultiIndex.from_tuples([(timestamp, data.ticker)], names=["datetime", "instrument"]),
            columns=pd.MultiIndex.from_product([["feature"], FEATURES]),
        )
        # No expression provider, external dataset loader, processor, pickle or
        # inference-time fitting: the complete dataset is an in-memory PIT frame.
        loader = loaders.StaticDataLoader(config=frame)
        handler = handlers.DataHandlerLP(data_loader=loader, infer_processors=[], learn_processors=[])
        dataset = datasets.DatasetH(handler=handler, segments={"test": (timestamp, timestamp)})
        native_model = linear.LinearModel()
        native_model.coef_ = np.asarray(artifact.coefficients, dtype=float)
        native_model.intercept_ = artifact.intercept
        prediction = native_model.predict(dataset, segment="test")
        if len(prediction) != 1:
            raise InvalidUpstreamReport("Qlib returned an unexpected prediction universe")
        score = float(prediction.iloc[0])
        if not math.isfinite(score):
            raise InvalidUpstreamReport("Qlib returned a non-finite prediction")
        return QlibQuantResearchReport(
            snapshot_id=data.snapshot_id, snapshot_hash=data.snapshot_hash,
            conclusion="Qualified linear model prediction; no cross-sectional rank is claimed "
            "because this invocation contains one instrument.",
            claims=(Claim(
                claim_id="qlib:prediction", family="quantitative",
                statement=f"Model {artifact.model_id}/{artifact.model_version} produced score {score:.8g}.",
                evidence_ids=tuple(bar.evidence_id for bar in data.bars[-21:]),
                classification="INFERENCE",
            ),),
            model_version=artifact.model_version, prompt_version="not-applicable",
            upstream_sha=UPSTREAM_SHAS["qlib"], model_family="linear-regression",
            feature_families=("momentum", "volatility", "volume"),
            argument_families=("validated-statistical-prediction",), created_at=datetime.now(UTC),
            prediction_score=score, uncertainty=artifact.oos_rmse,
            validation_metadata=(json.dumps({
                "artifact_hash": artifact.artifact_hash,
                "feature_set_version": artifact.feature_set_version,
                "training_cutoff": artifact.training_cutoff.isoformat(),
                "prediction_timestamp": data.cutoff.isoformat(),
                "dataset_hash": artifact.dataset_hash,
                "training_data_version": artifact.training_data_version,
                "validation_start": artifact.validation_start.isoformat(),
                "validation_end": artifact.validation_end.isoformat(),
                "oos_observations": artifact.oos_observations,
                "walk_forward_folds": artifact.walk_forward_folds,
                "feature_values": dict(zip(FEATURES, values, strict=True)),
                "promoted_by": self.model.promotion.approval.reviewer_id
                if self.model.promotion.approval else None,
            }),),
        )
