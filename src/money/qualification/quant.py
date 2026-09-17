"""Resumable, evidence-bound Qlib and LEAN operator qualification.

Training and simulation are automatic. Source authenticity, historical membership,
adjustments, costs, and independent manual activation remain human decisions.
No successful subprocess or populated JSON field substitutes for those decisions.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from pydantic import AwareDatetime, Field, SecretStr, TypeAdapter
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from money.adapters.native_attestation import SOURCE_DIGESTS, require_pinned_source
from money.adapters.native_qlib import LinearModelArtifact, QualifiedLinearModel
from money.adapters.upstream import UpstreamUnavailable
from money.api.settings import OperatorSettings
from money.backtest.lean import (
    LEAN_SHA,
    LeanContainerRunner,
    LeanContainerSettings,
    LeanCostAssumptions,
    LeanStudyParameters,
    LeanStudyQualification,
    historical_dataset_hash,
)
from money.models.qlib_training import train_qlib
from money.models.registry import ModelRegistry
from money.models.training import (
    MAXIMUM_DATASET_BYTES,
    MAXIMUM_ROWS,
    TrainingConfiguration,
    TrainingDataset,
    TrainingResult,
    observation_from_evidence,
)
from money.offline_research.promotion import (
    IndependentApproval,
    OfflineValidationEvidence,
    PromotionEvidence,
    ValidationKind,
    assess_promotion,
)
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    Contract,
    EvidenceRecord,
    FirmReport,
    LeanValidationReport,
    PriceBar,
    QlibQuantResearchReport,
    ResearchSnapshot,
    TradingAgentsResearchReport,
    content_hash,
)
from money.signals.generation import SignalPolicy
from money.storage import ResearchStore
from money.storage.production_models import model_promotions
from money.storage.store import StoreError

if TYPE_CHECKING:
    from money.qualification.core import QualificationContext


class QlibInputs(Contract):
    dataset_path: str | None = None
    archive_path: str
    historical_universe_path: str
    corporate_action_review_path: str
    configuration: TrainingConfiguration
    proposed_by: str = Field(min_length=1, max_length=200)
    # An operator must predeclare acceptance before seeing held-out measurements.
    maximum_oos_rmse: float = Field(gt=0)
    minimum_directional_accuracy: float = Field(ge=0.5, le=1)
    require_baseline_improvement: Literal[True]
    # Required for automatic derivation; never infer an adjustment basis.
    adjustment_policy: Literal["unadjusted_no_actions", "split_adjusted_total_return"] | None = None
    horizon_sessions: int = Field(default=1, ge=1, le=30)
    maximum_horizon_days: int = Field(default=30, ge=1, le=30)


class QlibSourceReview(Contract):
    approved: Literal[True]
    reviewed_by: str = Field(min_length=1, max_length=200)
    reviewed_at: AwareDatetime
    unpromoted_artifact_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    training_report_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_archive_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    historical_universe_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    corporate_action_review_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_publication_availability_verified: Literal[True]
    historical_universe_and_delistings_verified: Literal[True]
    corporate_actions_and_adjustments_verified: Literal[True]
    untouched_holdout_and_predeclared_configuration_verified: Literal[True]
    evidence_paths: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=20, max_length=8000)


class ManualApproval(Contract):
    approval: IndependentApproval
    manual_promote: Literal[True]
    operator_reviewer_id: str = Field(min_length=1, max_length=200)


class HistoricalAudit(Contract):
    kind: Literal["historical_eligibility", "survivorship", "corporate_actions", "costs"]
    approved: Literal[True]
    reviewed_by: str = Field(min_length=1, max_length=100)
    reviewed_at: AwareDatetime
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    subject_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    adjustment_policy: Literal["unadjusted_no_actions", "split_adjusted_total_return"]
    evidence_paths: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=20, max_length=8000)


class LeanInputs(Contract):
    container: LeanContainerSettings
    costs: LeanCostAssumptions
    parameters: LeanStudyParameters
    adjustment_policy: Literal["unadjusted_no_actions", "split_adjusted_total_return"]
    proposed_by: str = Field(min_length=1, max_length=100)
    approved_by: str = Field(min_length=1, max_length=100)
    approved_at: AwareDatetime


class ConsumedHoldout(ValueError):
    """A completed archived study cannot be retuned against its observed holdout."""


class InactiveRegistryModel(StoreError):
    """Resume cannot override an existing withdrawal or invalid activation."""


def _empty() -> dict[str, Any]:
    return {"complete": False, "manifest_fields": {}, "artifacts": [], "production_environment": {}}


def _bytes(ctx: QualificationContext, path: str, *, maximum: int = 2_000_000) -> bytes:
    payload = ctx.read_bytes(path, maximum=maximum)
    if payload is None:
        raise ValueError("referenced evidence is absent")
    return payload


def _copy_evidence(ctx: QualificationContext, paths: tuple[str, ...]) -> list[tuple[str, str]]:
    return [ctx.artifact(_bytes(ctx, path)) for path in paths]


def _derive_dataset(
    ctx: QualificationContext,
    inputs: QlibInputs,
    groups: Mapping[str, tuple[EvidenceRecord, ...]],
    archive_hash: str,
    universe_hash: str,
    actions_hash: str,
) -> TrainingDataset:
    """Derive numeric features/labels, never original availability or identities."""
    if inputs.adjustment_policy is None:
        raise ValueError("automatic derivation requires an explicitly reviewed adjustment policy")
    observations = []
    rejected_timing = 0
    for ticker, records in sorted(groups.items()):
        ordered = sorted(records, key=lambda row: row.observation_time)
        if (
            len({row.observation_time for row in ordered}) != len(ordered)
            or len({row.hash for row in ordered}) != len(ordered)
            or any(
                not isinstance(row.payload, PriceBar)
                or not row.pit_safe
                or row.publication_time is None
                or row.conflicting
                or row.provider == "money-demo"
                for row in ordered
            )
        ):
            raise ValueError(
                "automatic derivation requires unique original-publication PIT price bars"
            )
        for index in range(20, len(ordered) - inputs.horizon_sessions):
            features = ordered[index - 20 : index + 1]
            opening, closing = ordered[index + 1], ordered[index + inputs.horizon_sessions]
            # The prediction can only occur after every original feature became
            # available. A late publication excludes a row; it is never backdated.
            prediction = max(
                row.publication_time for row in features if row.publication_time is not None
            )
            if (
                prediction >= opening.observation_time
                or closing.observation_time - prediction
                > timedelta(days=inputs.maximum_horizon_days)
            ):
                rejected_timing += 1
                continue
            observations.append(
                observation_from_evidence(ticker, prediction, features, opening, closing)
            )
            if len(observations) > MAXIMUM_ROWS:
                raise ValueError("automatic archived dataset exceeds the fixed maximum rows")
    dataset = TrainingDataset(
        version=f"money-archive-{archive_hash[:20]}-h{inputs.horizon_sessions}",
        origin="ARCHIVED_EVIDENCE",
        source_archive_hash=archive_hash,
        historical_universe_hash=universe_hash,
        corporate_action_review_hash=actions_hash,
        adjustment_policy=inputs.adjustment_policy,
        maximum_horizon_days=inputs.maximum_horizon_days,
        observations=tuple(sorted(observations, key=lambda row: (row.prediction_time, row.ticker))),
    )
    # Exact canonical bytes underlying TrainingDataset.hash, retained as actual
    # evidence. The ordinary qualification artifact byte limit still applies.
    raw = json.dumps(
        dataset.model_dump(mode="json", exclude={"hash"}),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    reference = ctx.artifact(raw)
    if reference[0] != dataset.hash:
        raise ValueError("derived dataset bytes differ from its identity")
    ctx.write_json(
        "outputs/qlib-derived-dataset.json",
        {
            "dataset_hash": dataset.hash,
            "dataset_path": reference[1],
            "observations": len(observations),
            "excluded_late_or_out_of_horizon_windows": rejected_timing,
            "source_archive_hash": archive_hash,
            "status": "UNREVIEWED_UNPROMOTED",
        },
    )
    return dataset


def _verified_dataset(ctx: QualificationContext, inputs: QlibInputs) -> TrainingDataset:
    archive = _bytes(ctx, inputs.archive_path, maximum=MAXIMUM_DATASET_BYTES)
    archive_hash = hashlib.sha256(archive).hexdigest()
    universe_hash = hashlib.sha256(_bytes(ctx, inputs.historical_universe_path)).hexdigest()
    actions_hash = hashlib.sha256(_bytes(ctx, inputs.corporate_action_review_path)).hexdigest()
    # The archive's ticker grouping is itself subject to independent identity review.
    groups = TypeAdapter(dict[str, tuple[EvidenceRecord, ...]]).validate_json(archive)
    if any(
        row.retrieval_time > ctx.now
        or (row.publication_time is not None and row.publication_time > ctx.now)
        for records in groups.values()
        for row in records
    ):
        raise ValueError("archive contains future retrieval or publication claims")
    dataset = (
        TrainingDataset.model_validate_json(
            _bytes(ctx, inputs.dataset_path, maximum=MAXIMUM_DATASET_BYTES)
        )
        if inputs.dataset_path is not None
        else _derive_dataset(ctx, inputs, groups, archive_hash, universe_hash, actions_hash)
    )
    if dataset.origin != "ARCHIVED_EVIDENCE":
        raise ValueError("synthetic datasets cannot qualify")
    if (
        universe_hash != dataset.historical_universe_hash
        or actions_hash != dataset.corporate_action_review_hash
    ):
        raise ValueError("reviewed archive proof hash differs")
    if archive_hash != dataset.source_archive_hash:
        raise ValueError("archived source bytes differ")
    if (
        inputs.adjustment_policy is not None
        and dataset.adjustment_policy != inputs.adjustment_policy
    ):
        raise ValueError("reviewed adjustment policy differs")
    by_ticker = {ticker: {row.hash: row for row in records} for ticker, records in groups.items()}
    for observation in dataset.observations:
        rows = by_ticker[observation.ticker]
        selected = [rows[digest] for digest in observation.feature_evidence_hashes]
        labels = [rows[digest] for digest in observation.label_evidence_hashes]
        if any(row.provider == "money-demo" for row in (*selected, *labels)):
            raise ValueError("demo evidence cannot qualify")
        rebuilt = observation_from_evidence(
            observation.ticker, observation.prediction_time, selected, labels[0], labels[-1]
        )
        if rebuilt != observation:
            raise ValueError("training observations differ from actual archived bars")
    return dataset


def _training_result(
    ctx: QualificationContext,
    inputs: QlibInputs,
    dataset: TrainingDataset,
) -> TrainingResult:
    require_pinned_source("qlib")
    key = content_hash({"inputs": inputs.model_dump(mode="json"), "dataset": dataset.hash})
    study_path = f"outputs/qlib-study-{dataset.source_archive_hash}.json"
    prior_study = ctx.read_json(study_path)
    if prior_study is not None:
        prior_raw = _bytes(ctx, prior_study["path"])
        if hashlib.sha256(prior_raw).hexdigest() != prior_study["sha256"]:
            raise ValueError("prior study receipt bytes differ")
        prior_receipt = json.loads(prior_raw)
        if (
            prior_receipt.get("kind") != "money-qlib-training-receipt-v1"
            or prior_receipt.get("study_hash") != key
        ):
            raise ConsumedHoldout(
                "completed archive study already consumed this holdout; new untouched archive required"
            )
    checkpoint = ctx.read_json(f"outputs/qlib-training-{key}.json")
    if checkpoint is not None:
        result, completed_at = _read_training_receipt(ctx, checkpoint, key)
        if not inputs.configuration.evaluation_cutoff < completed_at <= ctx.now:
            raise ValueError("training completion time is invalid")
    else:
        result = train_qlib(dataset, inputs.configuration)
        digest, path = ctx.artifact(
            {
                "kind": "money-qlib-training-receipt-v1",
                "study_hash": key,
                "completed_at": ctx.now.isoformat(),
                "result": result.model_dump(mode="json"),
            }
        )
        checkpoint = {"sha256": digest, "path": path}
        ctx.write_json(
            f"outputs/qlib-training-{key}.json",
            checkpoint,
        )
    if (
        result.validation.dataset_hash != dataset.hash
        or result.validation.configuration != inputs.configuration
        or result.validation.solver != "pinned-qlib-standardized-ridge-v1"
        or result.validation.native_source_hash != SOURCE_DIGESTS["qlib"]
        or result.artifact.pit_validated
    ):
        raise ValueError("native training provenance differs")
    ctx.template(study_path, checkpoint)
    return result


def _read_training_receipt(
    ctx: QualificationContext,
    checkpoint: dict[str, Any],
    study_hash: str,
) -> tuple[TrainingResult, datetime]:
    """Cache metadata is only a pointer; all admission facts are hashed bytes."""
    payload = _bytes(ctx, checkpoint["path"])
    if hashlib.sha256(payload).hexdigest() != checkpoint["sha256"]:
        raise ValueError("cached training bytes were altered")
    receipt = json.loads(payload)
    if (
        receipt.get("kind") != "money-qlib-training-receipt-v1"
        or receipt.get("study_hash") != study_hash
    ):
        raise ValueError("cached training receipt belongs to another study")
    completed = TypeAdapter(AwareDatetime).validate_python(receipt["completed_at"])
    return TrainingResult.model_validate(receipt["result"]), completed


def _source_review(
    ctx: QualificationContext,
    inputs: QlibInputs,
    dataset: TrainingDataset,
    result: TrainingResult,
) -> tuple[QlibSourceReview, tuple[str, str]]:
    path = f"reviews/qlib-source-{result.artifact_hash}.json"
    ctx.template(
        path,
        {
            "approved": False,
            "reviewed_by": None,
            "reviewed_at": None,
            "unpromoted_artifact_hash": result.artifact_hash,
            "training_report_hash": result.validation_report_hash,
            "source_archive_hash": dataset.source_archive_hash,
            "historical_universe_hash": dataset.historical_universe_hash,
            "corporate_action_review_hash": dataset.corporate_action_review_hash,
            "original_publication_availability_verified": False,
            "historical_universe_and_delistings_verified": False,
            "corporate_actions_and_adjustments_verified": False,
            "untouched_holdout_and_predeclared_configuration_verified": False,
            "evidence_paths": [],
            "rationale": None,
        },
    )
    review = QlibSourceReview.model_validate(ctx.read_json(path))
    key = content_hash({"inputs": inputs.model_dump(mode="json"), "dataset": dataset.hash})
    training_state = ctx.read_json(f"outputs/qlib-training-{key}.json")
    if not isinstance(training_state, dict):
        raise ValueError("actual completed training receipt is unavailable")
    retained_result, completed_at = _read_training_receipt(ctx, training_state, key)
    if retained_result != result:
        raise ValueError("source review training receipt differs from the actual result")
    expected = (
        result.artifact_hash,
        result.validation_report_hash,
        dataset.source_archive_hash,
        dataset.historical_universe_hash,
        dataset.corporate_action_review_hash,
    )
    actual = (
        review.unpromoted_artifact_hash,
        review.training_report_hash,
        review.source_archive_hash,
        review.historical_universe_hash,
        review.corporate_action_review_hash,
    )
    if (
        actual != expected
        or not completed_at <= review.reviewed_at <= ctx.now
        or review.reviewed_by.strip().casefold() == inputs.proposed_by.strip().casefold()
        or not review.reviewed_by.strip()
    ):
        raise ValueError("source review must independently bind the actual completed training")
    proofs = _copy_evidence(ctx, review.evidence_paths)
    ref = ctx.artifact(
        {
            "review": review.model_dump(mode="json"),
            "source_bytes_sha256": hashlib.sha256(_bytes(ctx, path)).hexdigest(),
            "evidence": proofs,
        }
    )
    return review, ref


def _validation_evidence(
    ctx: QualificationContext,
    inputs: QlibInputs,
    result: TrainingResult,
    artifact: LinearModelArtifact,
    source: QlibSourceReview,
    source_ref: tuple[str, str],
) -> tuple[tuple[OfflineValidationEvidence, ...], dict[str, bytes], list[tuple[str, str]]]:
    report = result.validation
    folds = report.walk_forward
    chronological = all(
        f.training_latest_label_availability < f.fit_cutoff <= f.validation_start
        for f in (*folds, report.held_out)
    )
    metrics = report.held_out.metrics
    checks: dict[ValidationKind, bool] = {
        "POINT_IN_TIME": chronological,
        "WALK_FORWARD": (
            len(folds) >= inputs.configuration.walk_forward_folds
            and all(
                f.metrics.observations >= inputs.configuration.minimum_fold_observations
                for f in folds
            )
        ),
        "OUT_OF_SAMPLE": (
            metrics.observations >= inputs.configuration.minimum_oos_observations
            and artifact.validation_end < ctx.now
            and artifact.training_cutoff < artifact.validation_start
        ),
        "REGRESSION": (
            metrics.rmse <= inputs.maximum_oos_rmse
            and metrics.directional_accuracy >= inputs.minimum_directional_accuracy
            and metrics.improves_training_mean_baseline
        ),
    }
    refs, validations, reports = [], [], {}
    for kind, passed in checks.items():
        # Bind actual numeric measurements to the new PIT-reviewed artifact identity;
        # no coefficients, dataset, or validation windows are changed here.
        expected = {
            "version": "money-qlib-reviewed-validation-v1",
            "kind": kind,
            "passed": passed,
            "artifact_hash": artifact.artifact_hash,
            "dataset_hash": artifact.dataset_hash,
            "unpromoted_training_report_hash": report.report_hash,
            "source_review": list(source_ref),
            "training_validation": report.model_dump(mode="json"),
            "predeclared_criteria": {
                "maximum_oos_rmse": inputs.maximum_oos_rmse,
                "minimum_directional_accuracy": inputs.minimum_directional_accuracy,
                "require_baseline_improvement": inputs.require_baseline_improvement,
            },
        }
        checkpoint_path = f"outputs/qlib-validation-{content_hash(expected)}.json"
        checkpoint = ctx.read_json(checkpoint_path)
        if checkpoint is None:
            completed_at = ctx.now
            ref = ctx.artifact({**expected, "completed_at": completed_at.isoformat()})
            ctx.write_json(checkpoint_path, {"sha256": ref[0], "path": ref[1]})
        else:
            raw = _bytes(ctx, checkpoint["path"])
            if hashlib.sha256(raw).hexdigest() != checkpoint["sha256"]:
                raise ValueError("validation checkpoint bytes differ")
            stored = json.loads(raw)
            completed_at = datetime.fromisoformat(stored.pop("completed_at"))
            if stored != expected or not source.reviewed_at <= completed_at <= ctx.now:
                raise ValueError("validation checkpoint provenance differs")
            ref = (checkpoint["sha256"], checkpoint["path"])
        reports[ref[0]] = _bytes(ctx, ref[1])
        refs.append(ref)
        validations.append(
            OfflineValidationEvidence(
                kind=kind,
                passed=passed,
                tested_artifact_hash=artifact.artifact_hash,
                dataset_hash=artifact.dataset_hash,
                report_hash=ref[0],
                completed_at=completed_at,
            )
        )
    return tuple(validations), reports, refs


def _registry_activate(
    ctx: QualificationContext,
    model: QualifiedLinearModel,
    reports: Mapping[str, bytes],
    manual: ManualApproval,
) -> str:
    # Separate operator settings deliberately avoid the live-manifest bootstrap gate.
    settings = OperatorSettings(
        money_env="production",
        database_url=SecretStr(ctx.environ.get("DATABASE_URL", "")),
        money_workspace_id=ctx.environ.get("MONEY_WORKSPACE_ID", "private"),
    )
    store = ResearchStore(settings.database_url.get_secret_value(), allow_sqlite=False)
    try:
        registry = ModelRegistry(store)
        record_id = registry.register(model, reports)
        try:
            registry.load_active(record_id, model.artifact.artifact_hash, ctx.now)
        except StoreError:
            with store.engine.connect() as connection:
                latest = connection.scalar(
                    select(model_promotions.c.payload)
                    .where(model_promotions.c.model_id == record_id)
                    .order_by(
                        model_promotions.c.created_at.desc(),
                        model_promotions.c.payload["revision"].as_integer().desc(),
                    )
                    .limit(1)
                )
            if latest is not None:
                raise InactiveRegistryModel(
                    "previous activation no longer valid; never reactivate on resume"
                ) from None
            registry.promote(record_id, reviewer_id=manual.operator_reviewer_id, manual=True)
        # A successful insert is not evidence of active promotion.
        from money.schemas.contracts import utc_now

        registry.load_active(record_id, model.artifact.artifact_hash, utc_now())
        return record_id
    finally:
        store.engine.dispose()


def run_qlib_stage(ctx: QualificationContext) -> dict[str, Any]:
    output = _empty()
    ctx.template(
        "reviews/qlib-inputs.json",
        {
            "dataset_path": None,
            "archive_path": None,
            "historical_universe_path": None,
            "corporate_action_review_path": None,
            "configuration": None,
            "proposed_by": None,
            "maximum_oos_rmse": None,
            "minimum_directional_accuracy": None,
            "require_baseline_improvement": True,
            "adjustment_policy": None,
            "horizon_sessions": 1,
            "maximum_horizon_days": 30,
        },
    )
    ctx.template("reviews/schemas/qlib-inputs.schema.json", QlibInputs.model_json_schema())
    ctx.template("reviews/schemas/qlib-dataset.schema.json", TrainingDataset.model_json_schema())
    ctx.template(
        "reviews/schemas/qlib-archive.schema.json",
        TypeAdapter(dict[str, tuple[EvidenceRecord, ...]]).json_schema(),
    )
    try:
        inputs = QlibInputs.model_validate(ctx.read_json("reviews/qlib-inputs.json"))
        if inputs.configuration.evaluation_cutoff >= ctx.now:
            raise ValueError("evaluation requires completed historical outcomes")
        dataset = _verified_dataset(ctx, inputs)
    except (ValueError, OSError, KeyError, TypeError):
        ctx.block(
            "QLIB_ARCHIVED_INPUTS_REQUIRED",
            "Complete reviews/qlib-inputs.json with genuine "
            "archived bars, independently reviewed historical universe/actions and predeclared "
            "training/OOS criteria; generated schemas describe the exact contracts.",
        )
        return output
    try:
        result = _training_result(ctx, inputs, dataset)
        output["artifacts"].extend(
            [
                ctx.artifact(result.artifact.model_dump_json().encode()),
                ctx.artifact(result.validation.model_dump_json().encode()),
            ]
        )
    except ConsumedHoldout:
        ctx.block(
            "QLIB_HOLDOUT_ALREADY_CONSUMED",
            "This archived dataset already has a completed "
            "study with different predeclared inputs; supply a genuinely new untouched "
            "holdout/archive, not adjusted acceptance criteria for observed OOS results.",
        )
        return output
    except (ImportError, UpstreamUnavailable):
        ctx.block(
            "QLIB_NATIVE_TRAINING_REQUIRED",
            "Install the exact pinned Qlib runtime and its "
            "dependencies; "
            "no substitute solver or synthetic data is accepted.",
        )
        return output
    except (ValueError, OSError, KeyError, TypeError):
        ctx.block(
            "QLIB_TRAINING_EVIDENCE_INVALID",
            "The archived inputs cannot support the "
            "predeclared purged walk-forward/OOS study, or retained training provenance "
            "failed validation. Preserve failures and restore valid original evidence.",
        )
        return output
    try:
        source, source_ref = _source_review(ctx, inputs, dataset, result)
    except (ValueError, OSError, TypeError, KeyError):
        ctx.block(
            "QLIB_SOURCE_REVIEW_REQUIRED",
            f"An independent reviewer must complete "
            f"reviews/qlib-source-{result.artifact_hash}.json against real source evidence.",
        )
        return output
    artifact = LinearModelArtifact.model_validate(
        {**result.artifact.model_dump(), "pit_validated": True}
    )
    try:
        validations, reports, refs = _validation_evidence(
            ctx, inputs, result, artifact, source, source_ref
        )
    except (ValueError, OSError, KeyError, TypeError):
        ctx.block(
            "QLIB_VALIDATION_CHECKPOINT_INVALID",
            "Preserved validation receipts do not match "
            "the actual archived study; restore the independently retained artifact bytes.",
        )
        return output
    output["artifacts"].extend(
        [source_ref, *refs, ctx.artifact(artifact.model_dump_json().encode())]
    )
    failed = [validation.kind for validation in validations if not validation.passed]
    if failed:
        ctx.block(
            "QLIB_VALIDATION_FAILED",
            "Preserved measured failures: "
            + ", ".join(failed)
            + "; a new predeclared study and independent untouched holdout are required.",
        )
        return output
    approval_path = f"reviews/qlib-approval-{artifact.artifact_hash}.json"
    ctx.template(
        approval_path,
        {
            "approval": {
                "reviewer_id": None,
                "approved": False,
                "reviewed_artifact_hash": artifact.artifact_hash,
                "reviewed_validation_hashes": [v.report_hash for v in validations],
                "reviewed_at": None,
            },
            "manual_promote": False,
            "operator_reviewer_id": None,
        },
    )
    try:
        manual = ManualApproval.model_validate(ctx.read_json(approval_path))
        promotion = PromotionEvidence(
            artifact_id=f"{artifact.model_id}:{artifact.model_version}",
            artifact_hash=artifact.artifact_hash,
            proposed_by=inputs.proposed_by,
            validations=validations,
            approval=manual.approval,
        )
        model = QualifiedLinearModel(artifact=artifact, promotion=promotion)
        if (
            assess_promotion(promotion, ctx.now).state != "ELIGIBLE_FOR_MANUAL_PROMOTION"
            or manual.operator_reviewer_id != manual.approval.reviewer_id
        ):
            raise ValueError("manual independent approval is incomplete")
        model.require_qualified(ctx.now)
    except (ValueError, OSError, TypeError, UpstreamUnavailable):
        ctx.block(
            "QLIB_MANUAL_PROMOTION_REQUIRED",
            f"Independent authenticated operator: review "
            f"all actual validation bytes, complete {approval_path}, explicitly select "
            "manual_promote=true, and rerun. The proposer cannot approve their own model.",
        )
        return output
    try:
        record_id = _registry_activate(ctx, model, reports, manual)
    except InactiveRegistryModel:
        ctx.block(
            "QLIB_EXISTING_ACTIVATION_INVALID",
            "The selected model has an earlier activation "
            "that is no longer valid (including withdrawal). Resume will not override it; "
            "an independently approved new model version or separate explicit registry action is required.",
        )
        return output
    except (ValueError, OSError, StoreError, SQLAlchemyError):
        ctx.block(
            "QLIB_REGISTRY_UNAVAILABLE",
            "Provide network access to the existing migrated "
            "PostgreSQL database for ModelRegistry.register/manual promotion; this runner "
            "does not create databases or run migrations. Database credentials are not logged.",
        )
        return output
    model_ref = ctx.artifact(model.model_dump_json().encode())
    output["artifacts"].extend([model_ref, ctx.artifact(_bytes(ctx, approval_path))])
    output["manifest_fields"] = {
        "qlib_registry_id": record_id,
        "qlib_artifact_hash": artifact.artifact_hash,
    }
    output["production_environment"] = {
        "MONEY_QLIB_QUALIFIED_MODEL": str(ctx.root / model_ref[1]),
        "MONEY_QLIB_ARTIFACT_HASH": artifact.artifact_hash,
    }
    output["complete"] = True
    ctx.write_json(
        "outputs/qlib-state.json",
        {**output["manifest_fields"], "model_path": str(ctx.root / model_ref[1])},
    )
    return output


def _lean_reports(ctx: QualificationContext, snapshot: ResearchSnapshot) -> tuple[FirmReport, ...]:
    raw = ctx.read_json("outputs/first-pass.json")
    types: dict[str, type[FirmReport]] = {
        "tradingagents": TradingAgentsResearchReport,
        "ai_hedge_fund": AIHedgeFundResearchReport,
        "qlib": QlibQuantResearchReport,
    }
    if not isinstance(raw, list):
        raise ValueError("sealed first-pass reports are absent")
    reports = tuple(types[row["firm"]].model_validate(row) for row in raw)
    if (
        len(reports) != 3
        or {report.firm for report in reports} != set(types)
        or any(
            report.snapshot_id != snapshot.snapshot_id
            or report.snapshot_hash != snapshot.hash
            or report.runtime != "live"
            for report in reports
        )
    ):
        raise ValueError("LEAN requires all three independently sealed live first-pass reports")
    return reports


def _lean_audits(
    ctx: QualificationContext,
    inputs: LeanInputs,
    snapshot: ResearchSnapshot,
) -> tuple[LeanStudyQualification, list[tuple[str, str]]]:
    records = tuple(row for row in snapshot.evidence if isinstance(row.payload, PriceBar))
    dataset = historical_dataset_hash(records)
    refs: dict[str, tuple[str, str]] = {}
    extra: list[tuple[str, str]] = []
    for kind in ("historical_eligibility", "survivorship", "corporate_actions", "costs"):
        subject = content_hash(inputs.costs) if kind == "costs" else dataset
        path = f"reviews/lean-{kind}-{subject}.json"
        ctx.template(
            path,
            {
                "kind": kind,
                "approved": False,
                "reviewed_by": None,
                "reviewed_at": None,
                "dataset_hash": dataset,
                "subject_hash": subject,
                "adjustment_policy": inputs.adjustment_policy,
                "evidence_paths": [],
                "rationale": None,
            },
        )
        try:
            audit = HistoricalAudit.model_validate(ctx.read_json(path))
            if (
                audit.kind != kind
                or audit.dataset_hash != dataset
                or audit.subject_hash != subject
                or audit.adjustment_policy != inputs.adjustment_policy
                or audit.reviewed_at > inputs.approved_at
                or audit.reviewed_by.strip().casefold() == inputs.proposed_by.strip().casefold()
                or not audit.reviewed_by.strip()
            ):
                raise ValueError("audit does not bind independently reviewed study evidence")
            extra.extend(_copy_evidence(ctx, audit.evidence_paths))
            refs[kind] = ctx.artifact(_bytes(ctx, path))
        except (ValueError, TypeError, OSError):
            ctx.block(
                f"LEAN_{kind.upper()}_REVIEW_REQUIRED",
                f"Complete {path} using independently "
                "reviewed original evidence; current metadata is not historical membership proof.",
            )
    if len(refs) != 4:
        raise ValueError("historical audit evidence remains incomplete")
    qualification = LeanStudyQualification(
        parameter_hash=content_hash(inputs.parameters),
        dataset_hash=dataset,
        historical_eligibility_hash=refs["historical_eligibility"][0],
        survivorship_audit_hash=refs["survivorship"][0],
        corporate_action_audit_hash=refs["corporate_actions"][0],
        adjustment_policy=inputs.adjustment_policy,
        approved_by=inputs.approved_by,
        approved_at=inputs.approved_at,
    )
    return qualification, [*refs.values(), *extra]


def prepare_lean_inputs(ctx: QualificationContext) -> None:
    """Offer review templates without reading stale runtime output or executing LEAN."""
    ctx.template(
        "reviews/lean-inputs.json",
        {
            "container": None,
            "costs": None,
            "parameters": LeanStudyParameters(scenario_policy=SignalPolicy()).model_dump(
                mode="json"
            ),
            "adjustment_policy": None,
            "proposed_by": None,
            "approved_by": None,
            "approved_at": None,
        },
    )
    ctx.template("reviews/schemas/lean-inputs.schema.json", LeanInputs.model_json_schema())


def prepare_lean_audit_inputs(ctx: QualificationContext, snapshot: ResearchSnapshot) -> None:
    """Discover audit subjects before paid native runs, without asserting approval.

    The caller supplies the current successful snapshot; this helper deliberately
    does not load a previous run's outputs/snapshot.json.
    """
    raw = ctx.read_json("reviews/lean-inputs.json")
    if not isinstance(raw, dict):
        return
    try:
        costs = LeanCostAssumptions.model_validate(raw.get("costs"))
        adjustment: str = TypeAdapter(
            Literal["unadjusted_no_actions", "split_adjusted_total_return"]
        ).validate_python(raw.get("adjustment_policy"))
        dataset = historical_dataset_hash(
            tuple(row for row in snapshot.evidence if isinstance(row.payload, PriceBar))
        )
    except (ValueError, TypeError):
        return
    for kind in ("historical_eligibility", "survivorship", "corporate_actions", "costs"):
        subject = content_hash(costs) if kind == "costs" else dataset
        ctx.template(
            f"reviews/lean-{kind}-{subject}.json",
            {
                "kind": kind,
                "approved": False,
                "reviewed_by": None,
                "reviewed_at": None,
                "dataset_hash": dataset,
                "subject_hash": subject,
                "adjustment_policy": adjustment,
                "evidence_paths": [],
                "rationale": None,
            },
        )


def run_lean_stage(ctx: QualificationContext) -> dict[str, Any]:
    output = _empty()
    prepare_lean_inputs(ctx)
    try:
        inputs = LeanInputs.model_validate(ctx.read_json("reviews/lean-inputs.json"))
        if inputs.parameters.scenario_policy is None:
            raise ValueError(
                "production study requires an explicit matching signal scenario policy"
            )
        if (
            inputs.approved_at > ctx.now
            or inputs.proposed_by.strip().casefold() == inputs.approved_by.strip().casefold()
            or not inputs.approved_by.strip()
            or not inputs.proposed_by.strip()
        ):
            raise ValueError("study must be independently approved")
        snapshot = ResearchSnapshot.model_validate(ctx.read_json("outputs/snapshot.json"))
        if any(row.provider == "money-demo" for row in snapshot.evidence):
            raise ValueError("demo snapshots cannot qualify")
    except (ValueError, OSError, TypeError):
        ctx.block(
            "LEAN_STUDY_INPUTS_REQUIRED",
            "Complete reviews/lean-inputs.json with a real "
            "digest-pinned LEAN image, reviewed historical costs and predeclared study with "
            "an explicit scenario_policy matching its horizon; "
            "a genuine frozen outputs/snapshot.json is required (exact schema generated).",
        )
        return output
    try:
        qualification, refs = _lean_audits(ctx, inputs, snapshot)
    except (ValueError, OSError, TypeError):
        return output
    output["artifacts"].extend(refs)
    if inputs.approved_at > snapshot.created_at:
        ctx.block(
            "LEAN_APPROVAL_POSTDATES_SNAPSHOT",
            "Historical study approval must predate "
            "the frozen snapshot; rebuild the snapshot and all sealed first-pass reports "
            "after review. Never backdate the approval.",
        )
        return output
    try:
        reports = _lean_reports(ctx, snapshot)
    except (ValueError, OSError, KeyError, TypeError):
        ctx.block(
            "LEAN_FIRST_PASS_REQUIRED",
            "Complete the three independent native first-pass "
            "reports for this exact snapshot before LEAN; no fixture reports are accepted.",
        )
        return output
    config = {
        "container": inputs.container.model_dump(mode="json"),
        "costs": inputs.costs.model_dump(mode="json"),
        "parameters": inputs.parameters.model_dump(mode="json"),
        "qualification": qualification.model_dump(mode="json"),
    }
    run_key = content_hash(
        {
            "configuration": config,
            "snapshot": snapshot.hash,
            "reports": [content_hash(report) for report in reports],
        }
    )
    try:
        cached = ctx.read_json(f"outputs/lean-{run_key}.json")
        if cached is None:
            report = LeanContainerRunner(
                inputs.container, inputs.costs, inputs.parameters, qualification
            )(snapshot, reports)
            report_ref = ctx.artifact(report.model_dump_json().encode())
            receipt_ref = ctx.artifact(
                {
                    "kind": "money-lean-runtime-receipt-v1",
                    "request_hash": run_key,
                    "snapshot_hash": snapshot.hash,
                    "report": list(report_ref),
                }
            )
            ctx.write_json(
                f"outputs/lean-{run_key}.json", {"path": receipt_ref[1], "sha256": receipt_ref[0]}
            )
        else:
            payload = _bytes(ctx, cached["path"])
            if hashlib.sha256(payload).hexdigest() != cached["sha256"]:
                raise ValueError("cached LEAN receipt was altered")
            receipt = json.loads(payload)
            if (
                receipt.get("kind") != "money-lean-runtime-receipt-v1"
                or receipt.get("request_hash") != run_key
                or receipt.get("snapshot_hash") != snapshot.hash
            ):
                raise ValueError("cached LEAN receipt belongs to another request")
            report_ref = (receipt["report"][0], receipt["report"][1])
            report_bytes = _bytes(ctx, report_ref[1])
            if hashlib.sha256(report_bytes).hexdigest() != report_ref[0]:
                raise ValueError("cached LEAN report was altered")
            report = LeanValidationReport.model_validate_json(report_bytes)
            receipt_ref = (cached["sha256"], cached["path"])
        stats = json.loads(report.findings[0])
        if (
            report.snapshot_id != snapshot.snapshot_id
            or stats["dataset_hash"] != qualification.dataset_hash
            or stats["image"] != inputs.container.image
            or stats["upstream_sha"] != LEAN_SHA
            or stats["parameter_hash"] != qualification.parameter_hash
        ):
            raise ValueError("LEAN execution provenance differs")
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        UpstreamUnavailable,
        subprocess.SubprocessError,
    ):
        ctx.block(
            "LEAN_RUNTIME_REQUIRED",
            "Run the reviewed digest-pinned LEAN image with Docker "
            "and complete actual historical data; runtime failure is not a PASS.",
        )
        return output
    output["artifacts"].extend((report_ref, receipt_ref))
    ctx.write_json("outputs/lean-report.json", report.model_dump(mode="json"))
    if report.state != "PASS" or not all(
        (
            report.walk_forward,
            report.out_of_sample,
            report.pit_safe,
            report.survivorship_checked,
            report.costs_included,
            report.sensitivity_checked,
        )
    ):
        ctx.block(
            "LEAN_STUDY_NOT_PASSED",
            f"Actual LEAN state is {report.state}; inspect "
            "outputs/lean-report.json for walk-forward/OOS, regime, cost, drawdown and "
            "MAE/MFE results. Failed or insufficient evidence is preserved.",
        )
        return output
    config_ref = ctx.artifact(config)
    output["artifacts"].append(config_ref)
    output["manifest_fields"] = {
        "lean": config["container"],
        "lean_costs": config["costs"],
        "lean_parameters": config["parameters"],
        "lean_qualification": config["qualification"],
        "signal_policy": inputs.parameters.scenario_policy.model_dump(mode="json"),
    }
    output["production_environment"] = {
        "MONEY_LEAN_QUALIFICATION_CONFIG": str(ctx.root / config_ref[1]),
        "MONEY_NATIVE_QUALIFICATION_LEAN_REPORT": str(ctx.root / "outputs/lean-report.json"),
    }
    output["complete"] = True
    return output


def run_quant_stages(ctx: QualificationContext) -> dict[str, Any]:
    output = _empty()
    completed = []
    for stage in (run_qlib_stage, run_lean_stage):
        result = stage(ctx)
        output["manifest_fields"].update(result["manifest_fields"])
        output["artifacts"].extend(result["artifacts"])
        output["production_environment"].update(result["production_environment"])
        completed.append(result["complete"])
    output["complete"] = all(completed)
    return output
