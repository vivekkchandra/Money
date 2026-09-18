"""Explicit dependency-metadata compatibility for one immutable AIHF revision.

This is not an advisory override: the resulting wheel must resolve, import,
pass its behavior checks and pass the ordinary unsuppressed audit. No upstream
application byte, build backend, package list or dependency is removed.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import stat
import tomllib
from pathlib import Path
from typing import Any

from money.adapters.native_attestation import SOURCE_DIGESTS, source_fingerprint
from money.qualification.core import QualificationContext, fingerprint

SCHEMA = "money-aihf-metadata-compatibility-v1"
_DEFINITION = "data/configuration/native-runtimes/aihf-compatibility.json"
_REPOSITORY = "https://github.com/virattt/ai-hedge-fund.git"
_REVISION = "fc1bf250ead209ae5f02c39c3d0062c4bb554505"
_ORIGINAL_SHA256 = "4658513fe53a6d1bfab3415d4d6cb6d984b70400e1ac9c30e3680a719dba4bb6"
_CHANGES = (
    ("langchain-anthropic", "0.3.5", ">=1.4.6,<2"),
    ("langchain-openai", "^0.3.5", ">=1.1.14,<2"),
    ("langchain-deepseek", "^0.1.2", ">=1,<2"),
    ("langchain-xai", "^0.2.5", ">=1,<2"),
    ("python-dotenv", "1.0.0", ">=1.2.2,<2"),
)
_ADVISORIES = (
    "GHSA-gr75-jv2w-4656", "GHSA-qh6h-p6c9-ff54", "GHSA-2g6r-c272-w58r",
    "GHSA-r7w7-9xr2-qq2r", "GHSA-mf9w-mj56-hr94",
)


def _safe_path(path: Path) -> None:
    if any(part.is_symlink() for part in (*path.parents, path)):
        raise ValueError("NATIVE_COMPATIBILITY_PATH_UNSAFE")


def _validate_definition(value: Any) -> dict[str, Any]:
    expected: dict[str, Any] = {
        "schema_version": SCHEMA, "role": "ai_hedge_fund", "repository": _REPOSITORY,
        "revision": _REVISION, "target": "pyproject.toml", "section": "tool.poetry.dependencies",
        "original_sha256": _ORIGINAL_SHA256, "package": "hedge_fund",
        "source_sha256": SOURCE_DIGESTS["hedge_fund"], "advisories": list(_ADVISORIES),
        "changes": [{"name": name, "original": before, "replacement": after}
                    for name, before, after in _CHANGES],
    }
    if (not isinstance(value, dict) or set(value) != {*expected, "reason"}
            or any(value.get(key) != item for key, item in expected.items())
            or not isinstance(value.get("reason"), str) or not value["reason"].strip()):
        raise ValueError("NATIVE_COMPATIBILITY_DEFINITION_INVALID")
    return value


def load_compatibility(ctx: QualificationContext, role: str) -> dict[str, Any] | None:
    """Load only the explicit metadata patch for the unchanged AIHF source pin."""
    if role == "tradingagents":
        return None
    if role != "ai_hedge_fund":
        raise ValueError("NATIVE_COMPATIBILITY_ROLE_INVALID")
    path = ctx.repo / _DEFINITION
    _safe_path(path)
    if path.stat().st_size > 32_000:
        raise ValueError("NATIVE_COMPATIBILITY_DEFINITION_INVALID")
    raw = path.read_bytes()
    ctx.check_secrets(raw)
    value = _validate_definition(json.loads(raw))
    pins = {parts[0]: tuple(parts[1:]) for line in
            (ctx.repo / "UPSTREAM_LOCK.txt").read_text().splitlines()
            if len(parts := line.split()) == 3}
    if pins.get("ai-hedge-fund") != (_REPOSITORY, _REVISION):
        raise ValueError("NATIVE_COMPATIBILITY_SOURCE_PIN_MISMATCH")
    return {**value, "definition_sha256": hashlib.sha256(raw).hexdigest()}


def _patch_metadata(raw: bytes, definition: dict[str, Any]) -> bytes:
    if hashlib.sha256(raw).hexdigest() != definition["original_sha256"]:
        raise ValueError("NATIVE_COMPATIBILITY_ORIGINAL_METADATA_CHANGED")
    original = tomllib.loads(raw.decode())
    modified = raw
    for change in definition["changes"]:
        before = f'{change["name"]} = "{change["original"]}"\n'.encode()
        after = f'{change["name"]} = "{change["replacement"]}"\n'.encode()
        if modified.count(before) != 1:
            raise ValueError("NATIVE_COMPATIBILITY_DECLARATION_MISMATCH")
        modified = modified.replace(before, after, 1)
    parsed = tomllib.loads(modified.decode())
    # After restoring those exact five values, every parsed field must match.
    for change in definition["changes"]:
        dependencies = parsed["tool"]["poetry"]["dependencies"]
        if dependencies.get(change["name"]) != change["replacement"]:
            raise ValueError("NATIVE_COMPATIBILITY_NON_DEPENDENCY_CHANGE")
        dependencies[change["name"]] = change["original"]
    if parsed != original:
        raise ValueError("NATIVE_COMPATIBILITY_NON_DEPENDENCY_CHANGE")
    return modified


def _tree_manifest(root: Path) -> dict[str, tuple[str, str, int]]:
    """Include all files and modes, not merely the Python package fingerprint."""
    _safe_path(root)
    if not root.is_dir():
        raise ValueError("NATIVE_COMPATIBILITY_SOURCE_REQUIRED")
    result: dict[str, tuple[str, str, int]] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        name = path.relative_to(root).as_posix()
        if len(result) >= 30_000 or stat.S_ISLNK(info.st_mode):
            raise ValueError("NATIVE_COMPATIBILITY_PATH_UNSAFE")
        if stat.S_ISDIR(info.st_mode):
            result[name] = ("directory", "", stat.S_IMODE(info.st_mode))
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > 100_000_000:
            raise ValueError("NATIVE_COMPATIBILITY_PATH_UNSAFE")
        total += info.st_size
        if total > 100_000_000:
            raise ValueError("NATIVE_COMPATIBILITY_SOURCE_OVERSIZED")
        raw = path.read_bytes()
        after = path.lstat()
        if any(getattr(after, key) != getattr(info, key) for key in (
            "st_ino", "st_dev", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode",
        )):
            raise ValueError("NATIVE_COMPATIBILITY_SOURCE_CHANGED_DURING_READ")
        result[name] = ("file", hashlib.sha256(raw).hexdigest(), stat.S_IMODE(info.st_mode))
    return result


def prepare_compatibility_source(
    ctx: QualificationContext, source: Path, work: Path, definition: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    """Stage and attest a dependency-only copy without editing the original tree.

    Cached copies must match byte-for-byte except the explicit pyproject patch.
    Unknown build outputs are rejected, not silently carried into a new wheel.
    Security qualification is deliberately not granted by this receipt.
    """
    current = load_compatibility(ctx, "ai_hedge_fund")
    if current != definition:
        raise ValueError("NATIVE_COMPATIBILITY_DEFINITION_CHANGED")
    target = work / "compatibility-source"
    _safe_path(work)
    _safe_path(source)
    _safe_path(target)
    if (target == source or target.is_relative_to(source) or source.is_relative_to(target)):
        raise ValueError("NATIVE_COMPATIBILITY_PATH_UNSAFE")
    original_manifest = _tree_manifest(source)
    original = (source / "pyproject.toml").read_bytes()
    patched = _patch_metadata(original, definition)
    if source_fingerprint(source / "hedge_fund", "hedge_fund") != SOURCE_DIGESTS["hedge_fund"]:
        raise ValueError("NATIVE_COMPATIBILITY_APPLICATION_SOURCE_CHANGED")
    ctx.check_secrets(patched)
    if not target.exists():
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(source, target)
        (target / "pyproject.toml").write_bytes(patched)
    expected = dict(original_manifest)
    kind, _, mode = expected["pyproject.toml"]
    expected["pyproject.toml"] = (kind, hashlib.sha256(patched).hexdigest(), mode)
    if _tree_manifest(target) != expected or _tree_manifest(source) != original_manifest:
        raise ValueError("NATIVE_COMPATIBILITY_STAGED_SOURCE_CHANGED")
    package_hash = source_fingerprint(target / "hedge_fund", "hedge_fund")
    if package_hash != SOURCE_DIGESTS["hedge_fund"]:
        raise ValueError("NATIVE_COMPATIBILITY_APPLICATION_SOURCE_CHANGED")
    patch = "".join(difflib.unified_diff(
        original.decode().splitlines(keepends=True), patched.decode().splitlines(keepends=True),
        fromfile="upstream/pyproject.toml", tofile="money-compatibility/pyproject.toml",
    )).encode()
    definition_bytes = (ctx.repo / _DEFINITION).read_bytes()
    if hashlib.sha256(definition_bytes).hexdigest() != definition["definition_sha256"]:
        raise ValueError("NATIVE_COMPATIBILITY_DEFINITION_CHANGED")
    artifacts = [ctx.artifact(value) for value in (definition_bytes, original, patched, patch)]
    return target, {
        "schema_version": SCHEMA, "scope": "DEPENDENCY_METADATA_ONLY",
        "upstream_repository": _REPOSITORY, "upstream_commit": _REVISION,
        "definition_sha256": definition["definition_sha256"],
        "original_pyproject_sha256": hashlib.sha256(original).hexdigest(),
        "patched_pyproject_sha256": hashlib.sha256(patched).hexdigest(),
        "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "original_tree_sha256": fingerprint(original_manifest),
        "patched_tree_sha256": fingerprint(expected),
        "source_sha256": package_hash, "application_source_unchanged": True,
        "reason": definition["reason"], "advisories": definition["advisories"],
        "changes": definition["changes"], "artifacts": artifacts,
        "security_qualified": False,
    }
