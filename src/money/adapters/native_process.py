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

from pydantic import BaseModel

from money.adapters.native import NativeDeadline
from money.adapters.upstream import (
    InvalidUpstreamReport,
    UnsupportedSnapshotData,
    UpstreamUnavailable,
)
from money.data.security import ProviderFailure


class ProviderEscapeDenied(PermissionError):
    """A native component requested a capability outside its frozen snapshot."""


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
        if event == "socket.getaddrinfo":
            if str(args[0]).lower() not in allowed_hosts | allowed_addresses:
                raise ProviderEscapeDenied("native DNS lookup outside configured inference gateway")
        elif event == "socket.connect":
            address = args[1]
            if (not isinstance(address, tuple) or len(address) < 2
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
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            _install_capability_guard(policy, Path(workdir))
            result = runner(*arguments)
            payload = json.dumps({"ok": True, "result": result.model_dump(mode="json")},
                                 allow_nan=False).encode()
            if len(payload) > policy.maximum_output_bytes:
                raise InvalidUpstreamReport("native report exceeds transport bound")
        except BaseException as exc:
            provider_code = None
            provider_retryable = False
            if isinstance(exc, ProviderFailure) and exc.code in {
                "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "PROVIDER_RATE_LIMITED",
                "PROVIDER_COVERAGE_MISSING", "PROVIDER_CIRCUIT_OPEN", "PROVIDER_DNS_UNAVAILABLE",
            }:
                provider_code, provider_retryable = exc.code, exc.retryable
            category = type(exc).__name__
            if category not in {"InvalidUpstreamReport", "UnsupportedSnapshotData", "NativeDeadline",
                                 "ProviderEscapeDenied", "UpstreamUnavailable"}:
                category = "UpstreamUnavailable"
            payload = json.dumps({"ok": False, "category": category,
                                  "failure_class": type(exc).__name__,
                                  "capability": str(exc) if isinstance(exc, ProviderEscapeDenied) else None,
                                  "provider_code": provider_code, "retryable": provider_retryable}).encode()
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
                process.start()
                child.close()
                if not parent.poll(self.policy.timeout_seconds):
                    raise NativeDeadline("native process exceeded its execution deadline")
                try:
                    raw = parent.recv_bytes(self.policy.maximum_output_bytes)
                except (OSError, EOFError) as exc:
                    raise UpstreamUnavailable("native process returned no bounded report") from exc
                payload = json.loads(raw)
                if payload.get("ok") is not True:
                    if payload.get("provider_code") in {
                        "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "PROVIDER_RATE_LIMITED",
                        "PROVIDER_COVERAGE_MISSING", "PROVIDER_CIRCUIT_OPEN", "PROVIDER_DNS_UNAVAILABLE",
                    }:
                        raise ProviderFailure(payload["provider_code"], retryable=payload.get("retryable") is True)
                    errors: dict[str, type[Exception]] = {
                        "InvalidUpstreamReport": InvalidUpstreamReport,
                        "UnsupportedSnapshotData": UnsupportedSnapshotData,
                        "NativeDeadline": NativeDeadline,
                        "ProviderEscapeDenied": ProviderEscapeDenied,
                    }
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
