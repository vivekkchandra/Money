"""Synthetic operator approvals cannot override an unqualified training artifact."""

import hashlib
from datetime import timedelta

import pytest

from money.adapters.native_qlib import LinearModelArtifact, QualifiedLinearModel
from money.adapters.upstream import UpstreamUnavailable
from money.models.registry import ModelRegistry
from money.offline_research.promotion import (
    IndependentApproval,
    OfflineValidationEvidence,
    PromotionEvidence,
)
from money.schemas.contracts import utc_now
from money.storage import ResearchStore
from money.storage.store import StoreError


def test_registry_cannot_promote_pit_unqualified_offline_artifact(store: ResearchStore) -> None:
    now = utc_now()
    artifact = LinearModelArtifact(model_id="offline-test", model_version="unpromoted",
        coefficients=(1, 2, 3, 4, 5), intercept=0, training_data_version="synthetic-only",
        dataset_hash="a" * 64, training_start=now - timedelta(days=100),
        training_cutoff=now - timedelta(days=60), validation_start=now - timedelta(days=59),
        validation_end=now - timedelta(days=10), oos_observations=30, walk_forward_folds=3,
        oos_rmse=0.03, pit_validated=False)
    reports = {}
    validations = []
    for kind in ("POINT_IN_TIME", "WALK_FORWARD", "OUT_OF_SAMPLE", "REGRESSION"):
        raw = f"SYNTHETIC TEST alleged {kind} validation".encode()
        digest = hashlib.sha256(raw).hexdigest()
        reports[digest] = raw
        validations.append(OfflineValidationEvidence(kind=kind, passed=True,
            tested_artifact_hash=artifact.artifact_hash, dataset_hash=artifact.dataset_hash,
            report_hash=digest, completed_at=now - timedelta(days=2)))
    model = QualifiedLinearModel(artifact=artifact, promotion=PromotionEvidence(
        artifact_id="offline-test", artifact_hash=artifact.artifact_hash, proposed_by="trainer",
        validations=tuple(validations), approval=IndependentApproval(
            reviewer_id="independent-reviewer", approved=True, reviewed_artifact_hash=artifact.artifact_hash,
            reviewed_validation_hashes=tuple(reports), reviewed_at=now - timedelta(days=1))))
    registry = ModelRegistry(store)
    identity = registry.register(model, reports)
    with pytest.raises(UpstreamUnavailable, match="point-in-time qualification"):
        registry.promote(identity, reviewer_id="independent-reviewer", manual=True)
    with pytest.raises(StoreError):
        registry.load_active(identity, artifact.artifact_hash, now)
