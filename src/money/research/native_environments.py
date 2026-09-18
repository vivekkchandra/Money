"""Reproducible, source-pinned native environments with unsuppressed audits.

These environments install each complete upstream dependency graph separately.
Only the explicitly declared Pydantic adapter dependency is added; the Money
application (and its unrelated CrewAI/NumPy dependency graph) is not installed.
No source or dependency override, advisory suppression, or credential inheritance
is used. Failed installation/audit receipts are evidence of failure, not approval.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import sys
import tarfile
import tomllib
from pathlib import Path
from typing import Any

from money.adapters.native_attestation import SOURCE_DIGESTS, source_fingerprint
from money.adapters.native_subprocess import bridge_fingerprint, installed_files_fingerprint
from money.qualification.core import (
    CommandResult,
    QualificationContext,
    fingerprint,
    json_bytes,
    run_captured,
)
from money.qualification.native import _audit_result
from money.schemas.contracts import content_hash

SCHEMA = "money-isolated-native-runtimes-v1"
_DEFINITION = "data/configuration/native-runtimes/engines.json"
_ROLES = ("tradingagents", "ai_hedge_fund")
_PINNED = {
    "tradingagents": (
        "https://github.com/TauricResearch/TradingAgents.git",
        "be952b8eccb49720509af544c6675233bc1f10d0", "tradingagents", ".venv-tradingagents",
    ),
    "ai_hedge_fund": (
        "https://github.com/virattt/ai-hedge-fund.git",
        "fc1bf250ead209ae5f02c39c3d0062c4bb554505", "hedge_fund", ".venv-ai-hedge-fund",
    ),
}
_ENGINE_DETAILS = {
    "tradingagents": {
        "source_directory": "upstreams/tradingagents", "distribution": "tradingagents",
        "version": "0.4.0", "imports": ["tradingagents.agents",
            "tradingagents.graph.propagation", "langchain_core.runnables"],
    },
    "ai_hedge_fund": {
        "source_directory": "upstreams/ai-hedge-fund", "distribution": "aihf",
        "version": "2.2.0", "imports": ["hedge_fund.signals.buffett", "hedge_fund.signals.lynch"],
    },
}


def _environment(ctx: QualificationContext) -> dict[str, str]:
    """Build tools receive no application keys, user pip config or Git helpers."""
    result = {key: value for key, value in ctx.environ.items()
              if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}}
    result.update({
        "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PIP_CONFIG_FILE": "/dev/null",
        "UV_NO_CONFIG": "1", "UV_CACHE_DIR": str(ctx.root / "state/native/cache"),
        "UV_PYTHON_INSTALL_DIR": str(ctx.root / "state/native/python"),
        "UV_TOOL_DIR": str(ctx.root / "state/native/tools"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0", "SOURCE_DATE_EPOCH": "315532800",
        "TMPDIR": "/private/tmp" if platform.system() == "Darwin" else "/tmp",
        "UV_HTTP_TIMEOUT": "30", "UV_HTTP_RETRIES": "1",
    })
    return result


def _command(ctx: QualificationContext, args: list[str], *, timeout: int = 300,
             maximum: int = 2_000_000) -> CommandResult:
    return run_captured(args, cwd=ctx.repo, environ=_environment(ctx), timeout=timeout,
                        maximum_output=maximum)


def _require(ctx: QualificationContext, args: list[str], code: str,
             *, timeout: int = 300) -> bytes:
    result = _command(ctx, args, timeout=timeout)
    if result.returncode:
        # Raw installer output can include URLs/credentials from user metadata.
        # It is neither persisted nor propagated through an exception.
        lower = result.output.lower()
        if code in {"NATIVE_DEPENDENCY_RESOLUTION_FAILED", "NATIVE_DEPENDENCY_INSTALL_FAILED",
                    "NATIVE_SOURCE_BUILD_FAILED"}:
            if any(term in lower for term in (b"dns error", b"failed to lookup", b"name resolution",
                    b"nodename nor servname", b"could not resolve", b"network is unreachable")):
                code = "NATIVE_PACKAGE_INDEX_UNREACHABLE"
            elif any(term in lower for term in (b"401 unauthorized", b"403 forbidden")):
                code = "NATIVE_PACKAGE_INDEX_ACCESS_DENIED"
            elif b"no solution found" in lower or b"unsatisfiable" in lower:
                code = "NATIVE_DEPENDENCY_CONFLICT"
        raise ValueError(code)
    return result.output


def _safe_directory(path: Path) -> None:
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink():
            raise ValueError("NATIVE_ENVIRONMENT_PATH_UNSAFE")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def _no_symlinks(path: Path) -> None:
    if path.is_symlink() or any(item.is_symlink() for item in path.rglob("*")):
        raise ValueError("NATIVE_ENVIRONMENT_PATH_UNSAFE")


def _write(ctx: QualificationContext, path: Path, raw: bytes) -> None:
    ctx.check_secrets(raw)
    _safe_directory(path.parent)
    if path.is_symlink():
        raise ValueError("NATIVE_ENVIRONMENT_PATH_UNSAFE")
    path.write_bytes(raw)


def _definitions(ctx: QualificationContext) -> dict[str, Any]:
    raw = (ctx.repo / _DEFINITION).read_bytes()
    ctx.check_secrets(raw)
    value = json.loads(raw)
    if (value.get("schema_version") != SCHEMA or value.get("python_version") != "3.12.14"
            or value.get("adapter_dependencies") != ["pydantic>=2.12.5"]
            or value.get("audit_tool") != "pip-audit==2.10.1"
            or set(value.get("engines", {})) != set(_ROLES)):
        raise ValueError("NATIVE_RUNTIME_DEFINITION_INVALID")
    pins = {parts[0]: tuple(parts[1:]) for line in
            (ctx.repo / "UPSTREAM_LOCK.txt").read_text().splitlines()
            if len(parts := line.split()) == 3}
    for role, expected in _PINNED.items():
        engine = value["engines"][role]
        actual = tuple(engine[key] for key in
                       ("repository", "revision", "package", "environment_directory"))
        if actual != expected or pins.get(role.replace("_", "-")) != expected[:2]:
            raise ValueError("NATIVE_RUNTIME_SOURCE_PIN_MISMATCH")
        if (set(engine) != {"repository", "revision", "package", "environment_directory",
                           *_ENGINE_DETAILS[role]}
                or any(engine.get(key) != expected_value
                       for key, expected_value in _ENGINE_DETAILS[role].items())):
            raise ValueError("NATIVE_RUNTIME_ENTRYPOINT_DEFINITION_INVALID")
    return value


def _source(ctx: QualificationContext, role: str, spec: dict[str, Any], work: Path) -> Path:
    checkout = ctx.repo / spec["source_directory"]
    if not (checkout / ".git").exists():
        checkout = work / "repository"
        if not (checkout / ".git").exists():
            _require(ctx, ["git", "clone", "--filter=blob:none", "--no-checkout",
                           spec["repository"], str(checkout)], "NATIVE_SOURCE_FETCH_FAILED")
            _require(ctx, ["git", "-C", str(checkout), "fetch", "--depth=1", "origin",
                           spec["revision"]], "NATIVE_SOURCE_FETCH_FAILED")
    remote = _require(ctx, ["git", "-C", str(checkout), "remote", "get-url", "origin"],
                      "NATIVE_SOURCE_REPOSITORY_UNVERIFIED").decode().strip().splitlines()[-1]
    revision = _require(ctx, ["git", "-C", str(checkout), "rev-parse",
                             spec["revision"] + "^{commit}"],
                        "NATIVE_SOURCE_REVISION_UNAVAILABLE").decode().strip().splitlines()[-1]
    if remote.removesuffix(".git") != spec["repository"].removesuffix(".git") or revision != spec["revision"]:
        raise ValueError("NATIVE_SOURCE_REPOSITORY_MISMATCH")
    source = work / "source"
    archive_path = work / "source.tar"
    if archive_path.is_symlink():
        raise ValueError("NATIVE_SOURCE_ARCHIVE_INVALID")
    _require(ctx, ["git", "-C", str(checkout), "archive", "--format=tar",
                   "--output", str(archive_path), revision], "NATIVE_SOURCE_ARCHIVE_FAILED")
    if archive_path.stat().st_size > 100_000_000:
        raise ValueError("NATIVE_SOURCE_ARCHIVE_INVALID")
    existing = source.exists()
    _safe_directory(source)
    _no_symlinks(source)
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        if len(members) > 30_000:
            raise ValueError("NATIVE_SOURCE_ARCHIVE_INVALID")
        for member in members:
            if (member.name.startswith("/") or ".." in Path(member.name).parts
                    or not (member.isfile() or member.isdir())):
                raise ValueError("NATIVE_SOURCE_ARCHIVE_INVALID")
        if existing:
            original_names = {member.name for member in members}
            for member in members:
                target = source / member.name
                if target.is_symlink():
                    raise ValueError("NATIVE_STAGED_SOURCE_CHANGED")
                if member.isfile():
                    stream = archive.extractfile(member)
                    if stream is None or not target.is_file() or target.read_bytes() != stream.read():
                        raise ValueError("NATIVE_STAGED_SOURCE_CHANGED")
            for build_file in ("setup.py", "setup.cfg", "pyproject.toml"):
                if (source / build_file).exists() and build_file not in original_names:
                    raise ValueError("NATIVE_STAGED_SOURCE_CHANGED")
        else:
            archive.extractall(source, members=members, filter="data")
    if source_fingerprint(source / spec["package"], spec["package"]) != SOURCE_DIGESTS[spec["package"]]:
        raise ValueError("NATIVE_SOURCE_DIGEST_MISMATCH")
    if not (source / "LICENSE").is_file():
        raise ValueError("NATIVE_SOURCE_LICENSE_MISSING")
    return source


def _python(ctx: QualificationContext, version: str) -> str:
    if platform.python_version() == version:
        return sys.executable
    found = _command(ctx, ["uv", "python", "find", version])
    if found.returncode:
        _require(ctx, ["uv", "python", "install", version], "NATIVE_PYTHON_INSTALL_FAILED",
                 timeout=600)
        found = _command(ctx, ["uv", "python", "find", version])
    if found.returncode:
        raise ValueError("NATIVE_PYTHON_REQUIRED")
    path = found.output.decode().strip().splitlines()[-1]
    checked = _require(ctx, [path, "-I", "-c", "import platform; print(platform.python_version())"],
                       "NATIVE_PYTHON_REQUIRED").decode().strip()
    if checked != version:
        raise ValueError("NATIVE_PYTHON_VERSION_MISMATCH")
    return path


def _venv(ctx: QualificationContext, python: str, target: Path) -> str:
    _safe_directory(target.parent)
    if target.is_symlink():
        raise ValueError("NATIVE_ENVIRONMENT_PATH_UNSAFE")
    executable = target / "bin/python"
    if not executable.exists():
        _require(ctx, ["uv", "venv", "--python", python, str(target)],
                 "NATIVE_ENVIRONMENT_CREATION_FAILED")
    if ((target / "bin").is_symlink() or (target / "pyvenv.cfg").is_symlink()
            or executable.resolve() != Path(python).resolve()):
        raise ValueError("NATIVE_ENVIRONMENT_INTERPRETER_MISMATCH")
    return str(executable)


def _lock(ctx: QualificationContext, requirements: Path, lock: Path, python: str) -> str:
    receipt = lock.with_suffix(".receipt.json")
    inputs_hash = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if lock.exists():
        if not receipt.is_file() or receipt.is_symlink():
            raise ValueError("NATIVE_LOCK_RECEIPT_REQUIRED")
        record = json.loads(receipt.read_bytes())
        if (record.get("inputs_sha256") != inputs_hash
                or ctx.verify_artifact(*record["artifact"]) != lock.read_bytes()):
            raise ValueError("NATIVE_LOCK_INTEGRITY_FAILED")
    if not lock.exists():
        _require(ctx, ["uv", "pip", "compile", str(requirements), "--python", python,
                       "--generate-hashes", "--no-header", "--no-annotate", "--output-file",
                       str(lock)], "NATIVE_DEPENDENCY_RESOLUTION_FAILED", timeout=1200)
        reference = ctx.artifact(lock.read_bytes())
        _write(ctx, receipt, json_bytes({"inputs_sha256": inputs_hash, "artifact": reference}))
    raw = lock.read_bytes()
    ctx.check_secrets(raw)
    if not raw or b"--hash=sha256:" not in raw or b"--trusted-host" in raw:
        raise ValueError("NATIVE_LOCK_INVALID")
    return hashlib.sha256(raw).hexdigest()


def _sync(ctx: QualificationContext, lock: Path, python: str, *, reinstall: bool = False) -> None:
    _require(ctx, ["uv", "pip", "sync", "--python", python, "--require-hashes",
                   *(["--reinstall"] if reinstall else []), str(lock)],
             "NATIVE_DEPENDENCY_INSTALL_FAILED", timeout=1200)


def _wheel(ctx: QualificationContext, source: Path, work: Path, python: str) -> tuple[Path, str]:
    build = tomllib.loads((source / "pyproject.toml").read_text())["build-system"]["requires"]
    requirements, lock = work / "build.in", work / "build.lock"
    _write(ctx, requirements, ("\n".join(build) + "\n").encode())
    build_python = _venv(ctx, python, work / "build-venv")
    build_hash = _lock(ctx, requirements, lock, build_python)
    wheels = list((work / "wheels").glob("*.whl"))
    wheel_receipt = work / "wheel.receipt.json"
    if wheels:
        if not wheel_receipt.is_file() or len(wheels) != 1:
            raise ValueError("NATIVE_WHEEL_RECEIPT_REQUIRED")
        record = json.loads(wheel_receipt.read_bytes())
        if (record.get("build_lock_sha256") != build_hash
                or record.get("sha256") != hashlib.sha256(wheels[0].read_bytes()).hexdigest()):
            raise ValueError("NATIVE_WHEEL_INTEGRITY_FAILED")
    if not wheels:
        _sync(ctx, lock, build_python)
        _require(ctx, ["uv", "build", "--wheel", "--python", build_python,
                       "--no-build-isolation", "--out-dir", str(work / "wheels"), str(source)],
                 "NATIVE_SOURCE_BUILD_FAILED", timeout=600)
        wheels = list((work / "wheels").glob("*.whl"))
        if len(wheels) == 1:
            _write(ctx, wheel_receipt, json_bytes({"build_lock_sha256": build_hash,
                   "sha256": hashlib.sha256(wheels[0].read_bytes()).hexdigest()}))
    if len(wheels) != 1 or wheels[0].is_symlink():
        raise ValueError("NATIVE_SOURCE_WHEEL_INVALID")
    return wheels[0], build_hash


def _json_command(ctx: QualificationContext, args: list[str], code: str) -> Any:
    raw = _require(ctx, args, code)
    ctx.check_secrets(raw)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError(code) from None


def _inspect(ctx: QualificationContext, python: str, spec: dict[str, Any]) -> dict[str, Any]:
    code = """import importlib, importlib.metadata, importlib.util, json, os, platform, sysconfig
package, modules = json.loads(__import__('sys').argv[1])
spec = importlib.util.find_spec(package)
saved = [os.dup(1), os.dup(2)]
sink = os.open(os.devnull, os.O_WRONLY)
failed = None
try:
    os.dup2(sink, 1)
    os.dup2(sink, 2)
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception:
            failed = module
            break
    __import__('sys').stdout.flush()
    __import__('sys').stderr.flush()
finally:
    for number, descriptor in enumerate(saved, 1):
        os.dup2(descriptor, number)
        os.close(descriptor)
    os.close(sink)
if failed:
    print(json.dumps({'error': 'NATIVE_IMPORT_SMOKE_FAILED', 'entrypoint': failed}))
    raise SystemExit(0)
print(json.dumps({'python_version': platform.python_version(), 'source_path': spec.origin,
'purelib': sysconfig.get_path('purelib'), 'inventory': sorted(
[(d.metadata['Name'], d.version) for d in importlib.metadata.distributions()])}))
"""
    result = _json_command(ctx, [python, "-I", "-B", "-c", code,
                                json.dumps([spec["package"], spec["imports"]])],
                           "NATIVE_IMPORT_SMOKE_FAILED")
    # Third-party import chatter has no diagnostic authority. The independent
    # pip-audit below retains every security finding without suppression.
    if result.get("error") == "NATIVE_IMPORT_SMOKE_FAILED":
        raise ValueError("NATIVE_IMPORT_SMOKE_FAILED")
    root = Path(result["source_path"]).parent
    if (root.parent.resolve() != Path(result["purelib"]).resolve()
            or source_fingerprint(root, spec["package"]) != SOURCE_DIGESTS[spec["package"]]):
        raise ValueError("NATIVE_INSTALLED_SOURCE_MISMATCH")
    if result["python_version"] != "3.12.14":
        raise ValueError("NATIVE_PYTHON_VERSION_MISMATCH")
    inventory = sorted((re.sub(r"[-_.]+", "-", name).lower(), version)
                       for name, version in result["inventory"])
    if (spec["distribution"], spec["version"]) not in inventory:
        raise ValueError("NATIVE_DISTRIBUTION_VERSION_MISMATCH")
    result["inventory"] = inventory
    return result


def _restore_source_files(ctx: QualificationContext, python: str, source: Path,
                          spec: dict[str, Any], environment: Path) -> None:
    """Upstream AIHF wheel omits tests; preserve exact attested package bytes too."""
    purelib = Path(_require(ctx, [python, "-I", "-c",
                      "import sysconfig; print(sysconfig.get_path('purelib'))"],
                           "NATIVE_ENVIRONMENT_INVALID").decode().strip())
    if not purelib.resolve().is_relative_to(environment.resolve()):
        raise ValueError("NATIVE_ENVIRONMENT_PATH_UNSAFE")
    target = purelib / spec["package"]
    _safe_directory(target)
    _no_symlinks(source / spec["package"])
    _no_symlinks(target)
    # No upstream implementation is rewritten. Include files the upstream wheel
    # intentionally excludes from packaging so Money's unchanged digest matches.
    shutil.copytree(source / spec["package"], target, dirs_exist_ok=True)
    license_path = environment / "share/licenses" / spec["package"] / "LICENSE"
    _write(ctx, license_path, (source / "LICENSE").read_bytes())


def _audit(ctx: QualificationContext, role: str, python: str,
           inventory: list[tuple[str, str]]) -> dict[str, Any]:
    requirements = "".join(f"{name}=={version}\n" for name, version in inventory).encode()
    reference = ctx.artifact(requirements)
    checked = _command(ctx, ["uv", "pip", "check", "--python", python], timeout=60)
    audited = _command(ctx, ["uv", "tool", "run", "--from", "pip-audit==2.10.1", "pip-audit",
        "--strict", "--no-deps", "--disable-pip", "--timeout", "15", "--requirement",
        str(ctx.root / reference[1]), "--format", "json", "--progress-spinner", "off"],
        timeout=600)
    start, end = audited.output.find(b"{"), audited.output.rfind(b"}")
    if start < 0 or end <= start:
        raise ValueError("NATIVE_SECURITY_AUDIT_UNAVAILABLE")
    raw = json.loads(audited.output[start:end + 1])
    ctx.check_secrets(json_bytes(raw))
    passed, findings = _audit_result(raw, inventory, checked.returncode, audited.returncode)
    # Retain fix versions, aliases/CVEs and descriptions in the genuine report;
    # no ignored advisories, skipped distributions or unverifiable coverage pass.
    audit_ref = ctx.artifact({"kind": "money-isolated-native-security-v1", "role": role,
        "verified_at": ctx.now.isoformat(), "inventory": inventory,
        "python": python, "dependency_check_exit": checked.returncode,
        "audit_exit": audited.returncode, "audit": raw, "passed": passed,
        "security_warnings_ignored": False})
    return {"passed": passed, "findings": findings,
            "dependency_closure_verified": checked.returncode == 0,
            "artifacts": [reference, audit_ref], "audit": raw}


def _fix_available(security: dict[str, Any]) -> bool:
    return any(vuln.get("fix_versions") for dependency in
               security.get("audit", {}).get("dependencies", [])
               for vuln in dependency.get("vulns", []))


def _remediate(ctx: QualificationContext, role: str, python: str, source: Path,
               spec: dict[str, Any], environment: Path, requirements: Path,
               lock: Path, old_security: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """One ordinary compatible upgrade solve, never override upstream constraints."""
    candidate = lock.with_name("runtime-remediation.lock")
    _require(ctx, ["uv", "pip", "compile", str(requirements), "--python", python,
                   "--generate-hashes", "--no-header", "--no-annotate", "--upgrade",
                   "--output-file", str(candidate)], "NATIVE_DEPENDENCY_RESOLUTION_FAILED",
             timeout=1200)
    candidate_bytes = candidate.read_bytes()
    ctx.check_secrets(candidate_bytes)
    if candidate_bytes == lock.read_bytes():
        raise ValueError("NATIVE_ADVISORY_FIX_INCOMPATIBLE_WITH_PINNED_DEPENDENCIES")
    candidate_ref = ctx.artifact(candidate_bytes)
    _sync(ctx, candidate, python)
    _restore_source_files(ctx, python, source, spec, environment)
    inspected = _inspect(ctx, python, spec)
    security = _audit(ctx, role, python, inspected["inventory"])
    security["previous_audit_artifacts"] = old_security["artifacts"]
    # Record the actual installed closure even if its audit still fails.
    _write(ctx, lock, candidate_bytes)
    _write(ctx, lock.with_suffix(".receipt.json"), json_bytes({
        "inputs_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
        "artifact": candidate_ref}))
    return hashlib.sha256(candidate_bytes).hexdigest(), inspected, security


def prepare_native_environments(ctx: QualificationContext, *, provision: bool = True) -> dict[str, Any]:
    """Install/resume exact native runtimes, then recheck each actual environment.

    Caller holds the qualification runner lock. A prior passing receipt does not
    bypass current source, inventory, imports or an unsuppressed security audit.
    This function returns fixed diagnostics; callers decide how to expose gates.
    """
    definitions = _definitions(ctx)
    runtimes: dict[str, Any] = {}
    artifacts: list[Any] = []
    for role in _ROLES:
        spec = definitions["engines"][role]
        environment = ctx.repo / spec["environment_directory"]
        result: dict[str, Any] = {"complete": False, "role": role,
            "environment_path": str(environment), "python": str(environment / "bin/python"),
            "python_version": definitions["python_version"], "adapter_entrypoints": spec["imports"], "source": {
                "repository": spec["repository"], "revision": spec["revision"],
                "package": spec["package"], "expected_sha256": SOURCE_DIGESTS[spec["package"]],
                "verified": False}, "security": {"passed": False, "findings": [], "artifacts": []},
            "errors": []}
        runtimes[role] = result
        try:
            definition_hash = fingerprint({"spec": spec, "python": definitions["python_version"],
                "adapter_dependencies": definitions["adapter_dependencies"],
                "platform": platform.system(), "machine": platform.machine()})
            work = ctx.root / "state/native" / role / definition_hash
            _safe_directory(work)
            python = _python(ctx, definitions["python_version"])
            if not provision and not Path(result["python"]).exists():
                raise ValueError("NATIVE_ENVIRONMENT_NOT_INSTALLED")
            executable = _venv(ctx, python, environment)
            baseline = work / "environment.receipt.json"
            if baseline.exists():
                baseline_raw = baseline.read_bytes()
                ctx.check_secrets(baseline_raw)
                reference = json.loads(baseline_raw)["artifact"]
                baseline_value = json.loads(ctx.verify_artifact(*reference))
                if installed_files_fingerprint(environment) != baseline_value["environment_sha256"]:
                    raise ValueError("NATIVE_INSTALLED_ENVIRONMENT_CHANGED")
            previous = ctx.read_json(f"outputs/native-environments/{role}.json")
            if isinstance(previous, dict) and previous.get("importability") == "VERIFIED":
                if (previous.get("environment_sha256") is not None
                        and installed_files_fingerprint(environment) != previous["environment_sha256"]):
                    raise ValueError("NATIVE_INSTALLED_ENVIRONMENT_CHANGED")
                _inspect(ctx, executable, spec)  # changed installed code is never silently repaired
            if not provision and (not (work / "source").exists()
                    or not (work / "runtime.lock").exists()
                    or not list((work / "wheels").glob("*.whl"))):
                raise ValueError("NATIVE_ENVIRONMENT_NOT_INSTALLED")
            source = _source(ctx, role, spec, work)
            result["source"]["archive_verified"] = True
            result["source"]["archive_sha256"] = hashlib.sha256((work / "source.tar").read_bytes()).hexdigest()
            wheel, build_hash = _wheel(ctx, source, work, python)
            requirements, lock = work / "runtime.in", work / "runtime.lock"
            _write(ctx, requirements, (str(wheel) + "\n" +
                "\n".join(definitions["adapter_dependencies"]) + "\n").encode())
            lock_hash = _lock(ctx, requirements, lock, executable)
            if provision:
                _sync(ctx, lock, executable, reinstall=not baseline.exists())
                _restore_source_files(ctx, executable, source, spec, environment)
            inspected = _inspect(ctx, executable, spec)
            result.update({"source": {**result["source"], "verified": True,
                "installed_path": inspected["source_path"], "sha256": SOURCE_DIGESTS[spec["package"]]},
                "lock_path": str(lock), "lock_sha256": lock_hash,
                "build_lock_sha256": build_hash, "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "license_sha256": hashlib.sha256((source / "LICENSE").read_bytes()).hexdigest(),
                "inventory": inspected["inventory"], "inventory_sha256": content_hash(inspected["inventory"]),
                "definition_sha256": definition_hash, "importability": "VERIFIED"})
            result["bridge_sha256"] = bridge_fingerprint(ctx.repo)
            # Keep the installed-byte baseline separate from mutable latest status.
            # A failed retry must not erase the authority used to detect tampering
            # on the following retry. This is integrity evidence, not audit PASS.
            result["environment_sha256"] = installed_files_fingerprint(environment)
            baseline_ref = ctx.artifact({"environment_sha256": result["environment_sha256"],
                "inventory_sha256": result["inventory_sha256"], "lock_sha256": lock_hash})
            _write(ctx, baseline, json_bytes({"artifact": baseline_ref}))
            security = _audit(ctx, role, executable, inspected["inventory"])
            if provision and not security["passed"] and _fix_available(security):
                result["remediation_attempted"] = True
                result["security"] = security
                artifacts.extend(security["artifacts"])
                lock_hash, inspected, security = _remediate(ctx, role, executable, source, spec,
                    environment, requirements, lock, security)
                result.update({"lock_sha256": lock_hash, "inventory": inspected["inventory"],
                               "inventory_sha256": content_hash(inspected["inventory"])})
            result["security"] = security
            artifacts.extend(security["artifacts"])
            result["environment_sha256"] = installed_files_fingerprint(environment)
            baseline_ref = ctx.artifact({"environment_sha256": result["environment_sha256"],
                "inventory_sha256": result["inventory_sha256"], "lock_sha256": lock_hash})
            _write(ctx, baseline, json_bytes({"artifact": baseline_ref}))
            result["complete"] = security["passed"] is True
            if not result["complete"]:
                result["errors"].append("LOCAL_NATIVE_DEPENDENCY_SECURITY_REQUIRED")
        except Exception as error:
            code = str(error) if isinstance(error, ValueError) else "NATIVE_ENVIRONMENT_UNAVAILABLE"
            if not re.fullmatch(r"(?:NATIVE|QUALIFICATION)_[A-Z_]+", code):
                code = "NATIVE_ENVIRONMENT_UNAVAILABLE"
            result["errors"].append(code)
        result["execution_identity"] = fingerprint({key: value for key, value in result.items()
                                                     if key not in {"security", "errors", "complete"}})
        result["verified_at"] = ctx.now.isoformat()
        ctx.write_json(f"outputs/native-environments/{role}.json", result)
    pins = {entry["source"]["package"]: entry["source"].get("sha256")
            for entry in runtimes.values()}
    complete = all(entry["complete"] for entry in runtimes.values())
    result = {"schema_version": SCHEMA, "scope": "LOCAL_RESEARCH_TESTING",
        "complete": complete, "runtimes": runtimes, "pins": pins, "artifacts": artifacts,
        "security": {"passed": complete, "findings": [
            {"role": role, **finding} for role, entry in runtimes.items()
            for finding in entry["security"]["findings"]], "artifacts": artifacts},
        "execution_identity": fingerprint({role: entry["execution_identity"]
                                             for role, entry in runtimes.items()}),
        "qlib_enabled": False, "production_qualified": False, "hosted_egress_qualified": False}
    ctx.write_json("outputs/native-environments/result.json", result)
    return result
