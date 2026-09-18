"""Isolated installers never disguise missing code, closure or audit evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from money.qualification.core import CommandResult, QualificationContext, json_bytes
from money.research import native_environments as native


@pytest.fixture
def ctx(tmp_path: Path) -> QualificationContext:
    return QualificationContext(tmp_path / "qualification", Path(__file__).resolve().parents[2],
        {"PATH": "/usr/bin", "OPENAI_API_KEY": "never-inherit-this-private-key",
         "TRADING212_API_SECRET": "never-inherit-this-broker-secret",
         "UV_INDEX_URL": "https://secret:password@not-approved.invalid",
         "PYTHONPATH": "/unsafe", "PIP_EXTRA_INDEX_URL": "https://unsafe.invalid"},
        datetime(2026, 9, 18, tzinfo=UTC))


def test_subprocess_environment_excludes_all_application_credentials(ctx: QualificationContext) -> None:
    result = native._environment(ctx)
    assert not {"OPENAI_API_KEY", "TRADING212_API_SECRET", "UV_INDEX_URL", "PYTHONPATH",
                "PIP_EXTRA_INDEX_URL"}.intersection(result)
    assert result["PIP_CONFIG_FILE"] == "/dev/null"
    assert result["GIT_TERMINAL_PROMPT"] == "0"
    ctx.check_secrets(json_bytes(result))


def test_definitions_preserve_exact_repositories_and_python(ctx: QualificationContext) -> None:
    definitions = native._definitions(ctx)
    assert definitions["python_version"] == "3.12.14"
    assert definitions["adapter_dependencies"] == ["pydantic>=2.12.5"]
    assert definitions["engines"]["tradingagents"]["revision"] == "be952b8eccb49720509af544c6675233bc1f10d0"
    assert definitions["engines"]["ai_hedge_fund"]["revision"] == "fc1bf250ead209ae5f02c39c3d0062c4bb554505"
    assert {spec["environment_directory"] for spec in definitions["engines"].values()} == {
        ".venv-tradingagents", ".venv-ai-hedge-fund"}


@pytest.mark.parametrize("field,value", [
    ("imports", []), ("imports", ["json"]), ("version", "different"),
    ("distribution", "some-fork"), ("source_directory", "../unreviewed"),
])
def test_definition_cannot_weaken_adapter_imports_or_upstream_metadata(
    ctx: QualificationContext, tmp_path: Path, field: str, value: Any,
) -> None:
    definitions = native._definitions(ctx)
    definitions["engines"]["tradingagents"][field] = value
    repo = tmp_path / "other-repo"
    config = repo / native._DEFINITION
    config.parent.mkdir(parents=True)
    config.write_bytes(json_bytes(definitions))
    (repo / "UPSTREAM_LOCK.txt").write_bytes((ctx.repo / "UPSTREAM_LOCK.txt").read_bytes())
    changed = QualificationContext(tmp_path / "other-root", repo, ctx.environ, ctx.now)
    with pytest.raises(ValueError, match="NATIVE_RUNTIME_ENTRYPOINT_DEFINITION_INVALID"):
        native._definitions(changed)


def test_resolution_uses_all_dependencies_and_hash_locked_sync(
    ctx: QualificationContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    requirements, lock = tmp_path / "runtime.in", tmp_path / "runtime.lock"
    requirements.write_text("pydantic>=2.12.5\n")
    calls: list[list[str]] = []

    def run(_ctx: Any, args: list[str], _code: str, **kwargs: Any) -> bytes:
        calls.append(args)
        if "compile" in args:
            lock.write_text("pydantic==2.12.5 --hash=sha256:" + "b" * 64 + "\n")
        return b""

    monkeypatch.setattr(native, "_require", run)
    first = native._lock(ctx, requirements, lock, "/isolated/python")
    assert native._lock(ctx, requirements, lock, "/isolated/python") == first
    native._sync(ctx, lock, "/isolated/python")
    assert len(calls) == 2  # cached lock reused, not re-resolved
    assert "--generate-hashes" in calls[0]
    assert "--require-hashes" in calls[1]
    assert all(not {"--no-deps", "--overrides", "--excludes"}.intersection(args) for args in calls)


def test_changed_lock_or_inputs_never_silently_resume(
    ctx: QualificationContext, tmp_path: Path,
) -> None:
    requirements, lock = tmp_path / "runtime.in", tmp_path / "runtime.lock"
    requirements.write_text("pydantic>=2.12.5\n")
    lock.write_text("pydantic==2.12.5 --hash=sha256:" + "b" * 64 + "\n")
    with pytest.raises(ValueError, match="NATIVE_LOCK_RECEIPT_REQUIRED"):
        native._lock(ctx, requirements, lock, "/isolated/python")
    reference = ctx.artifact(lock.read_bytes())
    receipt = {"inputs_sha256": native.hashlib.sha256(requirements.read_bytes()).hexdigest(),
               "artifact": reference}
    lock.with_suffix(".receipt.json").write_bytes(json_bytes(receipt))
    lock.write_text("pydantic==0.1 --hash=sha256:" + "c" * 64 + "\n")
    with pytest.raises(ValueError, match="NATIVE_LOCK_INTEGRITY_FAILED"):
        native._lock(ctx, requirements, lock, "/isolated/python")


@pytest.mark.parametrize("skipped,vulnerable,dependency_exit,expected", [
    (False, False, 0, True), (True, False, 0, False), (False, True, 0, False),
    (False, False, 1, False),
])
def test_audit_targets_each_actual_environment_and_never_ignores_findings(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch,
    skipped: bool, vulnerable: bool, dependency_exit: int, expected: bool,
) -> None:
    report = {"dependencies": [{"name": "package-one", "version": "1.0",
        **({"skip_reason": "not found"} if skipped else {}),
        "vulns": [{"id": "GHSA-test", "fix_versions": ["1.1"]}] if vulnerable else []}]}
    calls: list[list[str]] = []

    def command(_ctx: Any, args: list[str], **kwargs: Any) -> CommandResult:
        calls.append(args)
        return (CommandResult(dependency_exit, b"") if "check" in args
                else CommandResult(1 if vulnerable else 0, json_bytes(report)))

    monkeypatch.setattr(native, "_command", command)
    audit = native._audit(ctx, "tradingagents", "/isolated/python", [("package-one", "1.0")])
    assert audit["passed"] is expected
    assert calls[0] == ["uv", "pip", "check", "--python", "/isolated/python"]
    assert "--ignore-vuln" not in calls[1]
    raw = json.loads(ctx.verify_artifact(*audit["artifacts"][1]))
    assert raw["audit"] == report
    assert raw["security_warnings_ignored"] is False


def test_install_failure_is_secret_free_and_does_not_skip_other_engine(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(native, "_python", lambda *args: "/python")
    monkeypatch.setattr(native, "_venv", lambda _ctx, _python, path: str(path / "bin/python"))

    def fail(_ctx: Any, role: str, *args: Any) -> Path:
        calls.append(role)
        raise RuntimeError("never-inherit-this-private-key")

    monkeypatch.setattr(native, "_source", fail)
    result = native.prepare_native_environments(ctx)
    assert calls == ["tradingagents", "ai_hedge_fund"]
    assert result["complete"] is False
    assert all(r["errors"] == ["NATIVE_ENVIRONMENT_UNAVAILABLE"] for r in result["runtimes"].values())
    assert not ctx.blockers  # caller owns contextual action messages
    assert result["production_qualified"] is False
    assert result["qlib_enabled"] is False
    ctx.check_secrets(json_bytes(result))


def test_environment_symlink_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "unsafe").symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="PATH_UNSAFE"):
        native._safe_directory(tmp_path / "unsafe" / "child")


def test_source_overlay_rejects_nested_symlink_before_any_copy(
    ctx: QualificationContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, source = tmp_path / "environment", tmp_path / "source"
    site = environment / "lib/python3.12/site-packages"
    (site / "hedge_fund").mkdir(parents=True)
    (source / "hedge_fund").mkdir(parents=True)
    outside = tmp_path / "not-owned"
    outside.mkdir()
    original = outside / "unchanged.py"
    original.write_text("do not alter")
    (site / "hedge_fund" / "signals").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(native, "_require", lambda *args, **kwargs: str(site).encode())
    with pytest.raises(ValueError, match="PATH_UNSAFE"):
        native._restore_source_files(ctx, "/python", source, {"package": "hedge_fund"}, environment)
    assert original.read_text() == "do not alter"


@pytest.mark.parametrize("output,code", [
    (b"Failed to lookup address: DNS error", "NATIVE_PACKAGE_INDEX_UNREACHABLE"),
    (b"HTTP 403 Forbidden", "NATIVE_PACKAGE_INDEX_ACCESS_DENIED"),
    (b"No solution found when resolving dependencies", "NATIVE_DEPENDENCY_CONFLICT"),
    (b"never-inherit-this-private-key", "NATIVE_DEPENDENCY_RESOLUTION_FAILED"),
])
def test_install_failure_categories_never_expose_raw_output(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch, output: bytes, code: str,
) -> None:
    monkeypatch.setattr(native, "_command", lambda *args, **kwargs: CommandResult(1, output))
    with pytest.raises(ValueError, match="^" + code + "$"):
        native._require(ctx, ["uv", "pip", "compile"], "NATIVE_DEPENDENCY_RESOLUTION_FAILED")


def test_fixed_versions_required_for_remediation_and_no_advisories_are_hidden() -> None:
    assert not native._fix_available({"audit": {"dependencies": [{"name": "chromadb",
        "vulns": [{"id": "GHSA-no-patch", "fix_versions": []}]}]}})
    assert native._fix_available({"audit": {"dependencies": [{"name": "other",
        "vulns": [{"id": "GHSA-fixed", "fix_versions": ["2.0"]}]}]}})


def test_upstream_metadata_tampering_in_staging_is_rejected(
    ctx: QualificationContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io
    import tarfile

    spec = native._definitions(ctx)["engines"]["tradingagents"]
    work = tmp_path / "work"
    source = work / "source"
    source.mkdir(parents=True)
    (source / "pyproject.toml").write_text("altered dependency metadata")

    def command(_ctx: Any, args: list[str], _code: str, **kwargs: Any) -> bytes:
        if "get-url" in args:
            return spec["repository"].encode()
        if "rev-parse" in args:
            return spec["revision"].encode()
        if "archive" in args:
            with tarfile.open(args[args.index("--output") + 1], "w") as archive:
                info = tarfile.TarInfo("pyproject.toml")
                info.size = len(b"exact original metadata")
                archive.addfile(info, io.BytesIO(b"exact original metadata"))
        return b""

    monkeypatch.setattr(native, "_require", command)
    with pytest.raises(ValueError, match="NATIVE_STAGED_SOURCE_CHANGED"):
        native._source(ctx, "tradingagents", spec, work)


def test_resume_checks_real_environment_instead_of_approving_checkpoint(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(native, "_python", lambda *args: "/python")
    monkeypatch.setattr(native, "_venv", lambda _ctx, _python, path: str(path / "bin/python"))
    for role in native._ROLES:
        ctx.write_json(f"outputs/native-environments/{role}.json", {
            "complete": True, "importability": "VERIFIED", "security": {"passed": True}})

    def inspect(_ctx: Any, _python: str, spec: dict[str, Any]) -> dict[str, Any]:
        calls.append(spec["package"])
        raise ValueError("NATIVE_INSTALLED_SOURCE_MISMATCH")

    monkeypatch.setattr(native, "_inspect", inspect)
    result = native.prepare_native_environments(ctx)
    assert calls == ["tradingagents", "hedge_fund"]
    assert result["complete"] is False
    assert all(r["errors"] == ["NATIVE_INSTALLED_SOURCE_MISMATCH"] for r in result["runtimes"].values())


def test_changed_installed_dependencies_cannot_be_reapproved_by_name_version_audit(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native, "_python", lambda *args: "/python")
    monkeypatch.setattr(native, "_venv", lambda _ctx, _python, path: str(path / "bin/python"))
    monkeypatch.setattr(native, "installed_files_fingerprint", lambda _path: "changed-file-hash")
    for role in native._ROLES:
        ctx.write_json(f"outputs/native-environments/{role}.json", {
            "importability": "VERIFIED", "environment_sha256": "previous-file-hash"})
    result = native.prepare_native_environments(ctx)
    assert result["complete"] is False
    assert all(r["errors"] == ["NATIVE_INSTALLED_ENVIRONMENT_CHANGED"] for r in result["runtimes"].values())


def test_first_unattested_install_forces_hash_checked_reinstall(
    ctx: QualificationContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(native, "_require", lambda _ctx, args, code, **kwargs: calls.append(args))
    native._sync(ctx, tmp_path / "runtime.lock", "/python", reinstall=True)
    assert "--reinstall" in calls[0]
    assert "--require-hashes" in calls[0]
    assert "--no-deps" not in calls[0]


def test_failed_latest_status_cannot_erase_installed_byte_baseline(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = native._definitions(ctx)
    monkeypatch.setattr(native, "_python", lambda *args: "/python")
    monkeypatch.setattr(native, "_venv", lambda _ctx, _python, path: str(path / "bin/python"))
    monkeypatch.setattr(native, "installed_files_fingerprint", lambda _path: "changed")
    for role, spec in definitions["engines"].items():
        identity = native.fingerprint({"spec": spec, "python": definitions["python_version"],
            "adapter_dependencies": definitions["adapter_dependencies"],
            "platform": native.platform.system(), "machine": native.platform.machine()})
        work = ctx.root / "state/native" / role / identity
        work.mkdir(parents=True)
        reference = ctx.artifact({"environment_sha256": "original"})
        (work / "environment.receipt.json").write_bytes(json_bytes({"artifact": reference}))
    for _attempt in range(2):
        result = native.prepare_native_environments(ctx)
        assert result["complete"] is False
        assert all(r["errors"] == ["NATIVE_INSTALLED_ENVIRONMENT_CHANGED"]
                   for r in result["runtimes"].values())
