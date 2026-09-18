"""Killable, credential-minimal process boundary for trusted native assemblies.

The audit hook catches Python provider/tool escape. Production still requires
host network policy: an audit hook cannot sandbox arbitrary native machine code.
"""

from __future__ import annotations

import contextlib
import json
import multiprocessing
import os
import socket
import ssl
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter

from money.adapters.native import NativeDeadline
from money.adapters.native_isolation import install_dependency_restrictions
from money.adapters.upstream import (
    InvalidUpstreamReport,
    UnsupportedSnapshotData,
    UpstreamUnavailable,
)
from money.data.security import ProviderFailure
from money.research.call_telemetry import InferenceReceipt, capture_calls, emit_calls


class ProviderEscapeDenied(PermissionError):
    """A native component requested a capability outside its frozen snapshot."""


PROVIDER_FAILURE_CODES = frozenset({
    "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "PROVIDER_RATE_LIMITED",
    "PROVIDER_COVERAGE_MISSING", "PROVIDER_CIRCUIT_OPEN", "PROVIDER_DNS_UNAVAILABLE",
})
NATIVE_DIAGNOSTIC_CODES = frozenset({
    "NATIVE_AGENT_TIMEOUT", "NATIVE_CALL_BUDGET_EXHAUSTED", "INFERENCE_EMPTY_RESPONSE",
    "NATIVE_STRUCTURED_OUTPUT_INVALID", "NATIVE_TOOL_CALL_FAILED", "NATIVE_ADAPTER_EXCEPTION",
    "NATIVE_SUBPROCESS_FAILED", "NATIVE_CAPABILITY_DENIED", "NATIVE_REPORT_INVALID",
    "NATIVE_SNAPSHOT_UNSUPPORTED", "NATIVE_RUNTIME_UNAVAILABLE", "INFERENCE_RESPONSE_INVALID",
    "INFERENCE_MODEL_MISMATCH", "INFERENCE_INCOMPLETE",
})
_CAPABILITY_MESSAGES = frozenset({
    "native listener capability denied", "native DNS lookup outside configured inference gateway",
    "native reverse DNS capability denied", "native datagram/message capability denied",
    "native network connection outside inference gateway", "native process or foreign library execution denied",
    "native file access outside its capability", "native database access outside private workspace",
    "native filesystem mutation outside private workspace", "native file relocation outside private workspace",
    "native filesystem links are not permitted", "native ChromaDB server/backend capability denied",
    "native ChromaDB backend was loaded before capability isolation",
    "native ChromaDB storage/embedding capability denied",
})


class NativeDiagnosticError(UpstreamUnavailable):
    """A fixed, secret-free diagnostic crossing a native process boundary."""

    def __init__(self, code: str) -> None:
        if code not in NATIVE_DIAGNOSTIC_CODES:
            raise ValueError("NATIVE_DIAGNOSTIC_CODE_INVALID")
        self.code = code
        super().__init__(code)


def native_failure_code(error: BaseException) -> str:
    """Classify failures without copying arbitrary exception text or class names."""
    if isinstance(error, NativeDiagnosticError):
        return error.code
    if isinstance(error, ProviderFailure) and error.code in PROVIDER_FAILURE_CODES:
        # A provider timeout is one model request, not the whole agent deadline.
        return error.code
    marker = error.args[0] if len(error.args) == 1 and isinstance(error.args[0], str) else None
    if isinstance(error, NativeDeadline):
        return ("NATIVE_CALL_BUDGET_EXHAUSTED" if marker == "native inference call budget exhausted"
                else "NATIVE_AGENT_TIMEOUT")
    if isinstance(error, TimeoutError):
        return "NATIVE_AGENT_TIMEOUT"
    if isinstance(error, ProviderEscapeDenied):
        return "NATIVE_CAPABILITY_DENIED"
    if isinstance(error, InvalidUpstreamReport):
        return ("NATIVE_STRUCTURED_OUTPUT_INVALID" if marker in {
            "native structured research output is malformed", "NATIVE_STRUCTURED_OUTPUT_INVALID",
            "native challenge response is malformed", "native JSON response is invalid",
        } else "NATIVE_REPORT_INVALID")
    if isinstance(error, UnsupportedSnapshotData):
        return "NATIVE_SNAPSHOT_UNSUPPORTED"
    if isinstance(error, ValueError):
        if marker in {"INFERENCE_EMPTY_RESPONSE", "INFERENCE_RESPONSE_INVALID",
                      "INFERENCE_MODEL_MISMATCH", "INFERENCE_INCOMPLETE"}:
            return str(marker)
        if marker in {"INFERENCE_TOOL_CALL_INVALID", "INFERENCE_TOOL_OUTPUT_DENIED",
                      "NATIVE_TOOL_CALL_FAILED"}:
            return "NATIVE_TOOL_CALL_FAILED"
        if marker in {"INFERENCE_OUTPUT_INVALID", "INFERENCE_NONFINITE_JSON",
                      "INFERENCE_DUPLICATE_JSON_KEY", "INFERENCE_USAGE_INVALID"}:
            return "INFERENCE_RESPONSE_INVALID"
    if isinstance(error, UpstreamUnavailable):
        return "NATIVE_RUNTIME_UNAVAILABLE"
    return "NATIVE_ADAPTER_EXCEPTION"


@dataclass(frozen=True)
class NativeProcessPolicy:
    gateway_hosts: tuple[str, ...] = ()
    gateway_port: int = 443
    timeout_seconds: float = 180
    maximum_output_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        if not 0 < self.timeout_seconds <= 1800 or not 1 <= self.gateway_port <= 65535:
            raise ValueError("invalid native process limits")
        if not 100 <= self.maximum_output_bytes <= 10_000_000:
            raise ValueError("invalid native process output limit")
        if any(not host or "/" in host or "@" in host or "*" in host for host in self.gateway_hosts):
            raise ValueError("inference gateway hosts must be exact hostnames")


def _install_capability_guard(policy: NativeProcessPolicy, workdir: Path) -> None:
    workdir = workdir.resolve()
    allowed_addresses: set[str] = set()
    allowed_hosts = {host.lower() for host in policy.gateway_hosts}
    for host in allowed_hosts:
        for item in socket.getaddrinfo(host, policy.gateway_port, type=socket.SOCK_STREAM):
            allowed_addresses.add(str(item[4][0]))
    certificates = ssl.get_default_verify_paths()
    certificate_roots = tuple(Path(path).resolve().parent for path in
                              (certificates.cafile, certificates.capath) if path)
    read_roots = (Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
                  Path(__file__).resolve().parents[2], workdir, *certificate_roots)

    def allowed_path(value: object, writing: bool = False) -> bool:
        if isinstance(value, int):
            return True  # existing pipe/stdio descriptors, never newly opened host files
        if not isinstance(value, (str, bytes, os.PathLike)):
            return False
        path = Path(os.fsdecode(value)).resolve()
        if path == Path(os.devnull):
            return True
        return any(path.is_relative_to(root) for root in ((workdir,) if writing else read_roots))

    def guard(event: str, args: tuple[Any, ...]) -> None:
        if event == "socket.bind":
            raise ProviderEscapeDenied("native listener capability denied")
        elif event in {"socket.getaddrinfo", "socket.gethostbyname"}:
            if str(args[0]).lower() not in allowed_hosts | allowed_addresses:
                raise ProviderEscapeDenied("native DNS lookup outside configured inference gateway")
        elif event in {"socket.gethostbyaddr", "socket.getnameinfo"}:
            # Reverse DNS is not required by the pinned HTTPS transport. It can
            # otherwise contact an unrelated resolver without socket.connect.
            raise ProviderEscapeDenied("native reverse DNS capability denied")
        elif event in {"socket.sendto", "socket.sendmsg"}:
            # Connectionless sends do not emit socket.connect, including DNS
            # datagrams to a literal address. HTTPS requires neither interface.
            raise ProviderEscapeDenied("native datagram/message capability denied")
        elif event == "socket.connect":
            address = args[1]
            if (args[0].type != socket.SOCK_STREAM
                    or not isinstance(address, tuple) or len(address) < 2
                    or str(address[0]) not in allowed_addresses | allowed_hosts
                    or address[1] != policy.gateway_port):
                raise ProviderEscapeDenied("native network connection outside inference gateway")
        elif event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "ctypes.dlopen"}:
            # Extension loading is needed for numerical packages; only runtime libraries qualify.
            if event == "ctypes.dlopen" and (args[0] is None or allowed_path(args[0])):
                return
            raise ProviderEscapeDenied("native process or foreign library execution denied")
        elif event == "open":
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if not allowed_path(args[0], writing):
                raise ProviderEscapeDenied("native file access outside its capability")
        elif event == "sqlite3.connect":
            if args[0] != ":memory:" and not allowed_path(args[0], writing=True):
                raise ProviderEscapeDenied("native database access outside private workspace")
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.truncate", "os.utime", "os.chdir"}:
            if not allowed_path(args[0], writing=True):
                raise ProviderEscapeDenied("native filesystem mutation outside private workspace")
        elif event in {"os.rename", "shutil.copyfile"}:
            if not all(allowed_path(path, writing=True) for path in args[:2]):
                raise ProviderEscapeDenied("native file relocation outside private workspace")
        elif event in {"os.symlink", "os.link"}:
            raise ProviderEscapeDenied("native filesystem links are not permitted")

    sys.addaudithook(guard)
    install_dependency_restrictions(ProviderEscapeDenied)


def _native_child(connection: Connection, runner: Callable[..., BaseModel],
                  arguments: tuple[object, ...], policy: NativeProcessPolicy,
                  workdir: str) -> None:
    retained = {key: value for key, value in os.environ.items()
                if key in {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL"}}
    os.environ.clear()
    os.environ.update(retained)
    os.environ.update({"PYTHONDONTWRITEBYTECODE": "1", "OTEL_SDK_DISABLED": "true",
                       "CREWAI_TELEMETRY_ENABLED": "false", "CREWAI_TRACING_ENABLED": "false",
                       "DO_NOT_TRACK": "1", "CREWAI_STORAGE_DIR": workdir, "TMPDIR": workdir,
                       "PYTHON_DOTENV_DISABLED": "1"})
    tempfile.tempdir = workdir
    sys.dont_write_bytecode = True
    os.chdir(workdir)
    # Native libraries may print raw responses or prompts; silence both channels.
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink), capture_calls() as calls:
        try:
            _install_capability_guard(policy, Path(workdir))
            result = runner(*arguments)
            payload = json.dumps({"ok": True, "result": result.model_dump(mode="json"),
                                  "calls": [call.model_dump(mode="json") for call in calls]},
                                 allow_nan=False).encode()
            if len(payload) > policy.maximum_output_bytes:
                raise InvalidUpstreamReport("native report exceeds transport bound")
        except BaseException as exc:
            provider_code = None
            provider_retryable = False
            if isinstance(exc, ProviderFailure) and exc.code in PROVIDER_FAILURE_CODES:
                provider_code, provider_retryable = exc.code, exc.retryable
            category = type(exc).__name__
            if category not in {"InvalidUpstreamReport", "UnsupportedSnapshotData", "NativeDeadline",
                                 "ProviderEscapeDenied", "UpstreamUnavailable"}:
                category = "UpstreamUnavailable"
            payload = json.dumps({"ok": False, "category": category,
                                  "diagnostic_code": native_failure_code(exc),
                                  "failure_class": category,
                                  "capability": (str(exc) if isinstance(exc, ProviderEscapeDenied)
                                                 and str(exc) in _CAPABILITY_MESSAGES else None),
                                  "provider_code": provider_code, "retryable": provider_retryable,
                                  "calls": [call.model_dump(mode="json") for call in calls]}).encode()
        connection.send_bytes(payload)
        connection.close()


@dataclass(frozen=True)
class BoundedNativeRunner:
    runner: Callable[..., BaseModel]
    result_type: type[BaseModel]
    policy: NativeProcessPolicy

    def __call__(self, *arguments: object) -> Any:
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory(prefix="money-native-") as temporary:
            parent, child = context.Pipe(duplex=False)
            process = context.Process(target=_native_child,
                args=(child, self.runner, arguments, self.policy, temporary), daemon=True)
            try:
                try:
                    process.start()
                except OSError:
                    raise NativeDiagnosticError("NATIVE_SUBPROCESS_FAILED") from None
                child.close()
                if not parent.poll(self.policy.timeout_seconds):
                    raise NativeDeadline("native process exceeded its execution deadline")
                try:
                    raw = parent.recv_bytes(self.policy.maximum_output_bytes)
                except (OSError, EOFError) as exc:
                    raise NativeDiagnosticError("NATIVE_SUBPROCESS_FAILED") from exc
                payload = json.loads(raw)
                receipts = payload.get("calls", [])
                if not isinstance(receipts, list) or len(receipts) > 256:
                    raise InvalidUpstreamReport("native call accounting exceeds transport bound")
                emit_calls(TypeAdapter(list[InferenceReceipt]).validate_python(receipts))
                if payload.get("ok") is not True:
                    if payload.get("provider_code") in PROVIDER_FAILURE_CODES:
                        raise ProviderFailure(payload["provider_code"], retryable=payload.get("retryable") is True)
                    errors: dict[str, type[Exception]] = {
                        "InvalidUpstreamReport": InvalidUpstreamReport,
                        "UnsupportedSnapshotData": UnsupportedSnapshotData,
                        "NativeDeadline": NativeDeadline,
                        "ProviderEscapeDenied": ProviderEscapeDenied,
                    }
                    diagnostic = payload.get("diagnostic_code")
                    if diagnostic in NATIVE_DIAGNOSTIC_CODES and payload.get("category") not in errors:
                        raise NativeDiagnosticError(diagnostic)
                    if diagnostic == "NATIVE_STRUCTURED_OUTPUT_INVALID":
                        raise InvalidUpstreamReport(diagnostic)
                    if diagnostic == "NATIVE_CALL_BUDGET_EXHAUSTED":
                        raise NativeDeadline("native inference call budget exhausted")
                    raise errors.get(payload.get("category"), UpstreamUnavailable)(
                        "native research failed within its isolated capability ("
                        + str(payload.get("capability") or payload.get("failure_class", "unknown")) + ")"
                    )
                return self.result_type.model_validate(payload["result"])
            finally:
                parent.close()
                child.close()
                if process.pid is not None:
                    process.join(timeout=0.2)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=1)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=1)
                    process.close()
