"""Pinned Qlib training on in-memory Money evidence with no provider downloads.

The source/PIT review and independent manual promotion remain separate. This
module neither writes the registry nor declares a dataset production qualified.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from importlib import import_module, metadata

import numpy as np
from numpy.typing import NDArray

from money.adapters.native_attestation import SOURCE_DIGESTS, require_pinned_source
from money.adapters.native_qlib import FEATURES
from money.adapters.upstream import UpstreamUnavailable
from money.models.training import (
    TrainingConfiguration,
    TrainingDataset,
    TrainingObservation,
    TrainingResult,
    _train,
)


def _fit_native(
    rows: Sequence[TrainingObservation], alpha: float,
) -> tuple[NDArray[np.float64], float, float]:
    """Fit only purged training rows, export non-executable raw-space coefficients."""
    pd = import_module("pandas")
    linear = import_module("qlib.contrib.model.linear")
    loaders = import_module("qlib.data.dataset.loader")
    handlers = import_module("qlib.data.dataset.handler")
    datasets = import_module("qlib.data.dataset")
    matrix = np.asarray([row.features for row in rows], dtype=np.float64)
    labels = np.asarray([row.label_return for row in rows], dtype=np.float64)
    center, scale = matrix.mean(axis=0), matrix.std(axis=0)
    scale[scale == 0] = 1
    standardized = (matrix - center) / scale
    timestamps = [pd.Timestamp(row.prediction_time).tz_localize(None) for row in rows]
    frame = pd.DataFrame(
        np.column_stack((standardized, labels)),
        index=pd.MultiIndex.from_tuples(
            list(zip(timestamps, [row.ticker for row in rows], strict=True)),
            names=["datetime", "instrument"],
        ),
        columns=pd.MultiIndex.from_tuples([
            *(("feature", name) for name in FEATURES), ("label", "return"),
        ]),
    )
    loader = loaders.StaticDataLoader(config=frame)
    handler = handlers.DataHandlerLP(
        data_loader=loader, infer_processors=[], learn_processors=[],
    )
    dataset = datasets.DatasetH(
        handler=handler, segments={"train": (min(timestamps), max(timestamps))},
    )
    native_model = linear.LinearModel(
        estimator="ridge", alpha=alpha, fit_intercept=True, include_valid=False,
    )
    native_model.fit(dataset)
    weights = np.asarray(native_model.coef_, dtype=np.float64) / scale
    intercept = float(native_model.intercept_) - float(center @ weights)
    if weights.shape != (len(FEATURES),) or not np.isfinite(weights).all() or not math.isfinite(intercept):
        raise ValueError("native Qlib fitting produced invalid coefficients")
    native_predictions = native_model.predict(dataset, segment="train")
    if (not native_predictions.index.equals(frame.index)
            or not np.allclose(native_predictions.to_numpy(), matrix @ weights + intercept,
                               rtol=1e-9, atol=1e-12)):
        raise ValueError("exported coefficients differ from native Qlib predictions")
    return weights, intercept, float(labels.mean())


def train_qlib(dataset: TrainingDataset, configuration: TrainingConfiguration) -> TrainingResult:
    """Run the attested implementation; missing dependencies never fall back to NumPy."""
    require_pinned_source("qlib")
    try:
        # Resolve all required imports before starting the first experiment fold.
        for module in ("qlib.contrib.model.linear", "qlib.data.dataset.loader",
                       "qlib.data.dataset.handler", "qlib.data.dataset"):
            import_module(module)
        versions = {name: metadata.version(name) for name in ("numpy", "pandas", "scipy", "scikit-learn")}
    except (ImportError, metadata.PackageNotFoundError) as exc:
        raise UpstreamUnavailable("the pinned Qlib training dependencies are not installed") from exc
    return _train(dataset, configuration, fit=_fit_native,
                  native_source_hash=SOURCE_DIGESTS["qlib"], runtime_versions=versions)
