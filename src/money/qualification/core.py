"""Secret-safe, bounded storage and resumable state for qualification steps.

Checkpoints are an optimization, never an admission authority. Every consumer
must still validate its contracts, freshness and linked evidence before use.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from money.research.live import read_qualification_bytes

MAXIMUM_BYTES = 2_000_000
SECRET_NAMES = (
    "TRADING212_API_KEY",
    "TRADING212_API_SECRET",
    "EODHD_API_KEY",
    "COMPANIES_HOUSE_API_KEY",
    "OPENAI_API_KEY",
    "RESEARCH_API_TOKEN",
    "DATABASE_URL",
)


def json_bytes(value: Any) -> bytes:
    """Encode deterministic non-executable JSON without accepting NaN."""
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def assert_secret_free(raw: bytes, environ: Mapping[str, str]) -> None:
    """Reject known credentials and encoded authentication before any output."""
    values = {
        value
        for name, value in environ.items()
        if value
        and (
            name in SECRET_NAMES
            or any(part in name.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))
        )
    }
    key, secret = environ.get("TRADING212_API_KEY"), environ.get("TRADING212_API_SECRET")
    if key and secret:
        values.add(f"{key}:{secret}")
    company = environ.get("COMPANIES_HOUSE_API_KEY")
    if company:
        values.add(company + ":")
    for value in values:
        encodings = (
            value.encode(),
            json.dumps(value)[1:-1].encode(),
            quote(value, safe="").encode(),
            base64.b64encode(value.encode()),
        )
        if any(encoded and encoded in raw for encoded in encodings):
            raise ValueError("QUALIFICATION_SECRET_DETECTED")


@dataclass
class QualificationContext:
    root: Path
    repo: Path
    environ: Mapping[str, str] = field(repr=False)
    now: datetime
    blockers: list[dict[str, str]] = field(default_factory=list)
    artifact_refs: set[tuple[str, str]] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("QUALIFICATION_CLOCK_INVALID")
        self.root = self.root.absolute()
        self.repo = self.repo.resolve()
        self.check_secrets(str(self.root).encode())
        self._directory(self.root)

    def _directory(self, path: Path) -> None:
        # Reject existing symlinks in the entire output path, not only leaves.
        for part in (*reversed(path.parents), path):
            if part.is_symlink():
                raise ValueError("QUALIFICATION_PATH_UNSAFE")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.is_dir():
            raise ValueError("QUALIFICATION_PATH_UNSAFE")

    def _path(self, relative: str) -> Path:
        path = Path(relative)
        if (
            not relative
            or path.is_absolute()
            or path.as_posix() != relative
            or "\\" in relative
            or any(p in {".", ".."} for p in path.parts)
        ):
            raise ValueError("QUALIFICATION_PATH_UNSAFE")
        target = self.root / path
        if target.is_symlink():
            raise ValueError("QUALIFICATION_PATH_UNSAFE")
        self._directory(target.parent)
        return target

    def check_secrets(self, raw: bytes) -> None:
        """Reject known credentials and encoded header forms before any write."""
        assert_secret_free(raw, self.environ)

    def read_bytes(self, relative: str, maximum: int = MAXIMUM_BYTES) -> bytes | None:
        target = self._path(relative)
        if not target.exists():
            return None
        raw = read_qualification_bytes(self.root, relative, maximum)
        self.check_secrets(raw)
        return raw

    def read_json(self, relative: str) -> Any | None:
        raw = self.read_bytes(relative)
        return json.loads(raw) if raw is not None else None

    def write_bytes(self, relative: str, raw: bytes, *, replace: bool = True) -> None:
        if not raw or len(raw) > MAXIMUM_BYTES:
            raise ValueError("QUALIFICATION_FILE_SIZE_INVALID")
        self.check_secrets(raw)
        target = self._path(relative)
        if not replace:
            try:
                descriptor = os.open(
                    target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
            except FileExistsError:
                return
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            return
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def write_json(self, relative: str, value: Any) -> None:
        self.write_bytes(relative, json_bytes(value))

    def template(self, relative: str, value: Any) -> None:
        """Never overwrite an operator's edits on resume."""
        self.write_bytes(relative, json_bytes(value), replace=False)

    def artifact(self, value: Any | bytes) -> tuple[str, str]:
        raw = value if isinstance(value, bytes) else json_bytes(value)
        digest = hashlib.sha256(raw).hexdigest()
        extension = "bin" if isinstance(value, bytes) else "json"
        relative = f"artifacts/{digest}.{extension}"
        existing = self.read_bytes(relative)
        if existing is not None and existing != raw:
            raise ValueError("QUALIFICATION_ARTIFACT_COLLISION")
        self.write_bytes(relative, raw, replace=False)
        self.artifact_refs.add((digest, relative))
        return digest, relative

    def verify_artifact(self, digest: str, relative: str) -> bytes:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("QUALIFICATION_ARTIFACT_HASH_INVALID")
        raw = self.read_bytes(relative)
        if raw is None or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("QUALIFICATION_ARTIFACT_HASH_MISMATCH")
        self.artifact_refs.add((digest, relative))
        return raw

    def block(self, code: str, action: str) -> None:
        item = {"code": code, "action": action}
        self.check_secrets(json_bytes(item))
        if item not in self.blockers:
            self.blockers.append(item)

    def cache(
        self, name: str, input_fingerprint: str, max_age_seconds: int
    ) -> dict[str, Any] | None:
        if not re.fullmatch(r"[a-z0-9_-]+", name):
            raise ValueError("QUALIFICATION_CACHE_NAME_INVALID")
        try:
            entry = self.read_json(f"state/{name}.json")
            if not isinstance(entry, dict) or entry["input_fingerprint"] != input_fingerprint:
                return None
            observed = datetime.fromisoformat(entry["created_at"])
            if not observed <= self.now < observed + timedelta(seconds=max_age_seconds):
                return None
            for digest, relative in entry["artifacts"]:
                self.verify_artifact(digest, relative)
            return entry["value"] if isinstance(entry["value"], dict) else None
        except (ValueError, KeyError, TypeError):
            return None

    def checkpoint(
        self,
        name: str,
        input_fingerprint: str,
        value: dict[str, Any],
        artifacts: Sequence[tuple[str, str]] = (),
    ) -> None:
        if not re.fullmatch(r"[a-z0-9_-]+", name):
            raise ValueError("QUALIFICATION_CACHE_NAME_INVALID")
        for digest, relative in artifacts:
            self.verify_artifact(digest, relative)
        self.write_json(
            f"state/{name}.json",
            {
                "input_fingerprint": input_fingerprint,
                "created_at": self.now.isoformat(),
                "artifacts": list(artifacts),
                "value": value,
            },
        )

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Prevent two qualification processes overwriting each other's receipts."""
        import fcntl

        descriptor = os.open(
            self._path(".runner.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("QUALIFICATION_LOCK_UNSAFE")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("QUALIFICATION_ALREADY_RUNNING") from None
            yield
        finally:
            os.close(descriptor)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    output: bytes = field(repr=False)


def run_captured(
    argv: Sequence[str],
    *,
    cwd: Path,
    environ: Mapping[str, str],
    timeout: int = 1200,
    maximum_output: int = MAXIMUM_BYTES,
) -> CommandResult:
    """Capture bounded subprocess output in memory; never spool raw logs to disk."""
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=dict(environ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    output = bytearray()
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            started = time.monotonic()
            while selector.get_map():
                if time.monotonic() - started > timeout:
                    raise ValueError("QUALIFICATION_COMMAND_TIMEOUT")
                for key, _ in selector.select(timeout=0.2):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > maximum_output:
                            raise ValueError("QUALIFICATION_COMMAND_OUTPUT_LIMIT")
            return CommandResult(
                process.wait(timeout=max(1, timeout - int(time.monotonic() - started))),
                bytes(output),
            )
    finally:
        # uv/pytest/native runtimes can spawn children. Terminate only the exact
        # session owned by this command, including on timeout or output overflow.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            process.wait()
        if process.stdout is not None:
            process.stdout.close()
