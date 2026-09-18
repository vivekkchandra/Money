"""Offline native blocker diagnosis; never runtime or security qualification.

Only the diagnosis projection is written. Receipts retain their original age,
artifact hashes are checked, and no import, install, network request or review
approval is performed. Public advisory links identify the recorded findings;
following a link is not a fresh whole-environment vulnerability scan.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import tomllib
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from money.adapters.native_attestation import SOURCE_DIGESTS, source_fingerprint
from money.qualification.core import QualificationContext
from money.research.live import read_qualification_bytes
from money.research.qlib_mode import qlib_enabled

_ROOTS = {
    "tradingagents": "upstreams/tradingagents/tradingagents",
    "hedge_fund": "upstreams/ai-hedge-fund/hedge_fund",
    "crewai": "upstreams/crewai/lib/crewai/src/crewai",
    "qlib": "upstreams/qlib/qlib",
}
_METADATA = {
    "money": "pyproject.toml",
    "tradingagents": "upstreams/tradingagents/pyproject.toml",
    "hedge_fund": "upstreams/ai-hedge-fund/pyproject.toml",
    "crewai": "upstreams/crewai/lib/crewai/pyproject.toml",
}
# Each pair is independently disjoint. Emit it only when the actual checked-out
# metadata still contains these exact constraints, not based on old prose.
_CONFLICTS = (
    ("python-dotenv", "hedge_fund", "1.0.0", "money", ">=1.2.3"),
    ("numpy", "hedge_fund", "^1.24.0", "money", ">=2.5.3"),
    ("pandas", "hedge_fund", "^2.1.0", "money", ">=3.0.5"),
    ("langchain-anthropic", "hedge_fund", "0.3.5", "tradingagents", ">=0.3.15"),
    ("langchain-google-genai", "hedge_fund", "^2.0.11", "tradingagents", ">=4.0.0"),
)


def _saved(ctx: QualificationContext, relative: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read a bounded saved projection, without reflecting arbitrary input text."""
    summary: dict[str, Any] = {"path": relative, "status": "ABSENT"}
    try:
        raw = ctx.read_bytes(relative)
        if raw is None:
            return {}, summary
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("DIAGNOSIS_INPUT_INVALID")
        summary.update(status="RECORDED", sha256=hashlib.sha256(raw).hexdigest())
        timestamp = value.get("observed_at", value.get("verified_at"))
        if isinstance(timestamp, str):
            observed = datetime.fromisoformat(timestamp)
            if observed.tzinfo is None or observed.utcoffset() is None:
                raise ValueError("DIAGNOSIS_TIMESTAMP_INVALID")
            age = (ctx.now - observed).total_seconds()
            summary.update(
                observed_at=observed.isoformat(),
                age_seconds=age,
                within_one_hour_window=0 <= age < 3600,
            )
        return value, summary
    except (ValueError, TypeError, OSError):
        return {}, {"path": relative, "status": "INVALID_OR_UNSAFE"}


def _references(ctx: QualificationContext, values: Any) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(values, list) or not 1 <= len(values) <= 12:
        return "ABSENT_OR_INVALID", []
    documents: list[dict[str, Any]] = []
    try:
        for reference in values:
            if (
                not isinstance(reference, (list, tuple))
                or len(reference) != 2
                or not all(isinstance(part, str) for part in reference)
                or not reference[1].startswith("artifacts/")
            ):
                raise ValueError("DIAGNOSIS_REFERENCE_INVALID")
            raw = ctx.verify_artifact(*reference)
            if reference[1].endswith(".json"):
                value = json.loads(raw)
                if isinstance(value, dict):
                    documents.append(value)
        return "VERIFIED", documents
    except (ValueError, TypeError, OSError):
        return "INVALID_OR_UNSAFE", []


def _metadata(ctx: QualificationContext) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    dependencies: dict[str, dict[str, str]] = {}
    records = []
    for package, relative in _METADATA.items():
        try:
            raw = read_qualification_bytes(ctx.repo, relative, 2_000_000)
            ctx.check_secrets(raw)
            value = tomllib.loads(raw.decode())
            requirements = {}
            if "project" in value:
                for item in value["project"].get("dependencies", []):
                    match = re.fullmatch(r"([A-Za-z0-9_.-]+)([^;]*)", item)
                    if match:
                        requirements[match[1].lower().replace("_", "-")] = match[2]
            else:
                requirements = {
                    name: constraint
                    for name, constraint in value["tool"]["poetry"]["dependencies"].items()
                    if isinstance(constraint, str)
                }
            dependencies[package] = requirements
            records.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
        except (ValueError, TypeError, KeyError, OSError):
            records.append({"path": relative, "status": "UNAVAILABLE_OR_INVALID"})
    return dependencies, records


def _sources(ctx: QualificationContext, enabled: bool) -> dict[str, Any]:
    records = {}
    for package, relative in _ROOTS.items():
        if package == "qlib" and not enabled:
            records[package] = {"status": "DISABLED_EXPLICITLY", "required": False}
            continue
        expected = SOURCE_DIGESTS[package]
        checkout_digest = None
        installed_digest = None
        try:
            root = ctx.repo / relative
            if root.resolve() == root.absolute() and root.is_dir():
                checkout_digest = source_fingerprint(root, package)
        except (ValueError, OSError, RuntimeError):
            pass
        try:
            # Top-level package discovery does not execute the native package.
            spec = find_spec(package)
            if spec is not None and spec.origin is not None:
                installed_digest = source_fingerprint(Path(spec.origin).parent, package)
        except (ImportError, ValueError, OSError, RuntimeError):
            pass
        records[package] = {
            "required": True,
            "source_path": relative,
            "expected_sha256": expected,
            "checkout_sha256": checkout_digest,
            "installed_sha256": installed_digest,
            "checkout_matches": checkout_digest == expected,
            "installed_matches": installed_digest == expected,
        }
    return records


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _findings(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for document in documents:
        if document.get("kind") != "money-native-security-v1":
            continue
        audit = document.get("audit", {})
        if not isinstance(audit, dict) or not isinstance(audit.get("dependencies"), list):
            continue
        for dependency in audit["dependencies"]:
            if not isinstance(dependency, dict) or not dependency.get("vulns"):
                continue
            name, version = dependency.get("name"), dependency.get("version")
            if not all(
                isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_.+-]{1,100}", item)
                for item in (name, version)
            ):
                continue
            vulns = dependency["vulns"]
            if not isinstance(vulns, list):
                continue
            identifiers = sorted(
                {
                    value
                    for vuln in vulns
                    if isinstance(vuln, dict)
                    for value in [vuln.get("id"), *_list(vuln.get("aliases"))]
                    if isinstance(value, str)
                    and re.fullmatch(r"(?:PYSEC|CVE|GHSA)-[A-Za-z0-9-]{4,80}", value)
                }
            )
            findings.append(
                {
                    "name": name,
                    "version": version,
                    "recorded_entries": len(vulns),
                    "identifiers": identifiers,
                    "primary_advisories": [
                        "https://github.com/advisories/" + value
                        for value in identifiers
                        if value.startswith("GHSA-")
                    ],
                    "fix_versions_in_recorded_audit": sorted(
                        {
                            version
                            for vuln in vulns
                            if isinstance(vuln, dict)
                            for version in _list(vuln.get("fix_versions"))
                            if isinstance(version, str)
                            and re.fullmatch(r"[A-Za-z0-9_.+-]{1,100}", version)
                        }
                    ),
                }
            )
    return findings


def write_native_diagnosis(ctx: QualificationContext) -> dict[str, Any]:
    """Write secret-free diagnosis from real local inputs, never a PASS artifact."""
    enabled = qlib_enabled(ctx.environ)
    closure, closure_ref = _saved(ctx, "outputs/native-dependency-resolution.json")
    preflight, preflight_ref = _saved(ctx, "outputs/native-preflight.json")
    inference, inference_ref = _saved(ctx, "outputs/inference.json")
    lean, lean_ref = _saved(ctx, "reviews/lean-inputs.json")
    closure_integrity, _ = _references(ctx, closure.get("artifacts"))
    security = preflight.get("security", {})
    security_integrity, security_documents = _references(
        ctx, security.get("artifacts") if isinstance(security, dict) else None
    )
    requirements, metadata_refs = _metadata(ctx)
    conflicts = [
        {
            "package": package,
            "left": {"source": left, "constraint": first},
            "right": {"source": right, "constraint": second},
            "intersection": "EMPTY",
        }
        for package, left, first, right, second in _CONFLICTS
        if requirements.get(left, {}).get(package) == first
        and requirements.get(right, {}).get(package) == second
    ]
    packages: dict[str, str | None] = {}
    for name in (
        "langchain-core",
        "langgraph",
        "crewai",
        "crewai-core",
        "crewai-cli",
        "chromadb",
        "numpy",
        "pandas",
        "python-dotenv",
    ):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    result: dict[str, Any] = {
        "kind": "money-native-blocker-diagnosis-v1",
        "diagnosed_at": ctx.now.isoformat(),
        "scope": "LOCAL_DIAGNOSIS_ONLY",
        "native_runtime_qualified": False,
        "production_qualified": False,
        "fresh_security_audit_performed": False,
        "reviews_modified": False,
        "installs_performed": False,
        "qlib_enabled": enabled,
        "lean_required": True,
        "observed_environment": {
            "platform": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "packages": packages,
        },
        "sources": _sources(ctx, enabled),
        "dependency_resolution": {
            "evidence": closure_ref,
            "artifact_integrity": closure_integrity,
            "recorded_resolved": closure.get("resolved") is True,
            "recorded_qlib_enabled": closure.get("qlib_enabled"),
            "mode_matches": closure.get("qlib_enabled") is enabled,
            "proven_metadata_conflicts": conflicts,
            "metadata_evidence": metadata_refs,
            "action": "Resolve every pinned distribution conflict through reviewed packaging/source work; do not ignore dependencies or change pins solely to pass.",
        },
        "security": {
            "evidence": preflight_ref,
            "artifact_integrity": security_integrity,
            "recorded_findings": _findings(security_documents),
            "chroma_pinned_requirement": requirements.get("crewai", {}).get("chromadb"),
            "action": "Obtain a pinned compatible closure and a fresh, complete, unsuppressed audit; capability guards do not remediate advisories.",
        },
        "inference": {
            "evidence": inference_ref,
            "recorded_access_verified": inference.get("access_verified") is True,
            "recorded_hosted_compatible": inference.get("hosted_compatible") is True,
            "action": "Local Ollama proves local functionality only; independently review and qualify a remotely reachable HTTPS inference endpoint for hosted use.",
        },
        "target_worker": {
            "linux_observed": platform.system() == "Linux",
            "tools_available": {
                name: shutil.which(name) is not None for name in ("docker", "dotnet", "nft")
            },
            "container_runtime_tested": False,
            "egress_qualified": False,
            "action": "Build the exact pinned Linux native closure, retain a private worker in the existing project, and verify OS-enforced egress on the actual worker namespace. Railway run on a Mac is not a deployed-worker probe.",
            "inspector_requirement": "Current implementation inspects nftables only; prove readable policy with worker network/raw/admin capabilities denied, or implement and review a platform-specific trusted inspector. Private networking alone is insufficient.",
        },
        "lean": {
            "required": True,
            "evidence": lean_ref,
            "unresolved_review_fields": [
                name
                for name in (
                    "container",
                    "costs",
                    "adjustment_policy",
                    "proposed_by",
                    "approved_by",
                    "approved_at",
                )
                if lean.get(name) is None
            ],
            "action": "Qualify the pinned LEAN image/runtime and reviewed costs, historical eligibility, survivorship, corporate actions, walk-forward/OOS, MAE/MFE and drawdown evidence. Qlib disabled does not waive LEAN.",
        },
        "documentation": "docs/native-blockers.md",
    }
    ctx.write_json("outputs/native-blocker-diagnosis.json", result)
    return result
