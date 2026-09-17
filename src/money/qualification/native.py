"""Credential-minimal live inference and native-runtime qualification stages.

An API receipt is not native qualification. Installed source, a complete security
audit, independently reviewed target OS enforcement and actual frozen-snapshot
execution are separate gates. This module never installs incompatible packages,
ignores advisories, creates a synthetic snapshot or promotes an operator assertion
that a Python audit hook is an operating-system sandbox.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sys
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, TypeAdapter

from money.adapters.eligibility import eligibility_failures
from money.adapters.native import NativeRunSettings
from money.adapters.native_attestation import (
    SOURCE_DIGESTS,
    require_pinned_source,
    source_fingerprint,
)
from money.adapters.native_process import BoundedNativeRunner, NativeProcessPolicy
from money.adapters.native_qlib import QlibNativeRunner, QualifiedLinearModel, load_qualified_model
from money.adapters.native_qualitative import AIHedgeFundNativeRunner, TradingAgentsNativeRunner
from money.adapters.upstream import (
    AIHedgeFundAdapter,
    QlibAdapter,
    TradingAgentsAdapter,
    _validate_report,
)
from money.crews.cio import CIOResult, CrewAICioAdapter, CrewAINativeRunner
from money.data.security import public_addresses
from money.models.registry import ModelRegistry
from money.qualification.core import CommandResult, run_captured
from money.research.budgets import BudgetLimits
from money.research.inference import HTTPInference
from money.research.inference_config import InferenceSelection, load_inference_selections
from money.research.inference_probe import InferenceProbeEvidence, probe_selection, safe_probe_error
from money.research.live import validate_invocation_budgets
from money.research.qlib_mode import qlib_enabled
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    Contract,
    FirmReport,
    LeanValidationReport,
    QlibQuantResearchReport,
    ResearchMandate,
    ResearchSnapshot,
    TradingAgentsResearchReport,
    content_hash,
)
from money.storage import ResearchStore

if TYPE_CHECKING:
    from money.qualification.core import QualificationContext

FIRMS = ("tradingagents", "ai_hedge_fund", "crewai")
SHA = r"^[a-f0-9]{64}$"


class InferenceReview(Contract):
    reviewed_by: str = Field(min_length=3, max_length=200)
    reviewed_at: AwareDatetime
    expires_at: AwareDatetime
    configuration_sha256: str = Field(pattern=SHA)
    budgets: BudgetLimits
    native_timeout_seconds: int = Field(ge=30, le=1200)
    native_max_calls: int = Field(ge=4, le=32)
    enable_native_cross_examination: bool = False
    cross_examination_maximum_challenges: int = Field(default=8, ge=1, le=24)
    # Rates are deliberately optional, never invented from USD prices or unknown FX.
    rates: dict[str, tuple[str, str]] = Field(default_factory=dict)
    rates_evidence: tuple[str, str] | None = None


class EgressReview(Contract):
    """Review of raw enforcement evidence generated on the actual execution host."""

    collected_by: str = Field(min_length=3, max_length=200)
    reviewed_by: str = Field(min_length=3, max_length=200)
    reviewed_at: AwareDatetime
    expires_at: AwareDatetime
    execution_identity: str = Field(pattern=SHA)
    target_identity: str = Field(min_length=3, max_length=200)
    enforcement: Literal["linux-nftables", "linux-iptables", "container-network-policy"]
    approved_hosts: tuple[str, ...] = Field(min_length=1)
    policy: tuple[str, str]
    native_probes: tuple[str, str]
    # The artifacts are independently reviewed evidence, not unsigned PASS flags.
    review_evidence: tuple[str, str]
    policy_scope: Literal["default-deny-all-routes-ipv4-ipv6-udp-dns"]


def _fresh(reviewed: datetime, expires: datetime, now: datetime) -> bool:
    return reviewed <= now < expires and now - reviewed <= timedelta(days=7)


def _reference(ctx: QualificationContext, reference: tuple[str, str]) -> bytes:
    digest, relative = reference
    raw = ctx.read_bytes(relative)
    if raw is None or not re.fullmatch(SHA, digest) or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("NATIVE_REVIEW_EVIDENCE_INVALID")
    return raw


def _selections(ctx: QualificationContext) -> dict[str, InferenceSelection]:
    result = load_inference_selections(ctx.repo, ctx.environ)
    for selection in result.values():
        if selection.provider == "openai" and (
            selection.endpoint != "https://api.openai.com/v1/chat/completions"
            or selection.protocol != "openai-compatible"
            or selection.credential_environment_variable != "OPENAI_API_KEY"
            or selection.authentication != "bearer"
            or not re.fullmatch(r"[a-z0-9.-]+-\d{4}-\d{2}-\d{2}", selection.model)
        ):
            raise ValueError("INFERENCE_ENDPOINT_OR_EXACT_MODEL_UNREVIEWED")
        if not selection.is_local and (
            selection.model.casefold() in {"latest", "default"}
            or selection.model.casefold().endswith(":latest")
        ):
            raise ValueError("INFERENCE_ENDPOINT_OR_EXACT_MODEL_UNREVIEWED")
    return result


def _inference(ctx: QualificationContext, selection: InferenceSelection) -> HTTPInference:
    return selection.inference(ctx.environ)


def _load_inference_review(
    ctx: QualificationContext, selections: dict[str, InferenceSelection]
) -> tuple[InferenceReview, dict[str, InferenceSelection], list[tuple[str, str]]] | None:
    identity = content_hash({key: item.model_dump(mode="json") for key, item in selections.items()})
    ctx.template(
        "reviews/inference.json",
        {
            "reviewed_by": None,
            "reviewed_at": None,
            "expires_at": None,
            "configuration_sha256": identity,
            "budgets": {
                "per_job": None,
                "per_candidate": None,
                "per_stage": None,
                "per_agent": None,
                "daily": None,
                "per_model": [],
            },
            "native_timeout_seconds": None,
            "native_max_calls": None,
            "enable_native_cross_examination": False,
            "cross_examination_maximum_challenges": 8,
            "rates": {},
            "rates_evidence": None,
        },
    )
    ctx.write_json(
        "inputs/inference-facts.json",
        {
            "configuration_sha256": identity,
            "selections": {key: value.model_dump(mode="json") for key, value in selections.items()},
            "budget_rule": "Money validate_invocation_budgets; choose deliberate limits for full workflows",
            "rates_rule": "Optional GBP rates require actual pricing/FX review; empty means unknown cost",
            "probe_limit": "Three small exact-model probes; each <=512 completion tokens; final content must be OK",
            "scope_rule": "Local inference functionality is not hosted, native runtime, egress or release qualification",
        },
    )
    try:
        raw = ctx.read_bytes("reviews/inference.json")
        if raw is None:
            raise ValueError("INFERENCE_REVIEW_REQUIRED")
        review = InferenceReview.model_validate_json(raw)
        if (
            not _fresh(review.reviewed_at, review.expires_at, ctx.now)
            or review.configuration_sha256 != identity
        ):
            raise ValueError("INFERENCE_REVIEW_STALE")
        if not set(review.rates) <= set(FIRMS) or (review.rates and review.rates_evidence is None):
            raise ValueError("INFERENCE_RATE_EVIDENCE_REQUIRED")
        refs = [ctx.artifact(raw)]
        if review.rates_evidence:
            refs.append(ctx.artifact(_reference(ctx, review.rates_evidence)))
        for firm, rates in review.rates.items():
            selections[firm] = InferenceSelection.model_validate(
                {
                    **selections[firm].model_dump(),
                    "input_gbp_per_million": rates[0],
                    "output_gbp_per_million": rates[1],
                }
            )
        validate_invocation_budgets(
            (selections["tradingagents"], selections["ai_hedge_fund"], selections["crewai"]),
            review.native_max_calls,
            review.budgets,
            review.cross_examination_maximum_challenges
            if review.enable_native_cross_examination
            else 0,
        )
        return review, selections, refs
    except (ValueError, OSError, TypeError, KeyError):
        ctx.block(
            "REVIEWED_INFERENCE_SELECTIONS_REQUIRED",
            "Complete reviews/inference.json: approve exact selections and sufficient budgets; "
            "optional GBP cost rates require reviewed pricing/FX evidence.",
        )
        return None


def run_inference_stage(ctx: QualificationContext) -> dict[str, Any]:
    """Probe explicit selections, including local-only access without production PASS."""
    selections = _selections(ctx)
    refs: list[tuple[str, str]] = []
    success = True
    for firm, selection in selections.items():
        identity = content_hash(selection.model_dump(mode="json"))
        # Credentials are not hashed or written to disk. Presence is rechecked;
        # cached receipts attest a recent account request, not future key validity.
        if selection.authentication != "none" and not ctx.environ.get(
            selection.credential_environment_variable or ""
        ):
            ctx.block(
                "OPENAI_CREDENTIAL_REQUIRED"
                if selection.provider == "openai"
                else "INFERENCE_CREDENTIAL_REQUIRED_" + firm.upper(),
                "Configure the environment variable explicitly selected by this authenticated inference role.",
            )
            success = False
            continue
        cached = ctx.cache("inference-" + firm, identity, 3600)
        if cached is not None:
            try:
                references = [tuple(reference) for reference in cached["artifacts"]]
                if len(references) != 1:
                    raise ValueError("INFERENCE_CACHE_INVALID")
                receipt = InferenceProbeEvidence.model_validate_json(_reference(ctx, references[0]))
                if not receipt.matches(
                    firm, selection
                ) or not receipt.verified_at <= ctx.now < receipt.verified_at + timedelta(hours=1):
                    raise ValueError("INFERENCE_CACHE_INVALID")
                refs.extend(references)
                continue
            except (ValueError, TypeError, KeyError, IndexError):
                pass
        try:
            receipt = probe_selection(
                cast(Literal["tradingagents", "ai_hedge_fund", "crewai"], firm),
                selection,
                ctx.environ,
                observed_at=ctx.now,
            )
            reference = ctx.artifact(receipt.model_dump(mode="json"))
            refs.append(reference)
            ctx.checkpoint(
                "inference-" + firm, identity, {"artifacts": [reference]}, artifacts=(reference,)
            )
        except Exception as error:
            # Transport responses/exceptions can contain URLs or credentials.
            ctx.block(
                "INFERENCE_PROBE_FAILED_" + firm.upper(),
                "Bounded exact-model request failed ("
                + safe_probe_error(error)
                + "); verify the selected server/model/authentication and rerun.",
            )
            success = False
    local_only = any(selection.is_local for selection in selections.values())
    hosted_compatible = True
    for selection in selections.values():
        try:
            selection.require_hosted()
        except ValueError:
            hosted_compatible = False
    if local_only:
        ctx.block(
            "LOCAL_INFERENCE_NOT_HOSTED_QUALIFIED",
            "Local Ollama probes attest this machine only. Railway cannot reach the Mac's loopback; "
            "a separately reviewed remotely reachable inference endpoint and target-worker qualification are required.",
        )
    elif not hosted_compatible:
        ctx.block(
            "HOSTED_INFERENCE_SELECTION_REQUIRED",
            "Select and independently review hosted-compatible inference endpoints before production qualification.",
        )
    reviewed = _load_inference_review(ctx, selections)
    fields: dict[str, Any] = {}
    if reviewed is not None:
        review, selections, review_refs = reviewed
        refs.extend(review_refs)
        fields = {key: value.model_dump(mode="json") for key, value in selections.items()}
        fields.update(
            review.model_dump(
                mode="json",
                include={
                    "budgets",
                    "native_timeout_seconds",
                    "native_max_calls",
                    "enable_native_cross_examination",
                    "cross_examination_maximum_challenges",
                },
            )
        )
    result = {
        "complete": success and reviewed is not None and hosted_compatible and not local_only,
        "access_verified": success,
        "scope": "LOCAL_INFERENCE_ONLY" if local_only else "REMOTE_INFERENCE_ACCESS_ONLY",
        "hosted_compatible": hosted_compatible,
        "native_runtime_qualified": False,
        "production_qualified": False,
        "manifest_fields": fields,
        "artifacts": refs,
    }
    ctx.write_json("outputs/inference.json", result)
    # The legacy integration test intentionally uses one configuration. Do not
    # silently use it to attest differing per-firm model selections.
    if (
        result["complete"]
        and len({content_hash(item.model_dump(mode="json")) for item in selections.values()}) == 1
    ):
        ctx.write_json(
            "outputs/native-inference.json",
            selections[FIRMS[0]].model_dump(mode="json"),
        )
        result["production_environment"] = {
            "MONEY_NATIVE_INFERENCE_CONFIG": str(ctx.root / "outputs/native-inference.json")
        }
    return result


def _inventory() -> list[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"]
        version = distribution.version
        if (
            not name
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
            or not re.fullmatch(r"[A-Za-z0-9_.+!-]+", version)
        ):
            raise ValueError("DEPENDENCY_IDENTITY_INVALID")
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        # Money is this checked-out application, not a same-named public package.
        if normalized != "money":
            result.add((normalized, version))
    if not result or len(result) > 2000:
        raise ValueError("DEPENDENCY_INVENTORY_INVALID")
    return sorted(result)


def _command(ctx: QualificationContext, arguments: list[str], timeout: int = 240) -> CommandResult:
    environment = {
        key: value
        for key, value in ctx.environ.items()
        if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}
    }
    environment.update(
        {"PYTHON_DOTENV_DISABLED": "1", "PIP_CONFIG_FILE": "/dev/null", "UV_NO_CONFIG": "1"}
    )
    return run_captured(arguments, cwd=ctx.repo, environ=environment, timeout=timeout)


def _security(ctx: QualificationContext, inventory: list[tuple[str, str]]) -> dict[str, Any]:
    identity = content_hash(inventory)
    requirements = "".join(f"{name}=={version}\n" for name, version in inventory).encode()
    cached = ctx.cache("native-security", identity, 3600)
    if cached is not None:
        try:
            references = [tuple(reference) for reference in cached["artifacts"]]
            if len(references) != 2 or _reference(ctx, references[0]) != requirements:
                raise ValueError("NATIVE_SECURITY_CACHE_INVALID")
            evidence = json.loads(_reference(ctx, references[1]))
            observed = TypeAdapter(AwareDatetime).validate_python(evidence["verified_at"])
            if (
                evidence["kind"] != "money-native-security-v1"
                or [tuple(item) for item in evidence["inventory"]] != inventory
                or evidence["security_warnings_ignored"] is not False
                or not observed <= ctx.now < observed + timedelta(hours=1)
            ):
                raise ValueError("NATIVE_SECURITY_CACHE_INVALID")
            passed, findings = _audit_result(
                evidence["audit"],
                inventory,
                evidence["dependency_check_exit"],
                evidence["audit_exit"],
            )
            # Checkpoint flags are never admission authority. Reconstruct solely
            # from the genuine hash-addressed report and its full coverage.
            return {"passed": passed, "findings": findings, "artifacts": references}
        except (ValueError, TypeError, KeyError, IndexError):
            pass
    requirements_ref = ctx.artifact(requirements)
    checked = _command(ctx, ["uv", "pip", "check", "--python", sys.executable], 60)
    audited = _command(
        ctx,
        [
            "uv",
            "tool",
            "run",
            "--from",
            "pip-audit==2.10.1",
            "pip-audit",
            "--strict",
            "--no-deps",
            "--disable-pip",
            "--timeout",
            "15",
            "--requirement",
            str(ctx.root / requirements_ref[1]),
            "--format",
            "json",
            "--progress-spinner",
            "off",
        ],
    )
    # uv/pip-audit may emit harmless installation/status text on stderr. The
    # bounded capture is never printed or persisted; only the complete JSON
    # report is admitted and scanned for credentials before becoming an artifact.
    start, end = audited.output.find(b"{"), audited.output.rfind(b"}")
    if start < 0 or end <= start:
        raise ValueError("DEPENDENCY_AUDIT_INVALID")
    raw = json.loads(audited.output[start : end + 1])
    passed, findings = _audit_result(raw, inventory, checked.returncode, audited.returncode)
    artifact = ctx.artifact(
        {
            "kind": "money-native-security-v1",
            "verified_at": ctx.now.isoformat(),
            "inventory": inventory,
            "dependency_check_exit": checked.returncode,
            "audit_exit": audited.returncode,
            "audit": raw,
            "passed": passed,
            "security_warnings_ignored": False,
        }
    )
    result = {"passed": passed, "findings": findings, "artifacts": [requirements_ref, artifact]}
    # Failures can recover immediately after an advisory fix; cache only successes.
    if passed:
        ctx.checkpoint("native-security", identity, result, artifacts=(requirements_ref, artifact))
    return result


def _audit_result(
    raw: dict[str, Any], inventory: list[tuple[str, str]], dependency_exit: int, audit_exit: int
) -> tuple[bool, list[dict[str, Any]]]:
    dependencies = raw.get("dependencies")
    if not isinstance(dependencies, list):
        raise ValueError("DEPENDENCY_AUDIT_INVALID")
    covered = {
        (re.sub(r"[-_.]+", "-", item["name"]).lower(), item["version"])
        for item in dependencies
        if "version" in item and not item.get("skip_reason")
    }
    findings = [
        {
            "name": item["name"],
            "version": item.get("version"),
            "advisories": sorted(str(vuln["id"]) for vuln in item.get("vulns", [])),
        }
        for item in dependencies
        if item.get("vulns")
    ]
    # A zero return code with missing coverage is not security qualification.
    passed = (
        type(dependency_exit) is int
        and dependency_exit == 0
        and type(audit_exit) is int
        and audit_exit == 0
        and covered == set(inventory)
        and not findings
    )
    return passed, findings


def _target_identity() -> str | None:
    """A Railway CLI environment on a Mac is not the deployed worker namespace."""
    if platform.system() != "Linux":
        return None
    try:
        namespace = os.readlink("/proc/self/ns/net")
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if not re.fullmatch(r"net:\[\d+\]", namespace) or not re.fullmatch(r"[a-f0-9-]{36}", boot):
            return None
        return "linux-network-namespace:" + content_hash({"namespace": namespace, "boot": boot})
    except OSError:
        return None


def _resolve_native_closure(ctx: QualificationContext) -> dict[str, Any]:
    """Resolve pinned native metadata for Linux without modifying any environment.

    Full-distribution conflicts are retained, not hidden with --no-deps or
    overriding upstream constraints. A successful solve is only build input;
    installed source, ABI, import, live execution and security gates still apply.
    """
    enabled = qlib_enabled(ctx.environ)
    paths = [
        "pyproject.toml",
        "upstreams/tradingagents/pyproject.toml",
        "upstreams/ai-hedge-fund/pyproject.toml",
        "upstreams/crewai/lib/crewai/pyproject.toml",
        "upstreams/crewai/lib/crewai-core/pyproject.toml",
        "upstreams/crewai/lib/cli/pyproject.toml",
    ]
    roots = {
        "tradingagents": "upstreams/tradingagents/tradingagents",
        "hedge_fund": "upstreams/ai-hedge-fund/hedge_fund",
        "crewai": "upstreams/crewai/lib/crewai/src/crewai",
    }
    if enabled:
        paths.append("upstreams/qlib/pyproject.toml")
        roots["qlib"] = "upstreams/qlib/qlib"
    for package, relative in roots.items():
        if source_fingerprint(ctx.repo / relative, package) != SOURCE_DIGESTS[package]:
            raise ValueError("NATIVE_RESOLVER_SOURCE_PIN_MISMATCH")
    inputs = {}
    for relative in [*paths, "UPSTREAM_LOCK.txt", "uv.lock"]:
        path = ctx.repo / relative
        if path.is_symlink() or path.stat().st_size > 5_000_000:
            raise ValueError("NATIVE_RESOLVER_INPUT_INVALID")
        inputs[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    identity = content_hash({"inputs": inputs, "qlib_enabled": enabled})
    cached = ctx.cache("native-dependency-resolution", identity, 3600)
    if cached is not None:
        try:
            references = [tuple(reference) for reference in cached["artifacts"]]
            if len(references) != 3:
                raise ValueError("NATIVE_RESOLUTION_CACHE_INVALID")
            for reference in references:
                _reference(ctx, reference)
            receipt = json.loads(_reference(ctx, references[2]))
            observed = TypeAdapter(AwareDatetime).validate_python(receipt["observed_at"])
            if (
                receipt["input_sha256"] != inputs
                or receipt.get("qlib_enabled") is not enabled
                or not observed <= ctx.now < observed + timedelta(hours=1)
                or [tuple(item) for item in receipt["artifacts"]] != references[:2]
                or type(receipt["exit_code"]) is not int
                or receipt["resolved"] is not (receipt["exit_code"] == 0)
                or receipt["installs_performed"] is not False
                or receipt["production_runtime_qualified"] is not False
            ):
                raise ValueError("NATIVE_RESOLUTION_CACHE_INVALID")
            # Preserve an actual conflict even if a mutable checkpoint claims a
            # successful solve. No failed dependency report can be promoted by
            # editing its convenience cache value.
            cached_result = {**receipt, "artifacts": references}
            ctx.write_json("outputs/native-dependency-resolution.json", cached_result)
            return cached_result
        except (ValueError, TypeError, KeyError, IndexError):
            pass
    requirements: list[str] = []
    for metadata_relative in paths:
        metadata = tomllib.loads((ctx.repo / metadata_relative).read_text())
        if "project" in metadata:
            requirements.extend(metadata["project"].get("dependencies", []))
        else:
            for name, constraint in metadata["tool"]["poetry"]["dependencies"].items():
                if name == "python":
                    continue  # The native worker solve explicitly targets Python 3.12.
                if not isinstance(constraint, str):
                    raise ValueError("NATIVE_DEPENDENCY_CONSTRAINT_UNSUPPORTED")
                if constraint.startswith("^"):
                    version = constraint[1:]
                    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
                        raise ValueError("NATIVE_DEPENDENCY_CONSTRAINT_UNSUPPORTED")
                    parts = [int(value) for value in version.split(".")]
                    index = next((index for index, value in enumerate(parts) if value), 2)
                    upper = [*parts[:index], parts[index] + 1, *([0] * (2 - index))]
                    constraint = f">={version},<{'.'.join(str(value) for value in upper)}"
                elif re.fullmatch(r"\d+(?:\.\d+)+", constraint):
                    constraint = "==" + constraint
                else:
                    raise ValueError("NATIVE_DEPENDENCY_CONSTRAINT_UNSUPPORTED")
                requirements.append(name + constraint)
    # Resolve the exact pinned CrewAI distribution and the research-only tools
    # declared by Money, rather than accidentally solving a newer native source.
    requirements.extend(["crewai[tools]==1.15.21"])
    requirements_ref = ctx.artifact(("\n".join(sorted(set(requirements))) + "\n").encode())
    arguments = [
        "uv",
        "pip",
        "compile",
        str(ctx.root / requirements_ref[1]),
        "--python-version",
        "3.12",
        "--python-platform",
        "linux",
        "--generate-hashes",
        "--no-annotate",
        "--no-header",
        "--no-build",
        "--no-sources",
        "--no-python-downloads",
        "--color",
        "never",
    ]
    attempted = _command(ctx, arguments, 180)
    # There are no credentials in the child environment or inputs. Nevertheless,
    # the common artifact boundary checks every byte before retaining diagnostics.
    diagnostics = ctx.artifact(attempted.output)
    resolved = attempted.returncode == 0
    result: dict[str, Any] = {
        "qlib_enabled": enabled,
        "resolved": resolved,
        "observed_at": ctx.now.isoformat(),
        "exit_code": attempted.returncode,
        "input_sha256": inputs,
        "artifacts": [requirements_ref, diagnostics],
        "installs_performed": False,
        "production_runtime_qualified": False,
    }
    if resolved:
        ctx.write_bytes("outputs/native-closure-requirements.txt", attempted.output)
    receipt = ctx.artifact(result)
    result["artifacts"].append(receipt)
    ctx.write_json("outputs/native-dependency-resolution.json", result)
    # Do not cache network/cache/service failures; a normal resolution conflict
    # may be reused briefly when the exact pinned input bytes are unchanged.
    if resolved or b"No solution found" in attempted.output:
        ctx.checkpoint(
            "native-dependency-resolution",
            identity,
            result,
            artifacts=(requirements_ref, diagnostics, receipt),
        )
    return result


# This process deliberately does not import Money or install its Python socket
# hooks. It has no credentials. Thus its ordinary socket/SSL calls reach the OS
# directly, including arbitrary subprocess, UDP and raw-socket escape vectors.
_EGRESS_PROBE = """
import json, socket, ssl, sys
observations = []
def tcp(vector, host, expected, tls=False):
    reached = False
    try:
        with socket.create_connection((host, 443), timeout=3) as connection:
            if tls:
                with ssl.create_default_context().wrap_socket(connection, server_hostname=host):
                    reached = True
            else:
                reached = True
    except (OSError, ValueError):
        pass
    observations.append({"vector":vector, "host":host, "reached":reached, "expected":expected})
for approved_host in json.loads(sys.argv[1]):
    tcp("approved_https", approved_host, True, True)
tcp("unapproved_https", "example.com", False)
tcp("direct_ipv4", "1.1.1.1", False)
tcp("direct_ipv6", "2606:4700:4700::1111", False)
tcp("subprocess_egress", "example.org", False)
reached = False
try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(3)
        connection.sendto(bytes.fromhex("4d5901000001000000000000") + b"\\x07example\\x03com\\x00\\x00\\x01\\x00\\x01", ("8.8.8.8", 53))
        reached = bool(connection.recv(512))
except OSError:
    pass
observations.append({"vector":"udp_dns", "host":"8.8.8.8", "reached":reached, "expected":False})
reached = False
try:
    with socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP):
        reached = True
except OSError:
    pass
observations.append({"vector":"raw_socket", "host":None, "reached":reached, "expected":False})
print(json.dumps({"engine":"unhooked-native-os-sockets", "observations":observations}, sort_keys=True))
"""


def _verify_running_enforcement(ctx: QualificationContext, review: EgressReview) -> tuple[str, str]:
    current = _target_identity()
    if current is None or current != review.target_identity:
        raise ValueError("EGRESS_ACTUAL_TARGET_MISMATCH")
    process_status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    if (
        any(
            int(process_status[field].strip(), 16) & ((1 << 12) | (1 << 13) | (1 << 21))
            for field in ("CapEff", "CapPrm", "CapInh", "CapAmb")
        )
        or process_status["NoNewPrivs"].strip() != "1"
    ):
        raise ValueError("EGRESS_WORKER_PRIVILEGES_UNCONFINED")
    command = {"linux-nftables": ["nft", "--json", "list", "ruleset"]}.get(review.enforcement)
    if command is None:
        # Container networking needs a platform-specific inspector; accepting an
        # arbitrary uploaded Kubernetes/Docker policy would not prove enforcement.
        raise ValueError("EGRESS_ENFORCEMENT_INSPECTOR_REQUIRED")
    policy = _command(ctx, command, 30)
    if policy.returncode or hashlib.sha256(policy.output).hexdigest() != review.policy[0]:
        raise ValueError("EGRESS_CURRENT_POLICY_MISMATCH")
    approved_addresses = {
        address for host in review.approved_hosts for address in public_addresses(host)
    }
    _strict_nft_policy(policy.output, approved_addresses)
    probe = _command(
        ctx,
        [sys.executable, "-I", "-S", "-c", _EGRESS_PROBE, json.dumps(review.approved_hosts)],
        45,
    )
    raw = json.loads(probe.output)
    expected: dict[tuple[str, str | None], bool] = {
        **{("approved_https", host): True for host in review.approved_hosts},
        ("unapproved_https", "example.com"): False,
        ("direct_ipv4", "1.1.1.1"): False,
        ("direct_ipv6", "2606:4700:4700::1111"): False,
        ("subprocess_egress", "example.org"): False,
        ("udp_dns", "8.8.8.8"): False,
        ("raw_socket", None): False,
    }
    observations = raw.get("observations", [])
    if (
        probe.returncode
        or raw.get("engine") != "unhooked-native-os-sockets"
        or len(observations) != len(expected)
        or {(item["vector"], item["host"]) for item in observations} != set(expected)
        or any(
            item["reached"] is not expected[(item["vector"], item["host"])] for item in observations
        )
    ):
        raise ValueError("EGRESS_CURRENT_PROBE_FAILED")
    return ctx.artifact(
        {
            "target_identity": current,
            "policy_sha256": review.policy[0],
            "execution_identity": review.execution_identity,
            "observations": raw,
        }
    )


def _strict_nft_policy(raw: bytes, approved_addresses: set[str]) -> None:
    """Accept only an intentionally tiny, auditable inet output allowlist.

    Reject sets, maps, jumps, established-flow exemptions, NAT, alternate output
    hooks and arbitrary DNS/UDP/loopback access. Probe timeouts alone prove none
    of this. More elaborate deployments require a reviewed dedicated inspector,
    not a permissive best-effort parse of firewall rules.
    """
    entries = json.loads(raw)["nftables"]
    chains = [entry["chain"] for entry in entries if "chain" in entry]
    tables = [entry["table"] for entry in entries if "table" in entry]
    if (
        len(chains) != 1
        or len(tables) != 1
        or any(set(entry) - {"metainfo", "table", "chain", "rule"} for entry in entries)
    ):
        raise ValueError("EGRESS_POLICY_UNSUPPORTED")
    chain, table = chains[0], tables[0]
    if (
        table["family"] != "inet"
        or chain["family"] != "inet"
        or chain["table"] != table["name"]
        or chain.get("hook") != "output"
        or chain.get("type") != "filter"
        or chain.get("policy") != "drop"
    ):
        raise ValueError("EGRESS_DEFAULT_DENY_REQUIRED")
    allowed: set[str] = set()
    for entry in entries:
        if "rule" not in entry:
            continue
        rule = entry["rule"]
        if (
            rule["family"] != "inet"
            or rule["table"] != table["name"]
            or rule["chain"] != chain["name"]
        ):
            raise ValueError("EGRESS_POLICY_UNSUPPORTED")
        expressions = rule["expr"]
        if len(expressions) != 4 or expressions[-1] != {"accept": None}:
            raise ValueError("EGRESS_POLICY_UNSUPPORTED")
        matches = [expression.get("match") for expression in expressions[:-1]]
        if any(not isinstance(match, dict) or match.get("op") != "==" for match in matches):
            raise ValueError("EGRESS_POLICY_UNSUPPORTED")
        expected = {
            json.dumps({"meta": {"key": "l4proto"}}, sort_keys=True): "tcp",
            json.dumps({"payload": {"protocol": "tcp", "field": "dport"}}, sort_keys=True): 443,
        }
        observed = {json.dumps(match["left"], sort_keys=True): match["right"] for match in matches}
        if (
            any(observed.pop(key, None) != value for key, value in expected.items())
            or len(observed) != 1
        ):
            raise ValueError("EGRESS_POLICY_UNSUPPORTED")
        destination, address = next(iter(observed.items()))
        if (
            json.loads(destination)
            not in (
                {"payload": {"protocol": "ip", "field": "daddr"}},
                {"payload": {"protocol": "ip6", "field": "daddr"}},
            )
            or not isinstance(address, str)
            or address not in approved_addresses
        ):
            raise ValueError("EGRESS_UNAPPROVED_DESTINATION")
        allowed.add(address)
    if not allowed:
        raise ValueError("EGRESS_APPROVED_DESTINATION_REQUIRED")


def _egress(ctx: QualificationContext, identity: str, hosts: tuple[str, ...]) -> dict[str, Any]:
    target_identity = _target_identity()
    ctx.template(
        "reviews/native-egress.json",
        {
            "collected_by": None,
            "reviewed_by": None,
            "reviewed_at": None,
            "expires_at": None,
            "execution_identity": identity,
            "target_identity": target_identity,
            "enforcement": None,
            "approved_hosts": hosts,
            "policy": None,
            "native_probes": None,
            "review_evidence": None,
            "policy_scope": None,
        },
    )
    ctx.write_json(
        "inputs/native-egress-requirements.json",
        {
            "execution_identity": identity,
            "actual_running_target_identity": target_identity,
            "approved_hosts": hosts,
            "required_artifacts": [
                "Raw target OS/container enforced policy (not a Python socket patch)",
                "Raw native allow/deny observations on the same worker target",
                "Independent review binding target identity and every observed result",
            ],
            "probe_schema": {
                "execution_identity": identity,
                "target_identity": "UNRESOLVED",
                "observed_at": None,
                "enforcement_policy_sha256": None,
                "allowed_hosts": [],
                "denied_vectors": [],
                "enforcement_active": None,
            },
            "required_denied_vectors": [
                "unapproved_https",
                "direct_ipv4",
                "direct_ipv6",
                "udp_dns",
                "raw_socket",
                "subprocess_egress",
            ],
            "limitation": "Local Mac reachability and operator PASS booleans do not attest hosted OS enforcement.",
        },
    )
    try:
        if not hosts:
            raise ValueError("EGRESS_REMOTE_INFERENCE_HOSTS_REQUIRED")
        review = EgressReview.model_validate(ctx.read_json("reviews/native-egress.json"))
        if (
            review.reviewed_by == review.collected_by
            or not _fresh(review.reviewed_at, review.expires_at, ctx.now)
            or review.execution_identity != identity
            or set(review.approved_hosts) != set(hosts)
        ):
            raise ValueError("EGRESS_REVIEW_INVALID")
        policy = _reference(ctx, review.policy)
        probes_raw = _reference(ctx, review.native_probes)
        independent = _reference(ctx, review.review_evidence)
        probes = json.loads(probes_raw)
        observed = TypeAdapter(AwareDatetime).validate_python(probes["observed_at"])
        if (
            not policy.strip()
            or not independent.strip()
            or probes["execution_identity"] != identity
            or probes["target_identity"] != review.target_identity
            or probes["enforcement_policy_sha256"] != review.policy[0]
            or not review.reviewed_at >= observed
            or ctx.now - observed > timedelta(days=1)
            or probes.get("enforcement_active") is not True
            or set(probes["allowed_hosts"]) != set(hosts)
            or set(probes["denied_vectors"])
            != {
                "unapproved_https",
                "direct_ipv4",
                "direct_ipv6",
                "udp_dns",
                "raw_socket",
                "subprocess_egress",
            }
            or not isinstance(probes.get("observations"), list)
            or len(probes["observations"]) < 6 + len(hosts)
        ):
            raise ValueError("EGRESS_TARGET_OBSERVATIONS_INCOMPLETE")
        running_probe = _verify_running_enforcement(ctx, review)
        refs = [ctx.artifact(value) for value in (policy, probes_raw, independent)]
        refs.append(running_probe)
        artifact = ctx.artifact(
            {
                "kind": "money-reviewed-target-egress-v1",
                "review": review.model_dump(mode="json"),
                "raw_evidence": refs,
                "verification": "independently-reviewed-target-enforcement",
            }
        )
        return {
            "complete": True,
            "artifacts": [*refs, artifact],
            "manifest_fields": {
                "native_egress_policy_verified": True,
                "native_egress_verification_hash": artifact[0],
            },
        }
    except Exception:
        ctx.block(
            "HOST_EGRESS_TARGET_EVIDENCE_REQUIRED",
            "Provide actual target worker OS/container policy and native allow/deny probe bytes, "
            "independently reviewed in reviews/native-egress.json, then run on that enforced Linux "
            "worker namespace with readable supported nftables policy; a Mac or Python guard cannot qualify it.",
        )
        return {"complete": False, "artifacts": [], "manifest_fields": {}}


def run_native_preflight_stage(ctx: QualificationContext) -> dict[str, Any]:
    """Observe installed closure; preserve source, missing dependency and CVE failures."""
    enabled = qlib_enabled(ctx.environ)
    inventory = _inventory()
    pins: dict[str, str | None] = {}
    for package, expected in SOURCE_DIGESTS.items():
        if package == "qlib" and not enabled:
            continue
        try:
            require_pinned_source(package)
            pins[package] = expected
        except Exception:
            pins[package] = None
            ctx.block(
                "NATIVE_SOURCE_UNQUALIFIED_" + package.upper(),
                "Install the exact native source and compiled dependency closure described in "
                "docs/NATIVE_WORKER_BUILD.md; version labels alone are not source qualification.",
            )
    identity = content_hash(
        {
            "inventory": inventory,
            "qlib_enabled": enabled,
            "pins": pins,
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        }
    )
    ctx.write_json(
        "inputs/native-runtime-facts.json",
        {
            "execution_identity": identity,
            "qlib_enabled": enabled,
            "source_pins": pins,
            "inventory": inventory,
            "platform": platform.system(),
        },
    )
    refs: list[tuple[str, str]] = []
    try:
        closure = _resolve_native_closure(ctx)
        refs.extend(closure["artifacts"])
        if not closure["resolved"]:
            ctx.block(
                "NATIVE_DEPENDENCY_RESOLUTION_FAILED",
                "Review outputs/native-dependency-resolution.json and its actual resolver diagnostics; "
                "resolve pinned distribution conflicts before building the worker. No environment was modified.",
            )
    except Exception:
        closure = {"resolved": False, "artifacts": []}
        ctx.block(
            "NATIVE_DEPENDENCY_RESOLVER_UNAVAILABLE",
            "Make pinned upstream checkouts and package registry metadata available, then rerun the "
            "automatic isolated Linux dependency solve; current environment remains unchanged.",
        )
    security_passed = False
    try:
        security = _security(ctx, inventory)
        security_passed = security["passed"]
        refs.extend(security["artifacts"])
        if not security_passed:
            chroma = any(item["name"] == "chromadb" for item in security["findings"])
            ctx.block(
                "CHROMADB_SECURITY_ADVISORIES" if chroma else "NATIVE_DEPENDENCY_SECURITY_FAILED",
                "Resolve the unsuppressed dependency audit findings in outputs/native-preflight.json; "
                "a compatible, pinned and fully audited native closure is required.",
            )
    except Exception:
        security = {"passed": False, "findings": [], "artifacts": []}
        ctx.block(
            "NATIVE_SECURITY_AUDIT_UNAVAILABLE",
            "Restore pip-audit/PyPI access and rerun; a missing or incomplete advisory scan cannot qualify runtime.",
        )
    hosts: tuple[str, ...] = ()
    try:
        selections = _selections(ctx)
        for selection in selections.values():
            selection.require_hosted()
            if urlsplit(selection.endpoint).port not in (None, 443):
                # The existing OS inspector qualifies only HTTPS on port 443.
                raise ValueError("EGRESS_INFERENCE_PORT_UNSUPPORTED")
        hosts = tuple(
            sorted({urlsplit(value.endpoint).hostname or "" for value in selections.values()})
        )
    except (ValueError, OSError):
        ctx.block(
            "HOST_EGRESS_REMOTE_INFERENCE_REQUIRED",
            "Hosted native egress needs explicitly selected remote HTTPS:443 inference hosts; local Ollama cannot qualify the worker.",
        )
    egress = _egress(ctx, identity, hosts)
    refs.extend(egress["artifacts"])
    result = {
        "qlib_enabled": enabled,
        "complete": all(pins.values())
        and closure["resolved"]
        and security_passed
        and egress["complete"],
        "verified_at": ctx.now.isoformat(),
        "execution_identity": identity,
        "pins": pins,
        "security": security,
        "dependency_resolution": closure,
        "manifest_fields": egress["manifest_fields"],
        "artifacts": refs,
    }
    ctx.write_json("outputs/native-preflight.json", result)
    return result


def _execution_inputs(
    ctx: QualificationContext,
) -> tuple[dict[str, Any], dict[str, Any], ResearchSnapshot] | None:
    inference = ctx.read_json("outputs/inference.json")
    preflight = ctx.read_json("outputs/native-preflight.json")
    snapshot_raw = ctx.read_bytes("outputs/snapshot.json")
    if (
        not inference
        or not inference.get("complete")
        or not preflight
        or not preflight.get("complete")
        or snapshot_raw is None
    ):
        ctx.block(
            "NATIVE_EXECUTION_INPUTS_REQUIRED",
            "Complete inference review, native closure/security, target egress and genuine frozen outputs/snapshot.json.",
        )
        return None
    for firm in FIRMS:
        InferenceSelection.model_validate(inference["manifest_fields"][firm]).require_hosted()
    verified = TypeAdapter(AwareDatetime).validate_python(preflight["verified_at"])
    if not verified <= ctx.now < verified + timedelta(hours=1):
        raise ValueError("NATIVE_PREFLIGHT_STALE")
    snapshot = ResearchSnapshot.model_validate_json(snapshot_raw)
    enabled = qlib_enabled(ctx.environ)
    if snapshot.qlib_enabled is not enabled or preflight.get("qlib_enabled", True) is not enabled:
        raise ValueError("QLIB_MODE_EXECUTION_INPUT_MISMATCH")
    if (
        snapshot.created_at > ctx.now
        or eligibility_failures(snapshot.instrument, ResearchMandate(), ctx.now)
        or any(
            record.critical
            and (
                record.fresh_until <= ctx.now
                or not record.available_at(snapshot.cutoff_for(record))
            )
            for record in snapshot.evidence
        )
    ):
        raise ValueError("NATIVE_SNAPSHOT_STALE_OR_UNAVAILABLE")
    if snapshot.instrument.provider in {"money-demo", "test"} or any(
        record.provider in {"money-demo", "test"} for record in snapshot.evidence
    ):
        raise ValueError("NATIVE_SYNTHETIC_EVIDENCE_DENIED")
    return inference, preflight, snapshot


def _active_model(ctx: QualificationContext, qlib: dict[str, Any]) -> QualifiedLinearModel:
    model = load_qualified_model(Path(qlib["model_path"]), qlib["qlib_artifact_hash"])
    store = ResearchStore(ctx.environ.get("DATABASE_URL", ""), allow_sqlite=False)
    try:
        active = ModelRegistry(store).load_active(
            qlib["qlib_registry_id"], qlib["qlib_artifact_hash"], ctx.now
        )
        if active != model:
            raise ValueError("NATIVE_ACTIVE_MODEL_MISMATCH")
        return active
    finally:
        store.engine.dispose()


def run_first_pass_stage(ctx: QualificationContext) -> dict[str, Any]:
    """Seal two independent firms, adding promoted numeric Qlib only when selected."""
    inputs = _execution_inputs(ctx)
    if inputs is None:
        return {"complete": False, "artifacts": []}
    inference, preflight, snapshot = inputs
    config = inference["manifest_fields"]
    enabled = qlib_enabled(ctx.environ)
    if snapshot.qlib_enabled is not enabled:
        raise ValueError("QLIB_MODE_SNAPSHOT_MISMATCH")
    qlib = ctx.read_json("outputs/qlib-state.json") if enabled else None
    if enabled and not qlib:
        ctx.block(
            "NATIVE_QLIB_PROMOTION_REQUIRED",
            "Complete independent/manual Qlib promotion before sealing three first-pass reports.",
        )
        return {"complete": False, "artifacts": []}
    model_hash = qlib["qlib_artifact_hash"] if qlib else None
    model = _active_model(ctx, qlib) if qlib else None
    identity = content_hash(
        {
            "snapshot": snapshot.hash,
            "qlib_enabled": enabled,
            "runtime": preflight["execution_identity"],
            "config": config,
            "qlib_artifact_hash": model_hash,
            "security": preflight["security"]["artifacts"],
            "egress": preflight["manifest_fields"],
        }
    )
    cached = ctx.cache("native-first-pass", identity, 3600)
    if cached is not None:
        sealed = json.loads(_reference(ctx, tuple(cached["artifacts"][0])))
        if (
            sealed["input_identity"] != identity
            or sealed["state"] != "FIRST_PASS_LOCKED"
            or sealed.get("qlib_enabled", True) is not enabled
        ):
            raise ValueError("NATIVE_FIRST_PASS_CACHE_INVALID")
        schemas: dict[str, type[FirmReport]] = {
            "tradingagents": TradingAgentsResearchReport,
            "ai_hedge_fund": AIHedgeFundResearchReport,
            "qlib": QlibQuantResearchReport,
        }
        cached_reports = sealed["reports"]
        if (
            len(cached_reports) != len(snapshot.required_first_pass_firms)
            or {item["firm"] for item in cached_reports} != snapshot.required_first_pass_firms
        ):
            raise ValueError("NATIVE_FIRST_PASS_CACHE_INVALID")
        for item in cached_reports:
            schema = schemas[item["firm"]]
            _validate_report(schema.model_validate(item), snapshot, schema, item["firm"])
        cached["reports"] = cached_reports
        ctx.write_json("outputs/first-pass.json", cached_reports)
        return cached
    settings = NativeRunSettings(
        timeout_seconds=config["native_timeout_seconds"], max_calls=config["native_max_calls"]
    )
    reports: list[FirmReport] = []
    for firm, runner_type, adapter_type, report_type in (
        (
            "tradingagents",
            TradingAgentsNativeRunner,
            TradingAgentsAdapter,
            TradingAgentsResearchReport,
        ),
        ("ai_hedge_fund", AIHedgeFundNativeRunner, AIHedgeFundAdapter, AIHedgeFundResearchReport),
    ):
        cached = ctx.cache("native-firm-" + firm, identity, 3600)
        if cached is not None:
            raw_cached = _reference(ctx, tuple(cached["artifact"]))
            cached_report = report_type.model_validate_json(raw_cached)
            reports.append(_validate_report(cached_report, snapshot, report_type, firm))
            continue
        transport = _inference(ctx, InferenceSelection.model_validate(config[firm]))
        runner = BoundedNativeRunner(
            runner_type(transport, settings),
            report_type,
            NativeProcessPolicy(
                gateway_hosts=transport.allowed_network_hosts,
                gateway_port=transport.allowed_network_port,
                timeout_seconds=settings.timeout_seconds,
            ),
        )
        report = adapter_type(cast(Any, runner)).research(ResearchMandate(), snapshot)
        if report.usage.input_tokens is None or report.usage.output_tokens is None:
            raise ValueError("NATIVE_INFERENCE_USAGE_REQUIRED")
        reports.append(report)
        reference = ctx.artifact(report.model_dump_json().encode())
        ctx.checkpoint(
            "native-firm-" + firm, identity, {"artifact": reference}, artifacts=(reference,)
        )
    if enabled:
        if model is None:
            raise ValueError("NATIVE_QLIB_PROMOTION_REQUIRED")
        quant_runner = BoundedNativeRunner(
            QlibNativeRunner(model),
            QlibQuantResearchReport,
            NativeProcessPolicy(timeout_seconds=settings.timeout_seconds),
        )
        reports.append(QlibAdapter(cast(Any, quant_runner)).research(ResearchMandate(), snapshot))
    raw_reports = [report.model_dump(mode="json") for report in reports]
    ref = ctx.artifact(
        {
            "state": "FIRST_PASS_LOCKED",
            "qlib_enabled": enabled,
            "snapshot_hash": snapshot.hash,
            "input_identity": identity,
            "execution_identity": preflight["execution_identity"],
            "reports": raw_reports,
        }
    )
    result = {
        "complete": True,
        "qlib_enabled": enabled,
        "artifacts": [ref],
        "reports": raw_reports,
        "production_environment": {
            "MONEY_NATIVE_QUALIFICATION_SNAPSHOT": str(ctx.root / "outputs/snapshot.json"),
            "MONEY_NATIVE_QUALIFICATION_REPORTS": str(ctx.root / "outputs/first-pass.json"),
        },
    }
    ctx.checkpoint("native-first-pass", identity, result, artifacts=(ref,))
    ctx.write_json("outputs/first-pass.json", raw_reports)
    return result


def run_cio_stage(ctx: QualificationContext) -> dict[str, Any]:
    """Actual central CrewAI audit and Red Team after frozen first pass and LEAN."""
    inputs = _execution_inputs(ctx)
    if inputs is None:
        return {"complete": False, "artifacts": []}
    inference, preflight, snapshot = inputs
    raw_reports = ctx.read_json("outputs/first-pass.json")
    lean_raw = ctx.read_bytes("outputs/lean-report.json")
    if raw_reports is None or lean_raw is None:
        ctx.block(
            "CIO_LOCKED_REPORTS_AND_LEAN_REQUIRED",
            "Complete every snapshot-selected sealed first-pass report and genuine LEAN validation before CrewAI CIO/Red Team.",
        )
        return {"complete": False, "artifacts": []}
    schemas: dict[str, type[FirmReport]] = {
        "tradingagents": TradingAgentsResearchReport,
        "ai_hedge_fund": AIHedgeFundResearchReport,
        "qlib": QlibQuantResearchReport,
    }
    reports = tuple(schemas[report["firm"]].model_validate(report) for report in raw_reports)
    if (
        len(reports) != len(snapshot.required_first_pass_firms)
        or {report.firm for report in reports} != snapshot.required_first_pass_firms
    ):
        raise ValueError("NATIVE_FIRST_PASS_INCOMPLETE")
    lean = LeanValidationReport.model_validate_json(lean_raw)
    config = inference["manifest_fields"]
    enabled = qlib_enabled(ctx.environ)
    if snapshot.qlib_enabled is not enabled:
        raise ValueError("QLIB_MODE_SNAPSHOT_MISMATCH")
    qlib = ctx.read_json("outputs/qlib-state.json") if enabled else None
    if enabled and not qlib:
        ctx.block(
            "CIO_ACTIVE_QLIB_MODEL_REQUIRED",
            "Restore the currently active qualified Qlib model for the CIO's independent numeric verification.",
        )
        return {"complete": False, "artifacts": []}
    model = _active_model(ctx, qlib) if qlib else None
    identity = content_hash(
        {
            "snapshot": snapshot.hash,
            "qlib_enabled": enabled,
            "runtime": preflight["execution_identity"],
            "reports": raw_reports,
            "lean": lean.model_dump(mode="json"),
            "config": config,
            "qlib_registry_id": qlib["qlib_registry_id"] if qlib else None,
            "qlib_artifact_hash": qlib["qlib_artifact_hash"] if qlib else None,
            "security": preflight["security"]["artifacts"],
            "egress": preflight["manifest_fields"],
        }
    )
    cached = ctx.cache("native-cio", identity, 3600)
    if cached is not None:
        sealed = json.loads(_reference(ctx, tuple(cached["artifacts"][0])))
        if sealed["input_identity"] != identity:
            raise ValueError("CIO_CACHE_INPUT_MISMATCH")
        CIOResult.model_validate(sealed["result"])
        cached["result"] = sealed["result"]
        ctx.write_json("outputs/cio.json", cached["result"])
        return cached
    transport = _inference(ctx, InferenceSelection.model_validate(config["crewai"]))
    settings = NativeRunSettings(
        timeout_seconds=config["native_timeout_seconds"], max_calls=config["native_max_calls"]
    )
    runner = BoundedNativeRunner(
        CrewAINativeRunner(transport, settings, qualified_model=model),
        CIOResult,
        NativeProcessPolicy(
            gateway_hosts=transport.allowed_network_hosts,
            gateway_port=transport.allowed_network_port,
            timeout_seconds=settings.timeout_seconds,
        ),
    )
    adapter = CrewAICioAdapter(runner)
    audit = adapter.audit(snapshot, reports, lean)
    red_team = adapter.red_team(snapshot, reports)
    result = adapter.last_result
    if (
        result is None
        or not audit.completed
        or result.usage.input_tokens is None
        or result.usage.output_tokens is None
    ):
        raise ValueError("CIO_QUALIFICATION_INCOMPLETE")
    artifact = ctx.artifact(
        {
            "snapshot_hash": snapshot.hash,
            "input_identity": identity,
            "execution_identity": preflight["execution_identity"],
            "result": result.model_dump(mode="json"),
            "red_team_state": red_team.state,
        }
    )
    ctx.write_json("outputs/cio.json", result.model_dump(mode="json"))
    output = {
        "complete": True,
        "artifacts": [artifact],
        "result": result.model_dump(mode="json"),
        "production_environment": {
            "MONEY_NATIVE_QUALIFICATION_LEAN_REPORT": str(ctx.root / "outputs/lean-report.json")
        },
    }
    ctx.checkpoint("native-cio", identity, output, artifacts=(artifact,))
    return output


def run_native_stages(ctx: QualificationContext) -> dict[str, Any]:
    """Convenience entry point; orchestrators may interleave Qlib/LEAN stages."""
    return {
        "inference": run_inference_stage(ctx),
        "preflight": run_native_preflight_stage(ctx),
        "first_pass": run_first_pass_stage(ctx),
        "cio": run_cio_stage(ctx),
    }
