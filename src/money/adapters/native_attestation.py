"""Verify actual native Python source against read-only pinned package manifests.

The digest is SHA256 of sorted ``sha256  package/relative.py\n`` records,
generated from the package directory at UPSTREAM_LOCK.txt's pinned commit.
No provider can select a module path or replace these expected digests.
"""

from __future__ import annotations

import hashlib
from importlib.util import find_spec
from pathlib import Path

from money.adapters.upstream import UpstreamUnavailable

SOURCE_DIGESTS = {
    "tradingagents": "0eec12d7ccff614c8124522ee5c9df2a5ec03187a693607bcd15221636d599f6",
    "hedge_fund": "4323005f036350ed3bb3b959a10215e4d5c7ecbc26d179751c15d8c4507a77e6",
    "qlib": "11c4744c882a42171df754d70a6cc9d3b17c7b7973aa277ae5358e2a90cd152b",
    "crewai": "a94c3acee11475019649c8be91f2caad28ab8d41023b9b993d5481a7a6c301cb",
}


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
        raise UpstreamUnavailable("installed native source differs from its pinned upstream manifest")
