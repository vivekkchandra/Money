"""Verify actual native Python source against read-only pinned package manifests.

The digest is SHA256 of sorted ``sha256  package/relative.py\n`` records,
generated from the package directory at UPSTREAM_LOCK.txt's pinned commit.
No provider can select a module path or replace these expected digests.
"""

from __future__ import annotations

import hashlib
import os
import stat
from importlib.machinery import SourceFileLoader
from importlib.util import find_spec
from pathlib import Path

from money.adapters.upstream import UpstreamUnavailable

SOURCE_DIGESTS = {
    "tradingagents": "0eec12d7ccff614c8124522ee5c9df2a5ec03187a693607bcd15221636d599f6",
    "hedge_fund": "4323005f036350ed3bb3b959a10215e4d5c7ecbc26d179751c15d8c4507a77e6",
    "qlib": "11c4744c882a42171df754d70a6cc9d3b17c7b7973aa277ae5358e2a90cd152b",
    "crewai": "a94c3acee11475019649c8be91f2caad28ab8d41023b9b993d5481a7a6c301cb",
}

# SHA256 of exactly one source file at stream-read-xbrl's UPSTREAM_LOCK.txt SHA
# b95b48bbf50727648cebcba56634b17dc9e60ad3. Never fingerprint site-packages.
SINGLE_MODULE_SOURCE_DIGESTS = {
    "stream_read_xbrl": "afff17f5a281e2474dcf11d6c8d407c08522cd144d7d075631e6e09c6ebc8b33",
}


def require_pinned_module(module: str) -> str:
    """Attest an allowlisted, bounded standalone module before importing it."""
    expected = SINGLE_MODULE_SOURCE_DIGESTS.get(module)
    if expected is None:
        raise UpstreamUnavailable("pinned native module is not allowlisted")
    try:
        spec = find_spec(module)
        if (
            spec is None
            or spec.origin is None
            or spec.submodule_search_locations is not None
            or not isinstance(spec.loader, SourceFileLoader)
        ):
            raise UpstreamUnavailable("pinned native source module is unavailable")
        path = Path(spec.origin)
        if (
            path.name != module + ".py"
            or path.is_symlink()
            or path.resolve() != path.absolute()
            or not path.is_file()
            or path.stat().st_size > 5_000_000
        ):
            raise UpstreamUnavailable("native source module is unsafe or oversized")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 5_000_000:
                raise UpstreamUnavailable("native source module is unsafe or oversized")
            content = stream.read(5_000_001)
        if len(content) > 5_000_000:
            raise UpstreamUnavailable("native source module is unsafe or oversized")
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected:
            raise UpstreamUnavailable("installed native module differs from its pinned source")
        return digest
    except UpstreamUnavailable:
        raise
    except (ImportError, OSError, ValueError, RuntimeError) as error:
        raise UpstreamUnavailable("pinned native source module is unavailable") from error


def source_fingerprint(root: Path, package: str) -> str:
    files = sorted(root.rglob("*.py"), key=lambda path: path.relative_to(root).as_posix())
    if not files or len(files) > 10000:
        raise UpstreamUnavailable("native source package has invalid file coverage")
    lines = []
    for path in files:
        if path.is_symlink() or path.stat().st_size > 5_000_000:
            raise UpstreamUnavailable("native source manifest includes unsafe files")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {package}/{path.relative_to(root).as_posix()}\n")
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def require_pinned_source(package: str) -> None:
    spec = find_spec(package)
    if spec is None or spec.origin is None or package not in SOURCE_DIGESTS:
        raise UpstreamUnavailable("pinned native source package is unavailable")
    if source_fingerprint(Path(spec.origin).parent, package) != SOURCE_DIGESTS[package]:
        raise UpstreamUnavailable(
            "installed native source differs from its pinned upstream manifest"
        )
