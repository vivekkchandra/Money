"""Synthetic diagnostic seams are not native qualification evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from money.qualification import native_diagnosis
from money.qualification.core import QualificationContext


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QualificationContext:
    monkeypatch.setattr(
        native_diagnosis,
        "_sources",
        lambda ctx, enabled: {
            "qlib": {"required": enabled},
            "tradingagents": {"installed_matches": False},
        },
    )
    return QualificationContext(
        tmp_path / "bundle",
        Path(__file__).resolve().parents[2],
        {"MONEY_QLIB_ENABLED": "false", "OPENAI_API_KEY": "unit-native-diagnosis-secret"},
        datetime(2026, 9, 18, 8, 0, tzinfo=UTC),
    )


def saved_security(ctx: QualificationContext) -> tuple[str, str]:
    reference = ctx.artifact(
        {
            "kind": "money-native-security-v1",
            "passed": False,
            "audit": {
                "dependencies": [
                    {
                        "name": "chromadb",
                        "version": "1.1.1",
                        "vulns": [
                            {
                                "id": "PYSEC-2026-311",
                                "aliases": ["CVE-2026-45829", "GHSA-f4j7-r4q5-qw2c"],
                                "fix_versions": [],
                            },
                            {
                                "id": "PYSEC-2026-311",
                                "aliases": ["CVE-2026-45829"],
                                "fix_versions": [],
                            },
                        ],
                    }
                ]
            },
        }
    )
    ctx.write_json(
        "outputs/native-preflight.json",
        {
            "complete": False,
            "qlib_enabled": False,
            "verified_at": (ctx.now - timedelta(days=1)).isoformat(),
            "security": {"passed": False, "artifacts": [reference]},
        },
    )
    return reference


def test_diagnosis_preserves_age_qllib_mode_and_mandatory_lean(ctx):
    saved_security(ctx)
    ctx.write_json("reviews/lean-inputs.json", {"approved_by": None, "costs": None})
    original = ctx.read_bytes("reviews/lean-inputs.json")
    before = set(ctx.root.rglob("*"))
    result = native_diagnosis.write_native_diagnosis(ctx)
    assert result["qlib_enabled"] is False and result["lean_required"] is True
    assert result["scope"] == "LOCAL_DIAGNOSIS_ONLY"
    assert result["production_qualified"] is False
    assert result["native_runtime_qualified"] is False
    assert result["fresh_security_audit_performed"] is False
    assert result["security"]["evidence"]["within_one_hour_window"] is False
    assert result["security"]["artifact_integrity"] == "VERIFIED"
    assert result["sources"]["qlib"]["required"] is False
    assert ctx.read_bytes("reviews/lean-inputs.json") == original
    new_files = {
        p.relative_to(ctx.root).as_posix() for p in set(ctx.root.rglob("*")) - before if p.is_file()
    }
    assert new_files == {"outputs/native-blocker-diagnosis.json"}


def test_recorded_advisories_remain_blockers_and_duplicates_do_not_inflate_unique_ids(ctx):
    saved_security(ctx)
    findings = native_diagnosis.write_native_diagnosis(ctx)["security"]["recorded_findings"]
    assert findings[0]["recorded_entries"] == 2
    assert findings[0]["identifiers"].count("CVE-2026-45829") == 1
    assert findings[0]["primary_advisories"] == [
        "https://github.com/advisories/GHSA-f4j7-r4q5-qw2c"
    ]
    assert findings[0]["fix_versions_in_recorded_audit"] == []


def test_corrupt_receipt_is_not_reported_as_valid(ctx):
    _, relative = saved_security(ctx)
    ctx.write_json(relative, {"kind": "modified"})
    result = native_diagnosis.write_native_diagnosis(ctx)
    assert result["security"]["artifact_integrity"] == "INVALID_OR_UNSAFE"
    assert result["security"]["recorded_findings"] == []
    assert result["native_runtime_qualified"] is False


def test_exact_real_metadata_conflicts_are_proven_not_assumed(ctx):
    result = native_diagnosis.write_native_diagnosis(ctx)
    conflicts = result["dependency_resolution"]["proven_metadata_conflicts"]
    assert {item["package"] for item in conflicts} == {
        "python-dotenv",
        "numpy",
        "pandas",
        "langchain-anthropic",
        "langchain-google-genai",
    }
    assert all(item["intersection"] == "EMPTY" for item in conflicts)
    assert result["security"]["chroma_pinned_requirement"] == "~=1.1.0"


def test_missing_metadata_does_not_invent_conflicts(ctx, tmp_path):
    ctx.repo = tmp_path / "missing-repository"
    result = native_diagnosis.write_native_diagnosis(ctx)
    assert result["dependency_resolution"]["proven_metadata_conflicts"] == []
    assert result["dependency_resolution"]["recorded_resolved"] is False


def test_local_inference_pass_does_not_qualify_native_or_hosted(ctx):
    ctx.write_json(
        "outputs/inference.json",
        {
            "access_verified": True,
            "hosted_compatible": False,
            "scope": "LOCAL_INFERENCE_ONLY",
        },
    )
    result = native_diagnosis.write_native_diagnosis(ctx)
    assert result["inference"]["recorded_access_verified"] is True
    assert result["inference"]["recorded_hosted_compatible"] is False
    assert result["native_runtime_qualified"] is False
    assert result["target_worker"]["egress_qualified"] is False


def test_secret_in_untrusted_receipt_never_copied_to_diagnosis(ctx):
    target = ctx._path("outputs/native-preflight.json")
    target.write_text(json.dumps({"secret": ctx.environ["OPENAI_API_KEY"]}))
    result = native_diagnosis.write_native_diagnosis(ctx)
    assert result["security"]["evidence"]["status"] == "INVALID_OR_UNSAFE"
    assert ctx.environ["OPENAI_API_KEY"].encode() not in ctx.read_bytes(
        "outputs/native-blocker-diagnosis.json"
    )


def test_symlink_input_is_rejected_without_reading_target(ctx, tmp_path):
    target = tmp_path / "not-a-receipt.json"
    target.write_text('{"complete":true}')
    ctx._path("outputs/native-preflight.json").symlink_to(target)
    result = native_diagnosis.write_native_diagnosis(ctx)
    assert result["security"]["evidence"]["status"] == "INVALID_OR_UNSAFE"
    assert result["production_qualified"] is False


def test_diagnosis_cannot_ignore_invalid_qlib_selection(ctx):
    ctx.environ = {"MONEY_QLIB_ENABLED": "not-a-mode"}
    with pytest.raises(ValueError, match="MONEY_QLIB_ENABLED_REQUIRES_TRUE_OR_FALSE"):
        native_diagnosis.write_native_diagnosis(ctx)
