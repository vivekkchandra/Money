"""Bounded JSON boundary to independently locked native Python environments.

Only the selected inference credential enters the private stdin pipe. No broker,
database or sibling report capability is supplied. The existing Python guard is
defence in depth for trusted audited sources, not hosted OS egress qualification.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter

from money.adapters.native import NativeDeadline, NativeRunSettings
from money.adapters.native_process import NativeProcessPolicy, ProviderEscapeDenied
from money.adapters.upstream import (
    InvalidUpstreamReport,
    UnsupportedSnapshotData,
    UpstreamUnavailable,
    _validate_report,
)
from money.data.security import ProviderFailure
from money.research.call_telemetry import InferenceReceipt, emit_calls
from money.research.inference import strict_response_json
from money.research.inference_config import InferenceSelection
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    FirmReport,
    ResearchMandate,
    ResearchSnapshot,
    TradingAgentsResearchReport,
    content_hash,
)

PROTOCOL = "money-isolated-native-json-v1"
MAXIMUM_INPUT_BYTES = 20_000_000
NATIVE_ENVIRONMENTS = {
    "tradingagents": ".venv-tradingagents",
    "ai_hedge_fund": ".venv-ai-hedge-fund",
}
_PROVIDER_CODES = frozenset({
    "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "PROVIDER_RATE_LIMITED",
    "PROVIDER_COVERAGE_MISSING", "PROVIDER_CIRCUIT_OPEN", "PROVIDER_DNS_UNAVAILABLE",
})
_ERRORS: dict[str, type[Exception]] = {
    "InvalidUpstreamReport": InvalidUpstreamReport,
    "UnsupportedSnapshotData": UnsupportedSnapshotData,
    "NativeDeadline": NativeDeadline,
    "ProviderEscapeDenied": ProviderEscapeDenied,
    "UpstreamUnavailable": UpstreamUnavailable,
}


def bridge_fingerprint(repository: Path) -> str:
    """Bind the fixed bridge and all Money Python imports to actual source bytes."""
    files = [repository / "scripts/native_research_child.py"]
    files.extend(sorted((repository / "src/money").rglob("*.py")))
    if len(files) < 2 or len(files) > 10000:
        raise UpstreamUnavailable("native bridge source unavailable")
    records = []
    for path in files:
        if (path.is_symlink() or not path.is_file() or path.stat().st_size > 5_000_000
                or path.resolve() != path.absolute()):
            raise UpstreamUnavailable("native bridge source integrity failed")
        records.append(hashlib.sha256(path.read_bytes()).hexdigest()
                       + "  " + path.relative_to(repository).as_posix() + "\n")
    return hashlib.sha256("".join(records).encode()).hexdigest()


def installed_inventory_fingerprint() -> str:
    """Match the audited role environment, not the parent/control-plane packages."""
    rows = sorted(
        (re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower(), distribution.version)
        for distribution in metadata.distributions()
    )
    return content_hash(rows)


def installed_files_fingerprint(environment: Path) -> str:
    """Hash installed code/metadata, including bytecode and unexpected additions.

    The deliberately stdlib-only bootstrap has the same algorithm: it must
    authenticate installed Pydantic bytes *before* it can import this module.
    Auditors must run import probes with -B, then fingerprint after installation.
    """
    if (environment.resolve() != environment.absolute()
            or not (environment / "pyvenv.cfg").is_file()
            or not (environment / "lib").is_dir()):
        raise ValueError("ISOLATED_NATIVE_ENVIRONMENT_INTEGRITY_REQUIRED")
    entries = sorted((environment / "lib").rglob("*"))
    if len(entries) > 100_000 or any(path.is_symlink() for path in entries):
        raise ValueError("ISOLATED_NATIVE_ENVIRONMENT_INTEGRITY_REQUIRED")
    files = [environment / "pyvenv.cfg", *(path for path in entries if path.is_file())]
    manifest = hashlib.sha256()
    total = 0
    for path in files:
        if path.is_symlink() or path.resolve() != path.absolute():
            raise ValueError("ISOLATED_NATIVE_ENVIRONMENT_INTEGRITY_REQUIRED")
        before = path.stat()
        total += before.st_size
        if before.st_size > 512_000_000 or total > 8_000_000_000:
            raise ValueError("ISOLATED_NATIVE_ENVIRONMENT_INTEGRITY_REQUIRED")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1_000_000):
                digest.update(block)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("ISOLATED_NATIVE_ENVIRONMENT_INTEGRITY_REQUIRED")
        manifest.update((digest.hexdigest() + "  " + path.relative_to(environment).as_posix() + "\n").encode())
    return manifest.hexdigest()


def _contains_secret(value: object, secret: str | None) -> bool:
    if not secret:
        return False
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(_contains_secret(item, secret) for pair in value.items() for item in pair)
    if isinstance(value, (tuple, list)):
        return any(_contains_secret(item, secret) for item in value)
    return False


def _private_environment(workdir: Path) -> dict[str, str]:
    """Construct from fixed values, never copy the host environment."""
    return {
        "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "PYTHON_DOTENV_DISABLED": "1", "OTEL_SDK_DISABLED": "true",
        "DO_NOT_TRACK": "1", "CREWAI_TELEMETRY_ENABLED": "false",
        "CREWAI_TRACING_ENABLED": "false", "CREWAI_STORAGE_DIR": str(workdir),
        "TMPDIR": str(workdir), "XDG_CACHE_HOME": str(workdir / "cache"),
    }


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    # A short-lived child may have left descendants with its process-group ID.
    # Kill the entire dedicated session even after its original leader exited.
    if process.pid:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        raise UpstreamUnavailable("native process group did not terminate") from None


def _exchange(
    argv: list[str], request: bytes, policy: NativeProcessPolicy, workdir: Path,
) -> bytes:
    """Multiplex both pipes, bounding memory and wall time including startup."""
    if len(request) > MAXIMUM_INPUT_BYTES:
        raise UnsupportedSnapshotData("native request exceeds transport bound")
    process = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=workdir, env=_private_environment(workdir), start_new_session=True,
        close_fds=True,
    )
    result = bytearray()
    deadline = time.monotonic() + policy.timeout_seconds
    assert process.stdin is not None and process.stdout is not None
    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            remaining = memoryview(request)
            while selector.get_map():
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    raise NativeDeadline("native process exceeded its execution deadline")
                for key, _ in selector.select(timeout=min(timeout, 0.1)):
                    if key.fileobj is process.stdin:
                        try:
                            sent = os.write(process.stdin.fileno(), remaining[:65536])
                            remaining = remaining[sent:]
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            remaining = memoryview(b"")
                        if not remaining:
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        try:
                            chunk = os.read(process.stdout.fileno(), min(
                                65536, policy.maximum_output_bytes + 1 - len(result),
                            ))
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(process.stdout)
                        result.extend(chunk)
                        if len(result) > policy.maximum_output_bytes:
                            raise InvalidUpstreamReport("native report exceeds transport bound")
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise NativeDeadline("native process exceeded its execution deadline") from None
        if process.returncode != 0 or not result:
            raise UpstreamUnavailable("native interpreter returned no bounded report")
        return bytes(result)
    finally:
        _terminate_group(process)
        process.stdin.close()
        process.stdout.close()


@dataclass(frozen=True)
class IsolatedNativeRunner:
    """Invoke the audited role-specific interpreter; never a configurable command."""

    interpreter: Path
    role: Literal["tradingagents", "ai_hedge_fund"]
    selection: InferenceSelection
    settings: NativeRunSettings
    policy: NativeProcessPolicy
    expected_python_version: str
    expected_inventory_sha256: str
    expected_bridge_sha256: str
    expected_environment_sha256: str
    credential: str | None = field(default=None, repr=False)
    repository: Path = field(default_factory=lambda: Path(__file__).resolve().parents[3])

    def __post_init__(self) -> None:
        if self.role not in NATIVE_ENVIRONMENTS or not self.settings.verify_source_pin:
            raise ValueError("ISOLATED_NATIVE_PINNED_ROLE_REQUIRED")
        if (re.fullmatch(r"\d+\.\d+\.\d+", self.expected_python_version) is None
                or re.fullmatch(r"[a-f0-9]{64}", self.expected_inventory_sha256) is None
                or re.fullmatch(r"[a-f0-9]{64}", self.expected_environment_sha256) is None
                or bridge_fingerprint(self.repository) != self.expected_bridge_sha256):
            raise ValueError("ISOLATED_NATIVE_AUDITED_IDENTITY_REQUIRED")
        expected = self.repository / NATIVE_ENVIRONMENTS[self.role] / "bin/python"
        if (self.repository.resolve() != self.repository.absolute()
                or self.interpreter.absolute() != expected.absolute()
                or not self.interpreter.is_file() or not os.access(self.interpreter, os.X_OK)
                or expected.parent.parent.is_symlink() or expected.parent.is_symlink()):
            raise ValueError("ISOLATED_NATIVE_VERIFIED_INTERPRETER_REQUIRED")
        if self.selection.authentication == "none":
            if self.credential is not None:
                raise ValueError("INFERENCE_UNUSED_CREDENTIAL_DENIED")
        elif not self.credential or len(self.credential) > 16384:
            raise ValueError("INFERENCE_CREDENTIAL_MISSING")
        values = {} if self.credential is None else {
            str(self.selection.credential_environment_variable): self.credential,
        }
        transport = self.selection.inference(values)
        if (self.policy.gateway_hosts != transport.allowed_network_hosts
                or self.policy.gateway_port != transport.allowed_network_port
                or self.policy.timeout_seconds != self.settings.timeout_seconds):
            raise ValueError("ISOLATED_NATIVE_CAPABILITY_POLICY_MISMATCH")

    def __call__(self, mandate: ResearchMandate, snapshot: ResearchSnapshot) -> FirmReport:
        self.__post_init__()
        frozen = ResearchSnapshot.model_validate_json(snapshot.model_dump_json())
        if (frozen.purpose != "RESEARCH_TESTING" or frozen.usage_mode != "PERSONAL_RESEARCH"
                or frozen.qlib_enabled):
            raise ValueError("ISOLATED_NATIVE_PERSONAL_RESEARCH_REQUIRED")
        request = json.dumps({
            "protocol": PROTOCOL, "role": self.role,
            "bridge_sha256": self.expected_bridge_sha256,
            "expected_python_version": self.expected_python_version,
            "expected_inventory_sha256": self.expected_inventory_sha256,
            "expected_environment_sha256": self.expected_environment_sha256,
            "selection": self.selection.model_dump(mode="json"),
            "credential": self.credential, "settings": asdict(self.settings),
            "policy": asdict(self.policy), "mandate": mandate.model_dump(mode="json"),
            "snapshot": frozen.model_dump(mode="json"),
        }, allow_nan=False).encode()
        with tempfile.TemporaryDirectory(prefix="money-native-" + self.role + "-") as temporary:
            raw = _exchange(
                [str(self.interpreter), "-I", "-S", "-B",
                 str(self.repository / "scripts/native_research_child.py")],
                request, self.policy, Path(temporary),
            )
        # Even a faulty native package cannot disclose the selected credential via reports.
        if self.credential and self.credential in raw.decode("utf-8", errors="replace"):
            raise InvalidUpstreamReport("native response contains restricted content")
        try:
            payload = strict_response_json(raw)
            if _contains_secret(payload, self.credential):
                raise InvalidUpstreamReport("native response contains restricted content")
            if (payload.get("protocol") != PROTOCOL
                    or payload.get("bridge_sha256") != bridge_fingerprint(self.repository)
                    or payload.get("python_version") != self.expected_python_version
                    or payload.get("inventory_sha256") != self.expected_inventory_sha256
                    or payload.get("environment_sha256") != self.expected_environment_sha256
                    or payload.get("role") != self.role):
                raise ValueError
            receipts = payload.get("calls", [])
            if not isinstance(receipts, list) or len(receipts) > 256:
                raise ValueError
            calls = TypeAdapter(list[InferenceReceipt]).validate_python(receipts)
            if any(call.provider != self.selection.provider or call.model != self.selection.model
                   for call in calls):
                raise ValueError
            emit_calls(calls)
            if payload.get("ok") is not True:
                code = payload.get("provider_code")
                if code in _PROVIDER_CODES:
                    raise ProviderFailure(code, retryable=payload.get("retryable") is True)
                error = _ERRORS.get(str(payload.get("category")), UpstreamUnavailable)
                raise error("isolated native research did not complete")
            schema = (TradingAgentsResearchReport if self.role == "tradingagents"
                      else AIHedgeFundResearchReport)
            report = schema.model_validate(payload["result"])
            return _validate_report(report, frozen, schema, self.role)
        except (KeyError, TypeError, json.JSONDecodeError):
            raise InvalidUpstreamReport("native JSON response is invalid") from None
        except ValueError as error:
            if isinstance(error, (UnsupportedSnapshotData, InvalidUpstreamReport)):
                raise
            raise InvalidUpstreamReport("native JSON response is invalid") from None


def execute_child_request(value: dict[str, Any], workdir: Path, repository: Path) -> bytes:
    """Fixed child entry point. All caller data is revalidated before any engine import."""
    from money.adapters.native_attestation import require_pinned_source
    from money.adapters.native_process import _install_capability_guard
    from money.research.call_telemetry import capture_calls

    maximum_output = 100_000
    role: str | None = None
    digest: str | None = None
    credential: str | None = None
    python_version: str | None = None
    inventory_sha256: str | None = None
    environment_sha256: str | None = None
    with capture_calls() as calls:
        try:
            if set(value) != {"protocol", "role", "bridge_sha256", "selection", "credential",
                              "settings", "policy", "mandate", "snapshot",
                              "expected_python_version", "expected_inventory_sha256",
                              "expected_environment_sha256"}:
                raise ValueError("request shape")
            role = value["role"]
            digest = bridge_fingerprint(repository)
            if value["protocol"] != PROTOCOL or value["bridge_sha256"] != digest:
                raise ValueError("bridge identity")
            if role not in NATIVE_ENVIRONMENTS:
                raise ValueError("role")
            expected_environment = repository / NATIVE_ENVIRONMENTS[role]
            if (Path(sys.executable).absolute() != expected_environment / "bin/python"
                    or Path(sys.prefix) != expected_environment):
                raise ValueError("interpreter")
            python_version = platform.python_version()
            inventory_sha256 = installed_inventory_fingerprint()
            environment_sha256 = installed_files_fingerprint(expected_environment)
            if (python_version != value["expected_python_version"]
                    or inventory_sha256 != value["expected_inventory_sha256"]
                    or environment_sha256 != value["expected_environment_sha256"]):
                raise ValueError("audited environment changed")
            selection = InferenceSelection.model_validate(value["selection"])
            settings = NativeRunSettings(**value["settings"])
            policy = NativeProcessPolicy(**(value["policy"] | {
                "gateway_hosts": tuple(value["policy"]["gateway_hosts"]),
            }))
            maximum_output = policy.maximum_output_bytes
            if not settings.verify_source_pin:
                raise ValueError("source verification required")
            credential = value["credential"]
            if selection.authentication == "none":
                if credential is not None:
                    raise ValueError("unexpected credential")
            elif not isinstance(credential, str) or not credential or len(credential) > 16384:
                raise ValueError("missing credential")
            transport = selection.inference({} if credential is None else {
                str(selection.credential_environment_variable): credential,
            })
            if (policy.gateway_hosts != transport.allowed_network_hosts
                    or policy.gateway_port != transport.allowed_network_port
                    or policy.timeout_seconds != settings.timeout_seconds):
                raise ValueError("capability mismatch")
            mandate = ResearchMandate.model_validate(value["mandate"])
            snapshot = ResearchSnapshot.model_validate(value["snapshot"])
            if (snapshot.purpose != "RESEARCH_TESTING"
                    or snapshot.usage_mode != "PERSONAL_RESEARCH" or snapshot.qlib_enabled):
                raise ValueError("research purpose")
            # Installation/source lookup occurs only after environment sanitization and guard.
            _install_capability_guard(policy, workdir)
            require_pinned_source("tradingagents" if role == "tradingagents" else "hedge_fund")
            from money.adapters.native_qualitative import (
                AIHedgeFundNativeRunner,
                TradingAgentsNativeRunner,
            )
            runner = TradingAgentsNativeRunner if role == "tradingagents" else AIHedgeFundNativeRunner
            report = runner(transport, settings)(mandate, snapshot)
            payload = {"ok": True, "result": report.model_dump(mode="json")}
        except BaseException as error:
            category = type(error).__name__
            payload = {"ok": False, "category": category if category in _ERRORS
                       else "UpstreamUnavailable"}
            if isinstance(error, ProviderFailure) and error.code in _PROVIDER_CODES:
                payload.update(provider_code=error.code, retryable=error.retryable)
        payload.update(protocol=PROTOCOL, role=role, bridge_sha256=digest,
                       python_version=python_version, inventory_sha256=inventory_sha256,
                       environment_sha256=environment_sha256,
                       calls=[call.model_dump(mode="json") for call in calls])
        raw = json.dumps(payload, allow_nan=False).encode()
        if len(raw) > maximum_output or _contains_secret(payload, credential):
            raw = json.dumps({"protocol": PROTOCOL, "role": role, "bridge_sha256": digest,
                              "python_version": python_version, "inventory_sha256": inventory_sha256,
                              "environment_sha256": environment_sha256,
                              "ok": False, "category": "InvalidUpstreamReport", "calls": []}).encode()
        return raw
