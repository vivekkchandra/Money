"""Operator runner gates under synthetic, isolated test-only inputs.

None of these fixtures or substituted runtimes are production evidence.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from money.adapters.native_attestation import SOURCE_DIGESTS
from money.adapters.native_qlib import LinearModelArtifact
from money.adapters.upstream import UpstreamUnavailable
from money.backtest.lean import LEAN_SHA, historical_dataset_hash
from money.models.training import (
    TrainingConfiguration,
    TrainingDataset,
    TrainingResult,
    observation_from_evidence,
    train_baseline,
)
from money.qualification import quant
from money.qualification.core import QualificationContext
from money.schemas.contracts import (
    EvidenceRecord,
    InstrumentMetadata,
    LeanValidationReport,
    PriceBar,
    ResearchSnapshot,
    content_hash,
)
from money.signals.generation import SignalPolicy
from money.storage.store import StoreError


@pytest.fixture
def ctx(tmp_path: Path) -> QualificationContext:
    return QualificationContext(
        tmp_path / "qualification", tmp_path, {}, datetime(2026, 9, 17, tzinfo=UTC)
    )


def archive_records(count: int) -> tuple[EvidenceRecord, ...]:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    records = []
    for index in range(count):
        stamp = start + timedelta(days=index)
        price = Decimal("100") + Decimal(index) / 10
        records.append(
            EvidenceRecord(
                evidence_id=f"unit-{index}",
                snapshot_id="unit-snapshot",
                source="unit-test-only",
                provider="unit-test-only",
                source_id=f"unit:{index}",
                canonical_source_id=f"unit:{index}",
                observation_time=stamp,
                publication_time=stamp + timedelta(hours=1),
                retrieval_time=stamp + timedelta(hours=2),
                fresh_until=stamp + timedelta(days=1),
                pit_safe=True,
                payload=PriceBar(
                    open=price,
                    high=price + 1,
                    low=price - 1,
                    close=price + Decimal("0.2"),
                    volume=1000 + index,
                    currency="GBP",
                ),
            )
        )
    return tuple(records)


def archived_inputs(
    ctx: QualificationContext, count: int = 440
) -> tuple[quant.QlibInputs, TrainingDataset]:
    records = archive_records(count)
    ctx.write_json(
        "inputs/archive.json", {"UNIT.L": [row.model_dump(mode="json") for row in records]}
    )
    ctx.write_json("inputs/universe.json", {"notice": "unit-test historical universe only"})
    ctx.write_json("inputs/actions.json", {"notice": "unit-test actions only"})
    dataset = TrainingDataset(
        version="unit-test-only",
        origin="ARCHIVED_EVIDENCE",
        source_archive_hash=hashlib.sha256(ctx.read_bytes("inputs/archive.json")).hexdigest(),
        historical_universe_hash=hashlib.sha256(ctx.read_bytes("inputs/universe.json")).hexdigest(),
        corporate_action_review_hash=hashlib.sha256(
            ctx.read_bytes("inputs/actions.json")
        ).hexdigest(),
        adjustment_policy="unadjusted_no_actions",
        maximum_horizon_days=2,
        observations=tuple(
            observation_from_evidence(
                "UNIT.L",
                records[index].publication_time + timedelta(minutes=1),
                records[index - 20 : index + 1],
                records[index + 1],
                records[index + 1],
            )
            for index in range(20, count - 1)
        ),
    )
    ctx.write_json("inputs/dataset.json", dataset.model_dump(mode="json"))
    inputs = quant.QlibInputs(
        dataset_path="inputs/dataset.json",
        archive_path="inputs/archive.json",
        historical_universe_path="inputs/universe.json",
        corporate_action_review_path="inputs/actions.json",
        configuration=TrainingConfiguration(
            model_id="unit-model",
            model_version="1",
            training_cutoff=datetime(2020, 11, 1, tzinfo=UTC),
            evaluation_cutoff=datetime(2021, 4, 1, tzinfo=UTC),
        ),
        proposed_by="test-author",
        maximum_oos_rmse=1,
        minimum_directional_accuracy=0.5,
        require_baseline_improvement=True,
    )
    ctx.write_json("reviews/qlib-inputs.json", inputs.model_dump(mode="json"))
    return inputs, dataset


def native_test_result(dataset: TrainingDataset, config: TrainingConfiguration) -> TrainingResult:
    result = train_baseline(dataset, config)
    report = result.validation.model_copy(
        update={
            "solver": "pinned-qlib-standardized-ridge-v1",
            "native_source_hash": SOURCE_DIGESTS["qlib"],
        }
    )
    return TrainingResult(
        artifact=result.artifact,
        validation=report,
        artifact_hash=result.artifact_hash,
        validation_report_hash=report.report_hash,
    )


def test_missing_inputs_emit_reviews_not_models_or_qualification(ctx: QualificationContext) -> None:
    result = quant.run_quant_stages(ctx)
    assert result["manifest_fields"] == {}
    assert {item["code"] for item in ctx.blockers} == {
        "QLIB_ARCHIVED_INPUTS_REQUIRED",
        "LEAN_STUDY_INPUTS_REQUIRED",
    }
    assert ctx.read_json("reviews/qlib-inputs.json")["dataset_path"] is None
    assert ctx.read_json("reviews/lean-inputs.json")["container"] is None
    assert ctx.read_json("reviews/schemas/qlib-dataset.schema.json")["title"] == "TrainingDataset"
    assert not (ctx.root / "manifest.json").exists()


def test_resume_preserves_operator_edits(ctx: QualificationContext) -> None:
    ctx.write_json("reviews/qlib-inputs.json", {"operator_note": "do not overwrite"})
    quant.run_qlib_stage(ctx)
    assert ctx.read_json("reviews/qlib-inputs.json") == {"operator_note": "do not overwrite"}


def test_training_dataset_is_reconstructed_from_real_archived_bytes(
    ctx: QualificationContext,
) -> None:
    inputs, dataset = archived_inputs(ctx, 24)
    assert quant._verified_dataset(ctx, inputs) == dataset
    changed_row = dataset.observations[0].model_copy(update={"label_return": 0.999})
    changed = TrainingDataset.model_validate(
        {
            **dataset.model_dump(exclude={"hash", "observations"}),
            "observations": (changed_row, *dataset.observations[1:]),
        }
    )
    ctx.write_json(inputs.dataset_path, changed.model_dump(mode="json"))
    with pytest.raises(ValueError, match="differ from actual"):
        quant._verified_dataset(ctx, inputs)


def test_synthetic_origin_and_changed_proof_bytes_cannot_qualify(ctx: QualificationContext) -> None:
    inputs, dataset = archived_inputs(ctx, 24)
    forged = TrainingDataset.model_validate(
        {**dataset.model_dump(exclude={"hash"}), "origin": "SYNTHETIC_TEST"}
    )
    ctx.write_json(inputs.dataset_path, forged.model_dump(mode="json"))
    with pytest.raises(ValueError, match="synthetic"):
        quant._verified_dataset(ctx, inputs)
    ctx.write_json(inputs.dataset_path, dataset.model_dump(mode="json"))
    ctx.write_json(inputs.historical_universe_path, {"changed": "unit-only"})
    with pytest.raises(ValueError, match="proof hash differs"):
        quant._verified_dataset(ctx, inputs)


def test_unknown_native_runtime_stays_blocked(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    archived_inputs(ctx)

    def absent(_: str) -> None:
        raise UpstreamUnavailable("unit runtime absent")

    monkeypatch.setattr(quant, "require_pinned_source", absent)
    assert quant.run_qlib_stage(ctx)["manifest_fields"] == {}
    assert ctx.blockers[-1]["code"] == "QLIB_NATIVE_TRAINING_REQUIRED"


def test_training_resume_does_not_rerun_and_keeps_unpromoted(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs, dataset = archived_inputs(ctx)
    calls = []
    monkeypatch.setattr(quant, "require_pinned_source", lambda _: None)

    def train(data, config):
        calls.append(True)
        return native_test_result(data, config)

    monkeypatch.setattr(quant, "train_qlib", train)
    first = quant._training_result(ctx, inputs, dataset)
    second = quant._training_result(ctx, inputs, dataset)
    assert first == second and calls == [True]
    assert first.status == "UNPROMOTED" and first.artifact.pit_validated is False
    modified = inputs.model_copy(update={"maximum_oos_rmse": 100})
    with pytest.raises(ValueError, match="consumed this holdout"):
        quant._training_result(ctx, modified, dataset)


def test_baseline_substitution_is_rejected(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs, dataset = archived_inputs(ctx)
    monkeypatch.setattr(quant, "require_pinned_source", lambda _: None)
    monkeypatch.setattr(quant, "train_qlib", train_baseline)
    with pytest.raises(ValueError, match="native training provenance"):
        quant._training_result(ctx, inputs, dataset)


def test_independent_source_review_required_after_actual_training(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    archived_inputs(ctx)
    monkeypatch.setattr(quant, "require_pinned_source", lambda _: None)
    monkeypatch.setattr(quant, "train_qlib", native_test_result)
    result = quant.run_qlib_stage(ctx)
    assert result["manifest_fields"] == {}
    assert ctx.blockers[-1]["code"] == "QLIB_SOURCE_REVIEW_REQUIRED"
    reviews = list((ctx.root / "reviews").glob("qlib-source-*.json"))
    assert len(reviews) == 1
    review = json.loads(reviews[0].read_bytes())
    assert review["approved"] is False and review["reviewed_by"] is None
    for digest, path in result["artifacts"]:
        assert hashlib.sha256(ctx.read_bytes(path)).hexdigest() == digest


def lean_setup(ctx: QualificationContext) -> tuple[quant.LeanInputs, ResearchSnapshot]:
    records = archive_records(100)
    metadata = InstrumentMetadata(
        ticker="UNIT.L",
        company="Unit fixture",
        instrument_type="STOCK",
        quote_currency="GBP",
        isa_available=True,
        currently_available=True,
        business_activities=("unit-testing",),
        activities_verified=True,
        verified_at=ctx.now - timedelta(hours=1),
        source="unit-test-only",
        provider="unit-test-only",
        source_id="unit:test",
    )
    snapshot = ResearchSnapshot(
        snapshot_id="unit-snapshot",
        ticker="UNIT.L",
        created_at=ctx.now,
        price_cutoff=ctx.now,
        news_cutoff=ctx.now,
        filing_cutoff=ctx.now,
        fundamental_cutoff=ctx.now,
        instrument=metadata,
        evidence=records,
    )
    ctx.write_json("outputs/snapshot.json", snapshot.model_dump(mode="json"))
    image = "unit-test.invalid/lean@sha256:" + hashlib.sha256(b"unit-image-only").hexdigest()
    inputs = quant.LeanInputs.model_validate(
        {
            "container": {"image": image},
            "costs": {
                "version": "unit-cost-v1",
                "source": "unit-fixture",
                "effective_from": "2019-01-01T00:00:00Z",
                "effective_to": "2030-01-01T00:00:00Z",
                "round_trip_cost_bps": 10,
                "spread_bps": 5,
                "slippage_bps": 5,
                "applicability_reasons": ["unit-only"],
            },
            "parameters": {"scenario_policy": SignalPolicy().model_dump(mode="json")},
            "adjustment_policy": "unadjusted_no_actions",
            "proposed_by": "unit-author",
            "approved_by": "unit-reviewer",
            "approved_at": ctx.now - timedelta(hours=1),
        }
    )
    ctx.write_json("reviews/lean-inputs.json", inputs.model_dump(mode="json"))
    return inputs, snapshot


def reviewed_lean_audits(
    ctx: QualificationContext, inputs: quant.LeanInputs, snapshot: ResearchSnapshot
) -> None:
    dataset_hash = historical_dataset_hash(snapshot.evidence)
    ctx.write_json("inputs/unit-audit-source.json", {"notice": "test evidence only"})
    for kind in ("historical_eligibility", "survivorship", "corporate_actions", "costs"):
        subject = content_hash(inputs.costs) if kind == "costs" else dataset_hash
        ctx.write_json(
            f"reviews/lean-{kind}-{subject}.json",
            {
                "kind": kind,
                "approved": True,
                "reviewed_by": "unit-reviewer",
                "reviewed_at": inputs.approved_at.isoformat(),
                "dataset_hash": dataset_hash,
                "subject_hash": subject,
                "adjustment_policy": inputs.adjustment_policy,
                "evidence_paths": ["inputs/unit-audit-source.json"],
                "rationale": "Synthetic unit test review; not a real production assertion.",
            },
        )


def test_lean_generates_all_historical_audit_templates(ctx: QualificationContext) -> None:
    lean_setup(ctx)
    result = quant.run_lean_stage(ctx)
    assert result["manifest_fields"] == {}
    assert {item["code"] for item in ctx.blockers} == {
        "LEAN_HISTORICAL_ELIGIBILITY_REVIEW_REQUIRED",
        "LEAN_SURVIVORSHIP_REVIEW_REQUIRED",
        "LEAN_CORPORATE_ACTIONS_REVIEW_REQUIRED",
        "LEAN_COSTS_REVIEW_REQUIRED",
    }


def test_lean_audits_hash_actual_review_bytes(ctx: QualificationContext) -> None:
    inputs, snapshot = lean_setup(ctx)
    reviewed_lean_audits(ctx, inputs, snapshot)
    qualification, refs = quant._lean_audits(ctx, inputs, snapshot)
    assert qualification.dataset_hash == historical_dataset_hash(snapshot.evidence)
    assert qualification.parameter_hash == content_hash(inputs.parameters)
    for digest, path in refs:
        assert hashlib.sha256(ctx.read_bytes(path)).hexdigest() == digest


def test_lean_refuses_missing_first_pass_and_does_not_invoke_docker(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs, snapshot = lean_setup(ctx)
    reviewed_lean_audits(ctx, inputs, snapshot)
    monkeypatch.setattr(
        quant, "LeanContainerRunner", lambda *_: pytest.fail("must not run without first pass")
    )
    assert quant.run_lean_stage(ctx)["manifest_fields"] == {}
    assert ctx.blockers[-1]["code"] == "LEAN_FIRST_PASS_REQUIRED"


@pytest.mark.parametrize("state", ["FAIL", "INSUFFICIENT_EVIDENCE"])
def test_actual_lean_failure_is_preserved(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    inputs, snapshot = lean_setup(ctx)
    reviewed_lean_audits(ctx, inputs, snapshot)
    monkeypatch.setattr(quant, "_lean_reports", lambda *_: ())
    report = LeanValidationReport(
        snapshot_id=snapshot.snapshot_id,
        runner_version="unit-test-only",
        state=state,
        observations=50,
        walk_forward=True,
        out_of_sample=True,
        pit_safe=True,
        survivorship_checked=True,
        costs_included=True,
        sensitivity_checked=True,
        maximum_drawdown=0.8,
        mae=-0.7,
        mfe=0.1,
        findings=(
            json.dumps(
                {
                    "dataset_hash": historical_dataset_hash(snapshot.evidence),
                    "parameter_hash": content_hash(inputs.parameters),
                    "image": inputs.container.image,
                    "upstream_sha": LEAN_SHA,
                }
            ),
        ),
    )
    monkeypatch.setattr(quant, "LeanContainerRunner", lambda *_: lambda *_: report)
    result = quant.run_lean_stage(ctx)
    assert result["manifest_fields"] == {}
    assert ctx.blockers[-1]["code"] == "LEAN_STUDY_NOT_PASSED"
    assert ctx.read_json("outputs/lean-report.json")["state"] == state


def test_lean_refuses_self_approval(ctx: QualificationContext) -> None:
    inputs, _ = lean_setup(ctx)
    ctx.write_json(
        "reviews/lean-inputs.json",
        {**inputs.model_dump(mode="json"), "approved_by": inputs.proposed_by.upper()},
    )
    assert quant.run_lean_stage(ctx)["manifest_fields"] == {}
    assert ctx.blockers[-1]["code"] == "LEAN_STUDY_INPUTS_REQUIRED"


def test_qlib_validation_cannot_rewrite_numeric_failure(ctx: QualificationContext) -> None:
    inputs, dataset = archived_inputs(ctx)
    result = native_test_result(dataset, inputs.configuration)
    source = quant.QlibSourceReview(
        approved=True,
        reviewed_by="unit-reviewer",
        reviewed_at=ctx.now,
        unpromoted_artifact_hash=result.artifact_hash,
        training_report_hash=result.validation_report_hash,
        source_archive_hash=dataset.source_archive_hash,
        historical_universe_hash=dataset.historical_universe_hash,
        corporate_action_review_hash=dataset.corporate_action_review_hash,
        original_publication_availability_verified=True,
        historical_universe_and_delistings_verified=True,
        corporate_actions_and_adjustments_verified=True,
        untouched_holdout_and_predeclared_configuration_verified=True,
        evidence_paths=("inputs/universe.json",),
        rationale="Test-only independent review record.",
    )
    source_ref = ctx.artifact(source.model_dump(mode="json"))
    artifact = LinearModelArtifact.model_validate(
        {**result.artifact.model_dump(), "pit_validated": True}
    )
    impossible = inputs.model_copy(update={"maximum_oos_rmse": 1e-20})
    validations, reports, _ = quant._validation_evidence(
        ctx, impossible, result, artifact, source, source_ref
    )
    assert next(item for item in validations if item.kind == "REGRESSION").passed is False
    for item in validations:
        assert item.tested_artifact_hash == artifact.artifact_hash
        assert hashlib.sha256(reports[item.report_hash]).hexdigest() == item.report_hash
    repeat = quant._validation_evidence(ctx, impossible, result, artifact, source, source_ref)
    assert repeat[0] == validations


def complete_source_review(ctx: QualificationContext) -> Path:
    path = next((ctx.root / "reviews").glob("qlib-source-*.json"))
    review = json.loads(path.read_bytes())
    for name in tuple(review):
        if name.endswith("_verified"):
            review[name] = True
    review.update(
        approved=True,
        reviewed_by="unit-independent",
        reviewed_at=ctx.now.isoformat(),
        evidence_paths=["inputs/universe.json"],
        rationale="Only a synthetic unit-test review.",
    )
    ctx.write_json(path.relative_to(ctx.root).as_posix(), review)
    return path


def test_source_review_alone_does_not_manually_activate(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    archived_inputs(ctx)
    monkeypatch.setattr(quant, "require_pinned_source", lambda _: None)
    monkeypatch.setattr(quant, "train_qlib", native_test_result)
    monkeypatch.setattr(
        quant, "_registry_activate", lambda *_: pytest.fail("approval cannot be automatic")
    )
    quant.run_qlib_stage(ctx)
    complete_source_review(ctx)
    result = quant.run_qlib_stage(ctx)
    assert result["complete"] is False
    assert ctx.blockers[-1]["code"] == "QLIB_MANUAL_PROMOTION_REQUIRED"
    approval = json.loads(next((ctx.root / "reviews").glob("qlib-approval-*.json")).read_bytes())
    assert approval["manual_promote"] is False
    assert approval["approval"]["approved"] is False
    assert len(approval["approval"]["reviewed_validation_hashes"]) == 4


def test_manual_approval_still_requires_registry_activation(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    archived_inputs(ctx)
    monkeypatch.setattr(quant, "require_pinned_source", lambda _: None)
    monkeypatch.setattr(quant, "train_qlib", native_test_result)
    quant.run_qlib_stage(ctx)
    complete_source_review(ctx)
    quant.run_qlib_stage(ctx)
    path = next((ctx.root / "reviews").glob("qlib-approval-*.json"))
    approval = json.loads(path.read_bytes())
    approval["approval"].update(
        reviewer_id="unit-independent", approved=True, reviewed_at=ctx.now.isoformat()
    )
    approval.update(manual_promote=True, operator_reviewer_id="unit-independent")
    ctx.write_json(path.relative_to(ctx.root).as_posix(), approval)

    def unavailable(*_):
        raise OSError("unit database unavailable")

    monkeypatch.setattr(quant, "_registry_activate", unavailable)
    blocked = quant.run_qlib_stage(ctx)
    assert blocked["complete"] is False and blocked["manifest_fields"] == {}
    assert ctx.blockers[-1]["code"] == "QLIB_REGISTRY_UNAVAILABLE"
    assert ctx.read_json("outputs/qlib-state.json") is None
    calls = []

    def activated(_ctx, model, reports, manual):
        assert manual.manual_promote is True
        assert model.artifact.pit_validated is True
        assert len(reports) == 4
        calls.append(model.artifact.artifact_hash)
        return "unit-model:1"

    monkeypatch.setattr(quant, "_registry_activate", activated)
    succeeded = quant.run_qlib_stage(ctx)
    assert succeeded["complete"] is True
    assert succeeded["manifest_fields"] == {
        "qlib_registry_id": "unit-model:1",
        "qlib_artifact_hash": calls[0],
    }
    assert Path(ctx.read_json("outputs/qlib-state.json")["model_path"]).is_absolute()


def test_independent_review_cannot_predate_training(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    archived_inputs(ctx)
    monkeypatch.setattr(quant, "require_pinned_source", lambda _: None)
    monkeypatch.setattr(quant, "train_qlib", native_test_result)
    quant.run_qlib_stage(ctx)
    path = complete_source_review(ctx)
    review = json.loads(path.read_bytes())
    review["reviewed_at"] = (ctx.now - timedelta(days=1)).isoformat()
    ctx.write_json(path.relative_to(ctx.root).as_posix(), review)
    # Mutable cache metadata must not be able to backdate actual completed
    # training and thereby approve a review that preceded it.
    checkpoint_path = next((ctx.root / "outputs").glob("qlib-training-*.json"))
    checkpoint = json.loads(checkpoint_path.read_bytes())
    checkpoint["completed_at"] = (ctx.now - timedelta(days=2)).isoformat()
    ctx.write_json(checkpoint_path.relative_to(ctx.root).as_posix(), checkpoint)
    assert quant.run_qlib_stage(ctx)["complete"] is False
    assert ctx.blockers[-1]["code"] == "QLIB_SOURCE_REVIEW_REQUIRED"


def test_resume_never_reactivates_withdrawn_registry_model(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx.environ = {"DATABASE_URL": "postgresql://unit-only@127.0.0.1/unit-only"}
    store = MagicMock()
    store.engine.connect.return_value.__enter__.return_value.scalar.return_value = {
        "state": "WITHDRAWN"
    }
    registry = MagicMock()
    registry.register.return_value = "unit-model:1"
    registry.load_active.side_effect = StoreError("unit withdrawn")
    monkeypatch.setattr(quant, "ResearchStore", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(quant, "ModelRegistry", lambda _: registry)
    model = SimpleNamespace(
        artifact=SimpleNamespace(artifact_hash=hashlib.sha256(b"unit-model").hexdigest())
    )
    manual = SimpleNamespace(operator_reviewer_id="unit-independent")
    with pytest.raises(StoreError, match="never reactivate"):
        quant._registry_activate(ctx, model, {}, manual)
    registry.promote.assert_not_called()
    store.engine.dispose.assert_called_once()


def test_historical_templates_can_precede_final_approval(ctx: QualificationContext) -> None:
    inputs, snapshot = lean_setup(ctx)
    draft = inputs.model_dump(mode="json")
    draft["approved_by"] = None
    draft["approved_at"] = None
    ctx.write_json("reviews/lean-inputs.json", draft)
    quant.prepare_lean_audit_inputs(ctx, snapshot)
    templates = [
        path
        for path in (ctx.root / "reviews").glob("lean-*.json")
        if path.name != "lean-inputs.json"
    ]
    assert len(templates) == 4
    assert all(json.loads(path.read_bytes())["approved"] is False for path in templates)
    assert not (ctx.root / "outputs/lean-report.json").exists()


def test_derive_dataset_from_actual_archive_without_handwritten_features(
    ctx: QualificationContext,
) -> None:
    inputs, _ = archived_inputs(ctx, 60)
    automatic = inputs.model_copy(
        update={"dataset_path": None, "adjustment_policy": "unadjusted_no_actions"}
    )
    derived = quant._verified_dataset(ctx, automatic)
    assert len(derived.observations) == 39
    assert derived.origin == "ARCHIVED_EVIDENCE"
    assert all(
        row.feature_available_at == row.prediction_time < row.label_start
        for row in derived.observations
    )
    receipt = ctx.read_json("outputs/qlib-derived-dataset.json")
    assert receipt["status"] == "UNREVIEWED_UNPROMOTED"
    assert hashlib.sha256(ctx.read_bytes(receipt["dataset_path"])).hexdigest() == derived.hash
    assert TrainingDataset.model_validate_json(ctx.read_bytes(receipt["dataset_path"])) == derived
    assert quant._verified_dataset(ctx, automatic) == derived


def test_auto_derivation_never_infers_adjustment_policy(ctx: QualificationContext) -> None:
    inputs, _ = archived_inputs(ctx, 60)
    with pytest.raises(ValueError, match="explicitly reviewed adjustment"):
        quant._verified_dataset(ctx, inputs.model_copy(update={"dataset_path": None}))


def test_auto_derivation_rejects_unknown_original_availability(ctx: QualificationContext) -> None:
    inputs, _ = archived_inputs(ctx, 60)
    rows = list(archive_records(60))
    invalid = EvidenceRecord.model_validate(
        {**rows[25].model_dump(exclude={"hash"}), "publication_time": None, "pit_safe": False}
    )
    rows[25] = invalid
    ctx.write_json(inputs.archive_path, {"UNIT.L": [row.model_dump(mode="json") for row in rows]})
    with pytest.raises(ValueError, match="original-publication PIT"):
        quant._verified_dataset(
            ctx,
            inputs.model_copy(
                update={"dataset_path": None, "adjustment_policy": "unadjusted_no_actions"}
            ),
        )


def test_auto_derivation_excludes_late_publication_without_backdating(
    ctx: QualificationContext,
) -> None:
    inputs, _ = archived_inputs(ctx, 60)
    rows = list(archive_records(60))
    late = rows[25].observation_time + timedelta(days=7, hours=1)
    rows[25] = EvidenceRecord.model_validate(
        {
            **rows[25].model_dump(exclude={"hash"}),
            "publication_time": late,
            "retrieval_time": late + timedelta(hours=1),
        }
    )
    ctx.write_json(inputs.archive_path, {"UNIT.L": [row.model_dump(mode="json") for row in rows]})
    derived = quant._verified_dataset(
        ctx,
        inputs.model_copy(
            update={"dataset_path": None, "adjustment_policy": "unadjusted_no_actions"}
        ),
    )
    receipt = ctx.read_json("outputs/qlib-derived-dataset.json")
    assert receipt["excluded_late_or_out_of_horizon_windows"] == 7
    assert len(derived.observations) == 32
    assert all(
        row.prediction_time >= late
        for row in derived.observations
        if rows[25].hash in row.feature_evidence_hashes
    )


def test_lean_resume_reuses_exact_hashed_completed_runtime(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs, snapshot = lean_setup(ctx)
    inputs = inputs.model_copy(
        update={
            "parameters": inputs.parameters.model_copy(
                update={
                    "scenario_policy": SignalPolicy(
                        version="unit-reviewed-policy-v2", target_atr=Decimal("7")
                    )
                }
            )
        }
    )
    ctx.write_json("reviews/lean-inputs.json", inputs.model_dump(mode="json"))
    reviewed_lean_audits(ctx, inputs, snapshot)
    monkeypatch.setattr(quant, "_lean_reports", lambda *_: ())
    report = LeanValidationReport(
        snapshot_id=snapshot.snapshot_id,
        runner_version="unit-test-only",
        state="PASS",
        observations=50,
        walk_forward=True,
        out_of_sample=True,
        pit_safe=True,
        survivorship_checked=True,
        costs_included=True,
        sensitivity_checked=True,
        maximum_drawdown=0.1,
        mae=-0.05,
        mfe=0.1,
        findings=(
            json.dumps(
                {
                    "dataset_hash": historical_dataset_hash(snapshot.evidence),
                    "parameter_hash": content_hash(inputs.parameters),
                    "image": inputs.container.image,
                    "upstream_sha": LEAN_SHA,
                }
            ),
        ),
    )
    calls = []

    def invoke(*_):
        calls.append(True)
        return report

    monkeypatch.setattr(quant, "LeanContainerRunner", lambda *_: invoke)
    first = quant.run_lean_stage(ctx)
    second = quant.run_lean_stage(ctx)
    assert calls == [True]
    assert first["complete"] is True and second["complete"] is True
    assert first["manifest_fields"] == second["manifest_fields"]
    assert first["manifest_fields"][
        "signal_policy"
    ] == inputs.parameters.scenario_policy.model_dump(mode="json")
    cache_path = next((ctx.root / "outputs").glob("lean-*.json"))
    if cache_path.name == "lean-report.json":
        cache_path = next(
            path
            for path in (ctx.root / "outputs").glob("lean-*.json")
            if path.name != "lean-report.json"
        )
    receipt = json.loads(cache_path.read_bytes())
    ctx.write_json(receipt["path"], {"tampered": True})
    assert quant.run_lean_stage(ctx)["complete"] is False
    assert ctx.blockers[-1]["code"] == "LEAN_RUNTIME_REQUIRED"


def test_production_lean_requires_explicit_signal_scenario(ctx: QualificationContext) -> None:
    inputs, _ = lean_setup(ctx)
    raw = inputs.model_dump(mode="json")
    raw["parameters"]["scenario_policy"] = None
    ctx.write_json("reviews/lean-inputs.json", raw)
    assert quant.run_lean_stage(ctx)["complete"] is False
    assert ctx.blockers[-1]["code"] == "LEAN_STUDY_INPUTS_REQUIRED"


def test_lean_cannot_rebind_valid_report_bytes_to_a_different_request(
    ctx: QualificationContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs, snapshot = lean_setup(ctx)
    reviewed_lean_audits(ctx, inputs, snapshot)
    monkeypatch.setattr(quant, "_lean_reports", lambda *_: ())
    report = LeanValidationReport(
        snapshot_id=snapshot.snapshot_id,
        runner_version="unit-test-only",
        state="PASS",
        observations=50,
        walk_forward=True,
        out_of_sample=True,
        pit_safe=True,
        survivorship_checked=True,
        costs_included=True,
        sensitivity_checked=True,
        findings=(
            json.dumps(
                {
                    "dataset_hash": historical_dataset_hash(snapshot.evidence),
                    "parameter_hash": content_hash(inputs.parameters),
                    "image": inputs.container.image,
                    "upstream_sha": LEAN_SHA,
                }
            ),
        ),
    )
    monkeypatch.setattr(quant, "LeanContainerRunner", lambda *_: lambda *_: report)
    first = quant.run_lean_stage(ctx)
    assert first["complete"] is True
    original_path = next(
        path
        for path in (ctx.root / "outputs").glob("lean-*.json")
        if path.name != "lean-report.json"
    )
    original_checkpoint = json.loads(original_path.read_bytes())
    changed = ResearchSnapshot.model_validate(
        {
            **snapshot.model_dump(exclude={"hash"}),
            "news_cutoff": snapshot.news_cutoff - timedelta(days=1),
        }
    )
    ctx.write_json("outputs/snapshot.json", changed.model_dump(mode="json"))
    config = {
        "container": first["manifest_fields"]["lean"],
        "costs": first["manifest_fields"]["lean_costs"],
        "parameters": first["manifest_fields"]["lean_parameters"],
        "qualification": first["manifest_fields"]["lean_qualification"],
    }
    new_key = content_hash({"configuration": config, "snapshot": changed.hash, "reports": []})
    ctx.write_json(f"outputs/lean-{new_key}.json", original_checkpoint)
    monkeypatch.setattr(
        quant,
        "LeanContainerRunner",
        lambda *_: pytest.fail("must reject mismatched cached receipt"),
    )
    assert quant.run_lean_stage(ctx)["complete"] is False
    assert ctx.blockers[-1]["code"] == "LEAN_RUNTIME_REQUIRED"
