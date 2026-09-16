"""Immutable model metadata with independently reviewed, manual activation events."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select

from money.adapters.native_qlib import QualifiedLinearModel
from money.offline_research.promotion import assess_promotion
from money.storage import ResearchStore
from money.storage.production_models import model_promotions, model_registry
from money.storage.store import StoreError, now_utc


class ModelRegistry:
    """Operator-only service. Public API clients cannot register or promote models."""

    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    def register(self, model: QualifiedLinearModel, validation_reports: Mapping[str, bytes]) -> str:
        model = QualifiedLinearModel.model_validate_json(model.model_dump_json())
        if model.artifact.artifact_hash != model.promotion.artifact_hash:
            raise StoreError("Model and promotion hashes differ")
        if any(v.dataset_hash != model.artifact.dataset_hash for v in model.promotion.validations):
            raise StoreError("Model and validation datasets differ")
        for validation in model.promotion.validations:
            report = validation_reports.get(validation.report_hash)
            if (
                report is None
                or len(report) > 2_000_000
                or hashlib.sha256(report).hexdigest() != validation.report_hash
            ):
                raise StoreError("Validation report bytes do not match the reviewed evidence")
        record_id = f"{model.artifact.model_id}:{model.artifact.model_version}"
        if len(record_id) > 128:
            raise StoreError("Model registry identity is too long")
        data = model.model_dump(mode="json")
        with self.store.transaction() as connection:
            previous = (
                connection.execute(select(model_registry).where(model_registry.c.id == record_id))
                .mappings()
                .first()
            )
            if previous is not None:
                if previous["payload"] != data:
                    raise StoreError("A registered model version is immutable")
                return record_id
            connection.execute(
                model_registry.insert().values(
                    id=record_id,
                    artifact_hash=model.artifact.artifact_hash,
                    payload=data,
                    created_at=now_utc(),
                )
            )
        return record_id

    def promote(
        self, record_id: str, *, reviewer_id: str, manual: bool, rollback_target: str | None = None
    ) -> str:
        if manual is not True:
            raise StoreError("Model activation requires explicit manual promotion")
        stamp = now_utc()
        with self.store.transaction() as connection:
            record = (
                connection.execute(
                    select(model_registry).where(model_registry.c.id == record_id).with_for_update()
                )
                .mappings()
                .first()
            )
            if record is None:
                raise StoreError("Model version is not registered")
            model = QualifiedLinearModel.model_validate(record["payload"])
            assessment = assess_promotion(model.promotion, stamp)
            if (
                assessment.state != "ELIGIBLE_FOR_MANUAL_PROMOTION"
                or model.promotion.approval is None
            ):
                raise StoreError("Model validation or independent approval is incomplete")
            if reviewer_id != model.promotion.approval.reviewer_id:
                raise StoreError("The authenticated operator must match the independent reviewer")
            model.require_qualified(stamp)
            if (
                rollback_target is not None
                and connection.scalar(
                    select(model_registry.c.id).where(model_registry.c.id == rollback_target)
                )
                is None
            ):
                raise StoreError("Rollback target is not registered")
            event_id = str(uuid4())
            revision = (
                int(
                    connection.scalar(
                        select(func.count())
                        .select_from(model_promotions)
                        .where(model_promotions.c.model_id == record_id)
                    )
                    or 0
                )
                + 1
            )
            connection.execute(
                model_promotions.insert().values(
                    id=event_id,
                    model_id=record_id,
                    created_at=stamp,
                    payload={
                        "state": "APPROVED",
                        "revision": revision,
                        "promoted_by": reviewer_id,
                        "promoted_at": stamp.isoformat(),
                        "artifact_hash": record["artifact_hash"],
                        "rollback_target": rollback_target,
                        "assessment": assessment.model_dump(mode="json"),
                    },
                )
            )
            return event_id

    def withdraw(self, record_id: str, *, reviewer_id: str, reason: str) -> None:
        if not reviewer_id.strip() or not reason.strip() or len(reason) > 512:
            raise StoreError("Withdrawal requires bounded operator identity and reason")
        with self.store.transaction() as connection:
            record = connection.scalar(
                select(model_registry.c.id)
                .where(model_registry.c.id == record_id)
                .with_for_update()
            )
            if record is None:
                raise StoreError("Model version is not registered")
            revision = (
                int(
                    connection.scalar(
                        select(func.count())
                        .select_from(model_promotions)
                        .where(model_promotions.c.model_id == record_id)
                    )
                    or 0
                )
                + 1
            )
            connection.execute(
                model_promotions.insert().values(
                    id=str(uuid4()),
                    model_id=record_id,
                    created_at=now_utc(),
                    payload={
                        "state": "WITHDRAWN",
                        "revision": revision,
                        "reviewer_id": reviewer_id,
                        "reason": reason,
                    },
                )
            )

    def load_active(
        self, record_id: str, expected_hash: str, as_of: datetime
    ) -> QualifiedLinearModel:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise StoreError("Model availability cutoff must be timezone-aware")
        with self.store.engine.connect() as connection:
            record = (
                connection.execute(select(model_registry).where(model_registry.c.id == record_id))
                .mappings()
                .first()
            )
            latest = connection.scalar(
                select(model_promotions.c.payload)
                .where(
                    model_promotions.c.model_id == record_id,
                    model_promotions.c.created_at <= as_of,
                )
                .order_by(
                    model_promotions.c.created_at.desc(),
                    model_promotions.c.payload["revision"].as_integer().desc(),
                )
                .limit(1)
            )
            current = connection.scalar(
                select(model_promotions.c.payload)
                .where(
                    model_promotions.c.model_id == record_id,
                )
                .order_by(
                    model_promotions.c.created_at.desc(),
                    model_promotions.c.payload["revision"].as_integer().desc(),
                )
                .limit(1)
            )
            if (
                record is None
                or record["artifact_hash"] != expected_hash
                or not latest
                or latest["state"] != "APPROVED"
                or not current
                or current["state"] != "APPROVED"
            ):
                raise StoreError("No manually promoted model matches the selected artifact")
            model = QualifiedLinearModel.model_validate(record["payload"])
            if model.artifact.artifact_hash != expected_hash:
                raise StoreError("Stored model artifact failed its integrity check")
            model.require_qualified(as_of)
            return model
