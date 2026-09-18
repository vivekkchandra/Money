"""Transport fixtures are synthetic and never emitted as production evidence."""

from __future__ import annotations

import json
import platform
import runpy
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from money.adapters import native_subprocess as boundary
from money.adapters.native import NativeDeadline, NativeRunSettings
from money.adapters.native_process import NativeProcessPolicy
from money.adapters.upstream import UPSTREAM_SHAS, InvalidUpstreamReport, UpstreamUnavailable
from money.research.inference_config import InferenceSelection
from money.schemas.contracts import (
    Claim,
    InstrumentMetadata,
    ResearchMandate,
    ResearchSnapshot,
    TradingAgentsResearchReport,
    content_hash,
)

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)


@pytest.fixture
def snapshot() -> ResearchSnapshot:
    return ResearchSnapshot(
        purpose="RESEARCH_TESTING", usage_mode="PERSONAL_RESEARCH", qlib_enabled=False,
        universe_hash=content_hash({"test_only": True}), snapshot_id="transport-fixture",
        ticker="FIXTURE", created_at=NOW, price_cutoff=NOW, news_cutoff=NOW,
        filing_cutoff=NOW, fundamental_cutoff=NOW, evidence=(),
        instrument=InstrumentMetadata(
            ticker="FIXTURE", company="Unit fixture", instrument_type="STOCK",
            quote_currency="GBX", currently_available=True, verified_at=NOW,
            source="fixture", provider="trading212", source_id="FIXTUREl_EQ",
        ),
    )


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> boundary.IsolatedNativeRunner:
    interpreter = tmp_path / ".venv-tradingagents/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(sys.executable)
    monkeypatch.setattr(boundary, "bridge_fingerprint", lambda path: content_hash({"fixture": True}))
    return boundary.IsolatedNativeRunner(
        interpreter=interpreter, role="tradingagents", repository=tmp_path,
        selection=InferenceSelection(
            provider="ollama", model="qwen3:14b",
            endpoint="http://127.0.0.1:11434/v1/chat/completions",
            endpoint_scope="local", authentication="none",
        ), settings=NativeRunSettings(timeout_seconds=5),
        policy=NativeProcessPolicy(gateway_hosts=("127.0.0.1",), gateway_port=11434,
                                   timeout_seconds=5),
        expected_python_version=platform.python_version(),
        expected_inventory_sha256=content_hash({"inventory_fixture": True}),
        expected_bridge_sha256=content_hash({"fixture": True}),
        expected_environment_sha256=content_hash({"environment_fixture": True}),
    )


def test_only_fixed_role_interpreter_is_allowed(runner: boundary.IsolatedNativeRunner) -> None:
    with pytest.raises(ValueError, match="VERIFIED_INTERPRETER"):
        replace(runner, interpreter=Path(sys.executable))
    with pytest.raises(ValueError, match="VERIFIED_INTERPRETER"):
        replace(runner, role="ai_hedge_fund")


def test_unpinned_and_wrong_gateway_execution_is_denied(runner: boundary.IsolatedNativeRunner) -> None:
    with pytest.raises(ValueError, match="PINNED_ROLE"):
        replace(runner, settings=replace(runner.settings, verify_source_pin=False))
    with pytest.raises(ValueError, match="CAPABILITY_POLICY"):
        replace(runner, policy=replace(runner.policy, gateway_hosts=("example.com",)))
    with pytest.raises(ValueError, match="CAPABILITY_POLICY"):
        replace(runner, policy=replace(runner.policy, timeout_seconds=10))


def test_local_ollama_has_no_fake_credential(runner: boundary.IsolatedNativeRunner) -> None:
    assert runner.credential is None
    with pytest.raises(ValueError, match="UNUSED_CREDENTIAL"):
        replace(runner, credential="unit-fixture-not-a-real-key")


def test_changed_bridge_invalidates_audited_execution(
    runner: boundary.IsolatedNativeRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(boundary, "bridge_fingerprint", lambda path: content_hash({"edited": True}))
    with pytest.raises(ValueError, match="AUDITED_IDENTITY"):
        replace(runner)


def test_escaped_secret_is_also_detected() -> None:
    secret = 'fixture-key-with-"quote'
    payload = json.loads(json.dumps({"result": {"conclusion": secret}}, ensure_ascii=True))
    assert boundary._contains_secret(payload, secret)


def test_environment_inventory_uses_canonical_names(monkeypatch: pytest.MonkeyPatch) -> None:
    class Distribution:
        metadata = {"Name": "Unit_Test.Package"}
        version = "1.2.3"
    monkeypatch.setattr(boundary.metadata, "distributions", lambda: [Distribution()])
    assert boundary.installed_inventory_fingerprint() == content_hash([("unit-test-package", "1.2.3")])


def test_private_environment_never_inherits_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADING212_API_KEY", "fixture-broker-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-inference-secret")
    monkeypatch.setenv("PYTHONPATH", "/untrusted/import/location")
    value = boundary._private_environment(tmp_path)
    assert not {"TRADING212_API_KEY", "OPENAI_API_KEY", "PYTHONPATH", "DATABASE_URL"} & value.keys()
    assert "fixture-" not in json.dumps(value)


def test_real_process_receives_no_host_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING212_API_KEY", "fixture-secret-that-must-not-escape")
    raw = boundary._exchange(
        [sys.executable, "-I", "-c", "import os,sys; sys.stdin.read(); print('TRADING212_API_KEY' not in os.environ)"],
        b"unit-fixture", NativeProcessPolicy(timeout_seconds=5), tmp_path,
    )
    assert raw.strip() == b"True"


def test_real_child_bootstrap_emits_only_safe_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING212_API_KEY", "fixture-broker-secret")
    script = Path(boundary.__file__).resolve().parents[3] / "scripts/native_research_child.py"
    # Missing the audited dependency identity must fail before any application
    # dependency import. The child deliberately returns no raw diagnostic text.
    with pytest.raises(UpstreamUnavailable, match="no bounded report") as captured:
        boundary._exchange(
            [sys.executable, "-I", "-S", "-B", str(script)],
            b'{"invalid_request":"fixture-input-secret"}', NativeProcessPolicy(timeout_seconds=20), tmp_path,
        )
    assert "secret" not in str(captured.value)


def test_json_request_keeps_credentials_off_argv_and_environment(
    runner: boundary.IsolatedNativeRunner, snapshot: ResearchSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "unit-fixture-private-input-not-a-real-key"
    selection = runner.selection.model_copy(update={
        "authentication": "bearer", "credential_environment_variable": "SELECTED_INFERENCE_KEY",
    })
    selected = replace(runner, selection=selection, credential=secret)

    def exchange(argv: list[str], request: bytes, policy: NativeProcessPolicy, workdir: Path) -> bytes:
        assert secret not in repr(argv)
        assert "-I" in argv and "-S" in argv
        assert workdir.name.startswith("money-native-tradingagents-")
        assert not tuple(workdir.iterdir())
        value = json.loads(request)
        assert value["credential"] == secret
        assert set(value) == {"protocol", "role", "bridge_sha256", "selection", "credential",
                              "settings", "policy", "mandate", "snapshot",
                              "expected_python_version", "expected_inventory_sha256",
                              "expected_environment_sha256"}
        assert value["snapshot"]["hash"] == snapshot.hash
        return json.dumps({
            "protocol": boundary.PROTOCOL, "bridge_sha256": value["bridge_sha256"],
            "python_version": value["expected_python_version"],
            "inventory_sha256": value["expected_inventory_sha256"],
            "environment_sha256": value["expected_environment_sha256"],
            "role": value["role"], "ok": False, "category": "UpstreamUnavailable", "calls": [],
        }).encode()

    monkeypatch.setattr(boundary, "_exchange", exchange)
    with pytest.raises(UpstreamUnavailable, match="did not complete") as captured:
        selected(ResearchMandate(), snapshot)
    assert secret not in str(captured.value)
    assert secret not in repr(selected)


@pytest.mark.parametrize("payload", [b"not JSON", b'{"ok":NaN}', b'{"ok":true,"ok":false}', b"[]"])
def test_malformed_output_fails_without_echoing_payload(
    runner: boundary.IsolatedNativeRunner, snapshot: ResearchSnapshot,
    monkeypatch: pytest.MonkeyPatch, payload: bytes,
) -> None:
    monkeypatch.setattr(boundary, "_exchange", lambda *args: payload)
    with pytest.raises(InvalidUpstreamReport, match="JSON response"):
        runner(ResearchMandate(), snapshot)


def test_secret_in_output_is_rejected(
    runner: boundary.IsolatedNativeRunner, snapshot: ResearchSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "unit-fixture-selected-private-key"
    selection = runner.selection.model_copy(update={
        "authentication": "bearer", "credential_environment_variable": "SELECTED_INFERENCE_KEY",
    })
    selected = replace(runner, selection=selection, credential=secret)
    monkeypatch.setattr(boundary, "_exchange", lambda *args: secret.encode())
    with pytest.raises(InvalidUpstreamReport, match="restricted content"):
        selected(ResearchMandate(), snapshot)


def test_real_transport_output_is_bounded(tmp_path: Path) -> None:
    with pytest.raises(InvalidUpstreamReport, match="transport bound"):
        boundary._exchange([sys.executable, "-I", "-c", "print('x'*10000)"], b"{}",
                           NativeProcessPolicy(timeout_seconds=5, maximum_output_bytes=1000), tmp_path)


def test_real_transport_deadline_terminates_process_group(tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(NativeDeadline):
        boundary._exchange([sys.executable, "-I", "-c", "import time;time.sleep(10)"], b"{}",
                           NativeProcessPolicy(timeout_seconds=0.15), tmp_path)
    assert time.monotonic() - started < 2


def test_real_transport_kills_descendants(tmp_path: Path) -> None:
    # A test fixture deliberately spawns before any native guard; real engines
    # cannot spawn. Verify cleanup of the whole session, not just its leader.
    marker = tmp_path / "should-never-be-created"
    child = "import pathlib,time;time.sleep(0.8);pathlib.Path(" + repr(str(marker)) + ").touch()"
    script = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);time.sleep(10)"
    with pytest.raises(NativeDeadline):
        boundary._exchange([sys.executable, "-I", "-c", script], b"{}",
                           NativeProcessPolicy(timeout_seconds=0.2), tmp_path)
    time.sleep(0.9)
    assert not marker.exists()


def test_child_errors_never_echo_invalid_input(tmp_path: Path) -> None:
    result = json.loads(boundary.execute_child_request({"secret": "fixture-secret"}, tmp_path, tmp_path))
    assert result["ok"] is False
    assert result["category"] == "UpstreamUnavailable"
    assert "fixture-secret" not in json.dumps(result)


def test_bridge_source_fingerprint_changes_and_denies_symlinks(tmp_path: Path) -> None:
    script = tmp_path / "scripts/native_research_child.py"
    source = tmp_path / "src/money/example.py"
    script.parent.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    script.write_text("# unit fixture only\n")
    source.write_text("# unit fixture source one\n")
    first = boundary.bridge_fingerprint(tmp_path)
    source.write_text("# unit fixture source two\n")
    assert boundary.bridge_fingerprint(tmp_path) != first
    source.unlink()
    source.symlink_to(script)
    with pytest.raises(UpstreamUnavailable, match="integrity"):
        boundary.bridge_fingerprint(tmp_path)


def test_foreign_role_or_snapshot_report_never_admitted(
    runner: boundary.IsolatedNativeRunner, snapshot: ResearchSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exchange(*args: Any) -> bytes:
        value = json.loads(args[1])
        return json.dumps({
            "protocol": boundary.PROTOCOL, "role": "ai_hedge_fund",
            "bridge_sha256": value["bridge_sha256"], "ok": True, "calls": [], "result": {},
            "python_version": value["expected_python_version"],
            "inventory_sha256": value["expected_inventory_sha256"],
            "environment_sha256": value["expected_environment_sha256"],
        }).encode()
    monkeypatch.setattr(boundary, "_exchange", exchange)
    with pytest.raises(InvalidUpstreamReport):
        runner(ResearchMandate(), snapshot)


@pytest.mark.parametrize("field,value", [
    ("inventory_sha256", content_hash({"changed": True})),
    ("python_version", "3.0.0"),
    ("bridge_sha256", content_hash({"changed": True})),
    ("environment_sha256", content_hash({"changed": True})),
])
def test_changed_child_environment_is_not_admitted(
    runner: boundary.IsolatedNativeRunner, snapshot: ResearchSnapshot,
    monkeypatch: pytest.MonkeyPatch, field: str, value: str,
) -> None:
    def exchange(*args: Any) -> bytes:
        request = json.loads(args[1])
        payload = {
            "protocol": boundary.PROTOCOL, "role": "tradingagents", "ok": True,
            "bridge_sha256": request["bridge_sha256"], "calls": [], "result": {},
            "python_version": request["expected_python_version"],
            "inventory_sha256": request["expected_inventory_sha256"],
            "environment_sha256": request["expected_environment_sha256"],
        }
        payload[field] = value
        return json.dumps(payload).encode()
    monkeypatch.setattr(boundary, "_exchange", exchange)
    with pytest.raises(InvalidUpstreamReport):
        runner(ResearchMandate(), snapshot)


def test_guard_precedes_source_verification_and_upstream_execution(
    runner: boundary.IsolatedNativeRunner, snapshot: ResearchSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import asdict

    from money.adapters import native_attestation, native_process, native_qualitative

    events: list[str] = []
    monkeypatch.setattr(boundary.sys, "executable", str(runner.interpreter))
    monkeypatch.setattr(boundary.sys, "prefix", str(runner.interpreter.parent.parent))
    monkeypatch.setattr(boundary, "installed_inventory_fingerprint", lambda: runner.expected_inventory_sha256)
    monkeypatch.setattr(boundary, "installed_files_fingerprint", lambda path: runner.expected_environment_sha256)
    monkeypatch.setattr(native_process, "_install_capability_guard", lambda *args: events.append("guard"))
    monkeypatch.setattr(native_attestation, "require_pinned_source", lambda *args: events.append("source"))

    class NativeFixture:
        def __init__(self, *args: Any) -> None:
            events.append("runner")

        def __call__(self, *args: Any) -> TradingAgentsResearchReport:
            events.append("research")
            return TradingAgentsResearchReport(
                snapshot_id=snapshot.snapshot_id, snapshot_hash=snapshot.hash,
                conclusion="Synthetic boundary test, not qualification", claims=(),
                model_version="qwen3:14b", prompt_version="fixture",
                upstream_sha=UPSTREAM_SHAS["tradingagents"], model_family="qwen3:14b", created_at=NOW,
            )

    monkeypatch.setattr(native_qualitative, "TradingAgentsNativeRunner", NativeFixture)
    value = {
        "protocol": boundary.PROTOCOL, "role": runner.role, "bridge_sha256": runner.expected_bridge_sha256,
        "expected_python_version": runner.expected_python_version,
        "expected_inventory_sha256": runner.expected_inventory_sha256,
        "expected_environment_sha256": runner.expected_environment_sha256,
        "selection": runner.selection.model_dump(mode="json"), "credential": None,
        "settings": asdict(runner.settings), "policy": asdict(runner.policy),
        "mandate": ResearchMandate().model_dump(mode="json"), "snapshot": snapshot.model_dump(mode="json"),
    }
    result = json.loads(boundary.execute_child_request(value, runner.repository, runner.repository))
    assert result["ok"] is True
    assert events == ["guard", "source", "runner", "research"]
    events.clear()
    value["expected_inventory_sha256"] = content_hash({"tampered": True})
    result = json.loads(boundary.execute_child_request(value, runner.repository, runner.repository))
    assert result["ok"] is False
    assert events == []


def test_report_citation_validation_is_not_bypassed(
    runner: boundary.IsolatedNativeRunner, snapshot: ResearchSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = TradingAgentsResearchReport(
        snapshot_id=snapshot.snapshot_id, snapshot_hash=snapshot.hash, conclusion="Fixture",
        claims=(Claim(claim_id="fixture", family="risk", statement="Fixture not real evidence",
                      evidence_ids=("absent-evidence",)),),
        model_version="qwen3:14b", prompt_version="fixture",
        upstream_sha=UPSTREAM_SHAS["tradingagents"], model_family="qwen3:14b", created_at=NOW,
    )
    def exchange(*args: Any) -> bytes:
        value = json.loads(args[1])
        return json.dumps({
            "protocol": boundary.PROTOCOL, "role": "tradingagents",
            "bridge_sha256": value["bridge_sha256"], "ok": True, "calls": [],
            "python_version": value["expected_python_version"],
            "inventory_sha256": value["expected_inventory_sha256"],
            "environment_sha256": value["expected_environment_sha256"],
            "result": report.model_dump(mode="json"),
        }).encode()
    monkeypatch.setattr(boundary, "_exchange", exchange)
    with pytest.raises(InvalidUpstreamReport, match="missing or unknown"):
        runner(ResearchMandate(), snapshot)


def test_installed_files_fingerprint_matches_bootstrap_and_detects_code_changes(tmp_path: Path) -> None:
    script = Path(boundary.__file__).resolve().parents[3] / "scripts/native_research_child.py"
    bootstrap = runpy.run_path(str(script))["installed_files_fingerprint"]
    (tmp_path / "pyvenv.cfg").write_text("# synthetic fixture environment\n")
    package = tmp_path / "lib/python3.12/site-packages/example.py"
    package.parent.mkdir(parents=True)
    package.write_text("# synthetic example\n")
    original = boundary.installed_files_fingerprint(tmp_path)
    assert bootstrap(tmp_path) == original
    package.write_text("# changed source with same distribution version\n")
    assert boundary.installed_files_fingerprint(tmp_path) != original
    assert boundary.installed_files_fingerprint(tmp_path) == bootstrap(tmp_path)
    current = bootstrap(tmp_path)
    bytecode = package.parent / "untracked.pyc"
    bytecode.write_bytes(b"fixture-bytecode-bytes")
    assert bootstrap(tmp_path) != current
    assert bootstrap(tmp_path) == boundary.installed_files_fingerprint(tmp_path)
    bytecode.unlink()
    bytecode.symlink_to(package)
    with pytest.raises(ValueError, match="INTEGRITY"):
        bootstrap(tmp_path)
    with pytest.raises(ValueError, match="INTEGRITY"):
        boundary.installed_files_fingerprint(tmp_path)
