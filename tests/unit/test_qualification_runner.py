"""Synthetic mechanics only: never qualification evidence for a deployment."""

import base64
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from money.qualification import runner
from money.qualification.core import CommandResult, QualificationContext, run_captured

NOW = datetime(2026, 9, 17, tzinfo=UTC)


@pytest.fixture
def context(tmp_path):
    return QualificationContext(tmp_path / "bundle", Path(__file__).resolve().parents[2], {}, NOW)


def test_artifact_hash_is_of_exact_bytes_and_checkpoint_detects_tampering(context):
    raw = b"synthetic unit test evidence, not production proof"
    reference = context.artifact(raw)
    assert reference[0] == hashlib.sha256(raw).hexdigest()
    assert context.verify_artifact(*reference) == raw
    context.checkpoint("sample", "input-version", {"result": "observed"}, [reference])
    assert context.cache("sample", "input-version", 60) == {"result": "observed"}
    assert context.cache("sample", "changed-input", 60) is None
    (context.root / reference[1]).write_bytes(b"changed fixture bytes")
    assert context.cache("sample", "input-version", 60) is None


def test_checkpoint_never_extends_freshness_or_accepts_future_receipt(context):
    context.checkpoint("sample", "input", {"done": True})
    context.now = NOW + timedelta(seconds=60)
    assert context.cache("sample", "input", 60) is None
    context.now = NOW - timedelta(seconds=1)
    assert context.cache("sample", "input", 60) is None


def test_resume_preserves_operator_edits(context):
    context.template("reviews/example.json", {"reviewer": None})
    context.write_json("reviews/example.json", {"reviewer": "unit reviewer"})
    context.template("reviews/example.json", {"reviewer": None})
    assert context.read_json("reviews/example.json") == {"reviewer": "unit reviewer"}
    assert (context.root / "reviews/example.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("encoding", ["raw", "base64", "basic", "company"])
def test_credentials_and_basic_auth_are_never_persisted(context, encoding):
    context.environ = {
        "TRADING212_API_KEY": "test-key-not-a-real-key",
        "TRADING212_API_SECRET": "test-secret-not-real",
        "COMPANIES_HOUSE_API_KEY": "company-fixture",
    }
    value = context.environ["TRADING212_API_KEY"].encode()
    if encoding == "base64":
        value = base64.b64encode(value)
    if encoding == "basic":
        value = base64.b64encode(value + b":" + context.environ["TRADING212_API_SECRET"].encode())
    if encoding == "company":
        value = base64.b64encode(b"company-fixture:")
    with pytest.raises(ValueError, match="QUALIFICATION_SECRET_DETECTED"):
        context.artifact(value)
    assert not list(context.root.rglob("*.bin"))


@pytest.mark.parametrize("path", ["../escaped", "/absolute", "x//y", "./name", "x/../y", "x\\y"])
def test_contained_paths_only(context, path):
    with pytest.raises(ValueError, match="PATH_UNSAFE"):
        context.write_json(path, {})


def test_parent_and_leaf_symlinks_are_rejected(context, tmp_path):
    (context.root / "inputs").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="PATH_UNSAFE"):
        context.read_bytes("inputs/file.json")
    (context.root / "output").symlink_to(tmp_path / "does-not-exist")
    with pytest.raises(ValueError, match="PATH_UNSAFE"):
        context.write_json("output", {})


def test_single_runner_lock(context):
    with context.locked(), pytest.raises(ValueError, match="ALREADY_RUNNING"), context.locked():
        pass


def test_stage_exception_and_secret_stdout_never_escape(context, capsys):
    secret = "this-is-a-unit-test-secret-value"
    context.environ = {"OPENAI_API_KEY": secret}

    def raises():
        print(secret)
        raise ValueError(secret)

    assert runner._execute(context, "sample", raises) == {}
    assert secret not in capsys.readouterr().out
    assert context.blockers[0]["code"] == "SAMPLE_FAILED"
    assert not (context.root / "outputs/sample-diagnostics.txt").exists()


def test_clean_exception_stdout_cannot_be_used_as_proof(context):
    def fails():
        print("PASS")
        raise ValueError("not actually passed")

    runner._execute(context, "sample", fails)
    assert not (context.root / "outputs/sample-result.json").exists()


def test_incomplete_bundle_does_not_write_manifest_or_overwrite_review(context):
    assert runner.assemble_manifest(context, []) is None
    review = context.read_json("reviews/release.json")
    review["reviewed_by"] = "unit operator"
    context.write_json("reviews/release.json", review)
    assert runner.assemble_manifest(context, []) is None
    assert context.read_json("reviews/release.json")["reviewed_by"] == "unit operator"
    assert not (context.root / "manifest.json").exists()


def test_manifest_inventory_rejects_mismatched_real_bytes(context):
    reference = context.artifact(b"test fixture")
    (context.root / reference[1]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        runner._artifacts(context)


@pytest.mark.parametrize(
    "counts,exitcode,expected",
    [
        ({"collected": 11, "passed": 0, "skipped": 11}, 0, False),
        ({"collected": 11, "passed": 10, "skipped": 1}, 0, False),
        ({"collected": 11, "passed": 10, "xfailed": 1}, 0, False),
        ({"collected": 11, "passed": 11, "deselected": 1}, 0, False),
        ({"collected": 0, "passed": 0}, 0, False),
        ({"collected": 11, "passed": 11}, 1, False),
        ({"collected": 11, "passed": 11}, 0, True),
    ],
)
def test_production_tests_require_actual_execution_not_skips(
    context, monkeypatch, counts, exitcode, expected
):
    commands = []

    def invoke(argv, **kwargs):
        commands.append((argv, kwargs["environ"]))
        if "pytest" not in argv:
            return CommandResult(0, b"offline schema valid")
        return CommandResult(
            exitcode, b"MONEY_QUALIFICATION_TEST_COUNTS=" + json.dumps(counts).encode()
        )

    monkeypatch.setattr(runner, "run_captured", invoke)
    actual = runner.validate_and_test(
        context,
        context.root / "manifest.json",
        hashlib.sha256(b"unit test manifest").hexdigest(),
        [],
    )
    assert actual is expected
    assert commands[0][0][:4] == ["uv", "run", "python", "scripts/validate_live.py"]
    assert commands[1][0][:5] == ["uv", "run", "pytest", "tests/production", "-q"]
    assert commands[1][1]["MONEY_RUN_PRODUCTION_INTEGRATION"] == "1"


def test_legacy_credential_aliases_are_only_in_memory(context):
    context.environ = {
        "TRADING212_API_KEY": "fixture-key",
        "TRADING212_API_SECRET": "fixture-secret",
        "OPENAI_API_KEY": "fixture-inference",
    }
    environment = runner.production_environment(
        context, context.root / "manifest.json", "test-hash", []
    )
    assert environment["TRADING212_METADATA_API_KEY"] == "fixture-key"
    assert environment["MONEY_NATIVE_INFERENCE_API_KEY"] == "fixture-inference"
    assert not list(context.root.iterdir())


def test_captured_commands_are_bounded_and_do_not_write_raw_logs(tmp_path):
    result = run_captured(
        [sys.executable, "-c", "print('unit fixture')"], cwd=tmp_path, environ=os.environ, timeout=5
    )
    assert result.returncode == 0 and result.output == b"unit fixture\n"
    with pytest.raises(ValueError, match="OUTPUT_LIMIT"):
        run_captured(
            [sys.executable, "-c", "print('x' * 5000)"],
            cwd=tmp_path,
            environ=os.environ,
            timeout=5,
            maximum_output=100,
        )
    assert not list(tmp_path.iterdir())


def test_diagnostic_capture_is_bounded_without_spooling(context):
    with runner.BoundedDiagnostics() as stream:
        with pytest.raises(ValueError, match="DIAGNOSTICS_LIMIT"):
            stream.write("x" * 2_000_001)
        assert not stream.getvalue()


def test_argument_errors_do_not_echo_accidentally_pasted_secrets(capsys):
    with pytest.raises(SystemExit):
        runner.main(["--accidental-secret", "unit-secret-do-not-echo"])
    capture = capsys.readouterr()
    assert "unit-secret-do-not-echo" not in capture.err + capture.out


def test_stale_files_never_authorize_downstream_after_current_failure(context, monkeypatch):
    from money.qualification import native, providers, quant

    context.environ = runner.EXPECTED_ENVIRONMENT
    for filename in ("snapshot", "qlib-state", "first-pass", "lean-report"):
        context.write_json(f"outputs/{filename}.json", {"old": "unit-test-stale-output"})
    monkeypatch.setattr(providers, "run_provider_stages", lambda ctx: {"complete": False})
    monkeypatch.setattr(native, "run_inference_stage", lambda ctx: {"complete": True})
    monkeypatch.setattr(native, "run_native_preflight_stage", lambda ctx: {"complete": False})
    monkeypatch.setattr(quant, "run_qlib_stage", lambda ctx: {"complete": False})
    monkeypatch.setattr(runner, "run_snapshot_stage", lambda *_: {})
    monkeypatch.setattr(
        native, "run_first_pass_stage", lambda *_: pytest.fail("stale first-pass execution")
    )
    monkeypatch.setattr(quant, "run_lean_stage", lambda *_: pytest.fail("stale LEAN execution"))
    monkeypatch.setattr(native, "run_cio_stage", lambda *_: pytest.fail("stale paid CIO execution"))
    report = runner.run(context)
    assert report["status"] == "QUALIFICATION BLOCKED"
    assert not (context.root / "manifest.json").exists()
    assert context.read_json("outputs/snapshot.json")["old"] == "unit-test-stale-output"


def test_refreeze_does_not_backdate_or_refresh_any_economic_evidence(context):
    from test_live_data_scanners import technical_snapshot

    from money.backtest.lean import historical_dataset_hash
    from money.qualification.snapshot import _refreeze
    from money.schemas.contracts import PriceBar

    original = technical_snapshot()
    context.now = original.created_at + timedelta(minutes=1)
    renewed = _refreeze(context, original)
    assert renewed.hash != original.hash
    assert renewed.created_at == context.now
    assert renewed.snapshot_id != original.snapshot_id
    for before, after in zip(original.evidence, renewed.evidence, strict=True):
        assert after.publication_time == before.publication_time
        assert after.retrieval_time == before.retrieval_time
        assert after.fresh_until == before.fresh_until
        assert after.payload == before.payload
    assert historical_dataset_hash(
        [item for item in original.evidence if isinstance(item.payload, PriceBar)]
    ) == historical_dataset_hash(
        [item for item in renewed.evidence if isinstance(item.payload, PriceBar)]
    )


def test_snapshot_resume_uses_artifact_not_mutable_checkpoint_payload(context):
    from test_live_data_scanners import technical_snapshot

    from money.qualification.snapshot import _cached_snapshot

    original = technical_snapshot()
    reference = context.artifact(original.model_dump(mode="json"))
    changed = original.model_dump(mode="json")
    changed["snapshot_id"] = "substituted-checkpoint-data"
    context.checkpoint(
        "snapshot", "inputs", {"snapshot_artifact": reference, "snapshot": changed}, [reference]
    )
    restored = _cached_snapshot(context, context.cache("snapshot", "inputs", 3600))
    assert restored == original
    assert _cached_snapshot(context, {"snapshot": changed}) is None
    (context.root / reference[1]).write_bytes(b"changed snapshot bytes")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        _cached_snapshot(context, {"snapshot_artifact": reference})


def test_assembled_manifest_uses_exact_bytes_and_preserves_last_good_on_failure(context):
    from test_filing_documents import reviewed_manifest

    fields = reviewed_manifest().model_dump(mode="json")
    digest, _ = context.artifact(b"synthetic fixture proof bytes, never production qualification")
    proof_keys = {
        "eligibility_proof_hash",
        "ethical_proof_hash",
        "corporate_action_coverage_hash",
        "evidence_hash",
        "review_evidence_hash",
        "qualification_report_hash",
        "native_egress_verification_hash",
        "historical_eligibility_hash",
        "survivorship_audit_hash",
        "corporate_action_audit_hash",
    }

    def replace_proofs(value):
        if isinstance(value, dict):
            return {
                key: digest if key in proof_keys else replace_proofs(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [replace_proofs(item) for item in value]
        return value

    fields = replace_proofs(fields)
    fields.pop("qualification_artifacts")
    fields.pop("reviewed_by")
    providers = {
        key: fields.pop(key)
        for key in ("instruments", "provider_qualifications", "filing_document_storage_hosts")
    }
    outputs = [providers, {"manifest_fields": fields}]
    assert runner.assemble_manifest(context, outputs) is None
    context.write_bytes("inputs/release-approval.txt", b"synthetic unit-test independent approval")
    context.write_json(
        "reviews/release.json",
        {
            "prepared_by": "fixture preparer",
            "reviewed_by": "fixture reviewer",
            "reviewed_at": NOW.isoformat(),
            "valid_until": (NOW + timedelta(hours=1)).isoformat(),
            "approved_inputs_hash": context.read_json("outputs/release-inputs.json")["inputs_hash"],
            "approval_evidence_path": "inputs/release-approval.txt",
        },
    )
    context.blockers.clear()  # Simulate a new invocation, not a production override.
    path, expected = runner.assemble_manifest(context, outputs)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    manifest = runner.load_manifest(path, expected)
    assert not manifest.instruments and len(manifest.reviewed_instruments) == 1
    retained = path.read_bytes()
    context.block("UNIT_TEST_FAILED_REQUALIFICATION", "Synthetic failed rerun")
    assert runner.assemble_manifest(context, outputs) is None
    assert path.read_bytes() == retained
