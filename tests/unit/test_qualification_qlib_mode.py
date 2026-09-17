"""Synthetic mode-control tests, never native production evidence."""

import runpy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from money.adapters.upstream import UPSTREAM_SHAS
from money.qualification import native, quant, runner
from money.qualification.core import CommandResult, QualificationContext
from money.research.qlib_mode import qlib_enabled
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    ResearchSnapshot,
    TradingAgentsResearchReport,
    Usage,
)


@pytest.fixture
def ctx(tmp_path):
    return QualificationContext(
        tmp_path / "qualification",
        Path(__file__).resolve().parents[2],
        {"MONEY_QLIB_ENABLED": "false"},
        datetime.now(UTC),
    )


def test_flag_defaults_enabled_and_requires_explicit_boolean_text():
    assert qlib_enabled({}) is True
    assert qlib_enabled({"MONEY_QLIB_ENABLED": "true"}) is True
    assert qlib_enabled({"MONEY_QLIB_ENABLED": "false"}) is False
    for value in ("", "0", "1", "FALSE", " false ", "accidental-secret"):
        with pytest.raises(ValueError, match="REQUIRES_TRUE_OR_FALSE") as exc:
            qlib_enabled({"MONEY_QLIB_ENABLED": value})
        assert "accidental-secret" not in str(exc.value)


def test_disabled_qlib_preserves_old_evidence_and_never_trains_or_promotes(ctx, monkeypatch):
    ctx.write_json("outputs/qlib-state.json", {"historical": "not-selected"})
    ctx.write_json("reviews/qlib-inputs.json", {"operator": "retain-this"})
    monkeypatch.setattr(quant, "train_qlib", lambda *_: pytest.fail("Qlib is disabled"))
    monkeypatch.setattr(quant, "_registry_activate", lambda *_: pytest.fail("Qlib is disabled"))
    result = quant.run_qlib_stage(ctx)
    assert result["complete"] is True and result["status"] == "DISABLED"
    assert result["manifest_fields"] == {"qlib_enabled": False}
    assert result["production_environment"] == {}
    assert ctx.blockers == []
    assert ctx.read_json("outputs/qlib-state.json") == {"historical": "not-selected"}
    assert ctx.read_json("reviews/qlib-inputs.json") == {"operator": "retain-this"}


def test_enabled_qlib_failure_does_not_automatically_disable_it(ctx):
    ctx.environ = {}
    result = quant.run_qlib_stage(ctx)
    assert result["complete"] is False and result["qlib_enabled"] is True
    assert "QLIB_ARCHIVED_INPUTS_REQUIRED" in {item["code"] for item in ctx.blockers}


def test_disabling_qlib_does_not_disable_lean(ctx):
    result = quant.run_quant_stages(ctx)
    assert result["complete"] is False
    assert result["manifest_fields"] == {"qlib_enabled": False}
    assert {item["code"] for item in ctx.blockers} == {"LEAN_STUDY_INPUTS_REQUIRED"}


def test_disabled_native_source_pin_skips_only_qlib_and_keeps_security_egress(ctx, monkeypatch):
    checked = []
    monkeypatch.setattr(native, "_inventory", lambda: [("chromadb", "test-version")])
    monkeypatch.setattr(native, "require_pinned_source", lambda package: checked.append(package))
    monkeypatch.setattr(
        native, "_resolve_native_closure", lambda *_: {"resolved": True, "artifacts": []}
    )
    monkeypatch.setattr(
        native,
        "_security",
        lambda *_: {
            "passed": False,
            "findings": [{"name": "chromadb", "advisories": ["TEST-ONLY-ADVISORY"]}],
            "artifacts": [],
        },
    )
    egress_calls = []

    def egress(*args):
        egress_calls.append(args)
        return {"complete": False, "artifacts": [], "manifest_fields": {}}

    monkeypatch.setattr(native, "_egress", egress)
    result = native.run_native_preflight_stage(ctx)
    assert result["complete"] is False and result["qlib_enabled"] is False
    assert set(checked) == set(native.SOURCE_DIGESTS) - {"qlib"}
    assert len(egress_calls) == 1
    assert "CHROMADB_SECURITY_ADVISORIES" in {item["code"] for item in ctx.blockers}


def test_dependency_closure_fingerprints_mode_and_omits_only_qlib_source(ctx, monkeypatch):
    checked = []
    original = native.source_fingerprint

    def fingerprint(path, package):
        checked.append(package)
        return original(path, package)

    monkeypatch.setattr(native, "source_fingerprint", fingerprint)
    calls = []

    def command(*args):
        calls.append(args)
        return CommandResult(1, b"No solution found: synthetic test resolver conflict")

    monkeypatch.setattr(native, "_command", command)
    disabled = native._resolve_native_closure(ctx)
    assert "qlib" not in checked
    assert "upstreams/qlib/pyproject.toml" not in disabled["input_sha256"]
    assert disabled["qlib_enabled"] is False
    ctx.environ = {"MONEY_QLIB_ENABLED": "true"}
    enabled = native._resolve_native_closure(ctx)
    assert "qlib" in checked
    assert "upstreams/qlib/pyproject.toml" in enabled["input_sha256"]
    assert enabled["qlib_enabled"] is True and len(calls) == 2


def two_firm_inputs(ctx, monkeypatch):
    from test_live_data_scanners import technical_snapshot

    original = technical_snapshot()
    snapshot = ResearchSnapshot.model_validate(
        {**original.model_dump(), "qlib_enabled": False, "hash": ""}
    )
    selections = native._selections(ctx)
    config = {firm: selected.model_dump() for firm, selected in selections.items()}
    config.update(native_timeout_seconds=600, native_max_calls=16)
    preflight = {
        "execution_identity": "synthetic-test-runtime",
        "security": {"artifacts": []},
        "manifest_fields": {},
        "qlib_enabled": False,
    }
    monkeypatch.setattr(
        native, "_execution_inputs", lambda *_: ({"manifest_fields": config}, preflight, snapshot)
    )
    monkeypatch.setattr(native, "_active_model", lambda *_: pytest.fail("Qlib model must not load"))
    monkeypatch.setattr(native, "QlibNativeRunner", lambda *_: pytest.fail("Qlib must not run"))
    monkeypatch.setattr(
        native,
        "_inference",
        lambda *_: SimpleNamespace(allowed_network_hosts=(), allowed_network_port=443),
    )
    monkeypatch.setattr(native, "BoundedNativeRunner", lambda engine, *_: engine)
    created = []
    for firm, schema, name in (
        ("tradingagents", TradingAgentsResearchReport, "TradingAgentsNativeRunner"),
        ("ai_hedge_fund", AIHedgeFundResearchReport, "AIHedgeFundNativeRunner"),
    ):

        def factory(*_, firm=firm, schema=schema):
            def research(*_):
                created.append(firm)
                return schema(
                    snapshot_id=snapshot.snapshot_id,
                    snapshot_hash=snapshot.hash,
                    conclusion="Synthetic mode-control test",
                    claims=(),
                    model_version="test-only",
                    prompt_version="test-only",
                    model_family="test-only",
                    upstream_sha=UPSTREAM_SHAS[firm],
                    created_at=snapshot.created_at,
                    usage=Usage(input_tokens=1, output_tokens=1),
                )

            return research

        monkeypatch.setattr(native, name, factory)
    return snapshot, created


def test_disabled_first_pass_seals_two_real_reports_and_reuses_only_matching_mode(ctx, monkeypatch):
    snapshot, created = two_firm_inputs(ctx, monkeypatch)
    result = native.run_first_pass_stage(ctx)
    assert result["complete"] is True and result["qlib_enabled"] is False
    assert {report["firm"] for report in result["reports"]} == snapshot.required_first_pass_firms
    assert created == ["tradingagents", "ai_hedge_fund"]
    assert native.run_first_pass_stage(ctx)["complete"] is True
    assert len(created) == 2
    ctx.environ = {"MONEY_QLIB_ENABLED": "true"}
    with pytest.raises(ValueError, match="QLIB_MODE_SNAPSHOT_MISMATCH"):
        native.run_first_pass_stage(ctx)


def test_lean_requires_two_or_three_exact_reports_from_frozen_mode(ctx, monkeypatch):
    snapshot, _ = two_firm_inputs(ctx, monkeypatch)
    native.run_first_pass_stage(ctx)
    assert len(quant._lean_reports(ctx, snapshot)) == 2
    ctx.environ = {"MONEY_QLIB_ENABLED": "true"}
    with pytest.raises(ValueError, match="QLIB_MODE_SNAPSHOT_MISMATCH"):
        quant._lean_reports(ctx, snapshot)


def test_release_inputs_explicitly_bind_disabled_mode_and_reject_conflicting_stage(ctx):
    assert runner.assemble_manifest(ctx, []) is None
    assert ctx.read_json("outputs/release-inputs.json")["manifest_fields"]["qlib_enabled"] is False
    with pytest.raises(ValueError, match="STAGE_FIELD_CONFLICT"):
        runner.assemble_manifest(ctx, [{"manifest_fields": {"qlib_enabled": True}}])


def test_disabled_qlib_runner_still_executes_lean_and_blocks_cio_on_failure(ctx, monkeypatch):
    from test_live_data_scanners import technical_snapshot

    from money.qualification import providers

    ctx.environ = {**runner.EXPECTED_ENVIRONMENT, "MONEY_QLIB_ENABLED": "false"}
    snapshot = technical_snapshot()
    snapshot = ResearchSnapshot.model_validate(
        {**snapshot.model_dump(), "qlib_enabled": False, "hash": ""}
    )
    ctx.write_json("outputs/snapshot.json", snapshot.model_dump(mode="json"))
    monkeypatch.setattr(providers, "run_provider_stages", lambda *_: {"complete": True})
    monkeypatch.setattr(native, "run_inference_stage", lambda *_: {"complete": True})
    monkeypatch.setattr(native, "run_native_preflight_stage", lambda *_: {"complete": True})
    monkeypatch.setattr(runner, "run_snapshot_stage", lambda *_: {"complete": True})
    monkeypatch.setattr(quant, "prepare_lean_audit_inputs", lambda *_: None)
    calls = []

    def first_pass(*_):
        calls.append("first-pass")
        return {"complete": True}

    def lean(*_):
        calls.append("lean")
        return {"complete": False}

    monkeypatch.setattr(native, "run_first_pass_stage", first_pass)
    monkeypatch.setattr(quant, "run_lean_stage", lean)
    monkeypatch.setattr(
        native, "run_cio_stage", lambda *_: pytest.fail("LEAN failure must block CIO")
    )
    monkeypatch.setattr(runner, "assemble_manifest", lambda *_: None)
    result = runner.run(ctx)
    assert calls == ["first-pass", "lean"]
    assert result["status"] == "QUALIFICATION BLOCKED"
    assert ctx.read_json("outputs/qlib-result.json")["status"] == "DISABLED"
    assert "CIO_LOCKED_REPORTS_AND_LEAN_REQUIRED" in {item["code"] for item in ctx.blockers}


def test_disabled_production_environment_cannot_reintroduce_old_model_paths(ctx):
    ctx.environ = {"MONEY_QLIB_ENABLED": "false", "MONEY_QLIB_QUALIFIED_MODEL": "historical-model"}
    environment = runner.production_environment(ctx, ctx.root / "manifest.json", "test-only", [])
    assert environment["MONEY_QLIB_ENABLED"] == "false"
    assert "MONEY_QLIB_QUALIFIED_MODEL" not in environment
    with pytest.raises(ValueError, match="QLIB_DISABLED_CANNOT_REFERENCE_MODEL"):
        runner.production_environment(
            ctx,
            ctx.root / "manifest.json",
            "test-only",
            [{"production_environment": {"MONEY_QLIB_QUALIFIED_MODEL": "old-model"}}],
        )


@pytest.mark.parametrize("old_mode", [None, True])
def test_disabled_mode_rejects_legacy_or_enabled_native_preflight(ctx, old_mode):
    from test_live_data_scanners import technical_snapshot

    snapshot = technical_snapshot()
    snapshot = ResearchSnapshot.model_validate(
        {**snapshot.model_dump(), "qlib_enabled": False, "hash": ""}
    )
    ctx.write_json("outputs/snapshot.json", snapshot.model_dump(mode="json"))
    config = {
        firm: selection.model_dump(mode="json")
        for firm, selection in native._selections(ctx).items()
    }
    ctx.write_json("outputs/inference.json", {"complete": True, "manifest_fields": config})
    preflight = {"complete": True, "verified_at": ctx.now.isoformat()}
    if old_mode is not None:
        preflight["qlib_enabled"] = old_mode
    ctx.write_json("outputs/native-preflight.json", preflight)
    with pytest.raises(ValueError, match="QLIB_MODE_EXECUTION_INPUT_MISMATCH"):
        native._execution_inputs(ctx)


def disabled_production_case(ctx, monkeypatch):
    snapshot, _ = two_firm_inputs(ctx, monkeypatch)
    result = native.run_first_pass_stage(ctx)
    ctx.write_json("manifest.json", {"synthetic_unit_case": True})
    monkeypatch.setenv("MONEY_QLIB_ENABLED", "false")
    monkeypatch.setenv("MONEY_LIVE_MANIFEST", str(ctx.root / "manifest.json"))
    monkeypatch.setenv(
        "MONEY_NATIVE_QUALIFICATION_REPORTS", str(ctx.root / "outputs/first-pass.json")
    )
    monkeypatch.delenv("MONEY_QLIB_QUALIFIED_MODEL", raising=False)
    monkeypatch.delenv("MONEY_QLIB_ARTIFACT_HASH", raising=False)
    module = runpy.run_path(str(ctx.repo / "tests/production/test_native_live.py"))
    operation = module["test_qlib_selection_and_native_live"]
    monkeypatch.setitem(
        operation.__globals__,
        "_runtime",
        lambda *_: pytest.fail("Disabled selection must not claim Qlib runtime execution"),
    )
    manifest = SimpleNamespace(
        qlib_enabled=False,
        qlib_registry_id=None,
        qlib_artifact_hash=None,
        qualification_artifacts=result["artifacts"],
    )
    return operation, snapshot, manifest


def test_production_disabled_selection_checks_genuine_seal_structure_not_runtime(ctx, monkeypatch):
    # Synthetic local seam only; this never invokes/claims production acceptance.
    operation, snapshot, manifest = disabled_production_case(ctx, monkeypatch)
    operation(snapshot, manifest)
    manifest.qualification_artifacts = []
    with pytest.raises(AssertionError, match="first-pass seal"):
        operation(snapshot, manifest)


def test_production_disabled_selection_rejects_tampered_seal_bytes(ctx, monkeypatch):
    operation, snapshot, manifest = disabled_production_case(ctx, monkeypatch)
    _, relative = manifest.qualification_artifacts[0]
    ctx.write_json(relative, {"tampered_test_bytes": True})
    with pytest.raises(AssertionError):
        operation(snapshot, manifest)


def test_production_disabled_selection_rejects_old_three_report_file(ctx, monkeypatch):
    operation, snapshot, manifest = disabled_production_case(ctx, monkeypatch)
    reports = ctx.read_json("outputs/first-pass.json")
    reports.append({**reports[0], "firm": "qlib", "upstream_sha": UPSTREAM_SHAS["qlib"]})
    ctx.write_json("outputs/first-pass.json", reports)
    with pytest.raises(AssertionError):
        operation(snapshot, manifest)
