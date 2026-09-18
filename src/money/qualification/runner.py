"""Resumable operator workflow. Qualification is not hosted acceptance."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Never

from pydantic import AwareDatetime, Field, model_validator

from money.data.source_policy import issuer_source_policy
from money.qualification.core import MAXIMUM_BYTES, QualificationContext, json_bytes, run_captured
from money.qualification.snapshot import run_snapshot_stage
from money.research.inference_config import InferenceSelection, load_inference_selections
from money.research.live import LiveManifest, load_manifest
from money.research.qlib_mode import qlib_enabled
from money.schemas.contracts import Contract, ResearchSnapshot, utc_now
from money.usage_policy import USAGE_POLICY_VERSION, UsageMode, usage_mode

EXPECTED_ENVIRONMENT = {
    "MONEY_ENV": "production",
    "MONEY_DEPLOYMENT_ENV": "hosted",
    "MONEY_RESEARCH_MODE": "live",
    "MONEY_ENABLE_SYNTHETIC_DEMO": "false",
}


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # An accidentally pasted credential must not be echoed by argparse.
        self.exit(
            2,
            "QUALIFICATION BLOCKED\nInvalid arguments; use --help. Credentials belong only in the environment.\n",
        )


class ReleaseReview(Contract):
    """Explicit bundle approval, not permission to fabricate missing evidence."""

    prepared_by: str = Field(min_length=1, max_length=100)
    reviewed_by: str = Field(min_length=1, max_length=100)
    reviewed_at: AwareDatetime
    valid_until: AwareDatetime
    approved_inputs_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_evidence_path: str = Field(min_length=1)

    @model_validator(mode="after")
    def independent(self) -> ReleaseReview:
        if self.prepared_by.strip().casefold() == self.reviewed_by.strip().casefold():
            raise ValueError("RELEASE_INDEPENDENT_REVIEW_REQUIRED")
        if self.valid_until <= self.reviewed_at:
            raise ValueError("RELEASE_REVIEW_INTERVAL_INVALID")
        return self


class BoundedDiagnostics(io.StringIO):
    """Native progress output cannot exhaust operator memory or spill to disk."""

    def __init__(self) -> None:
        super().__init__()
        self.byte_count = 0

    def write(self, value: str) -> int:
        self.byte_count += len(value.encode())
        if self.byte_count > MAXIMUM_BYTES:
            raise ValueError("QUALIFICATION_DIAGNOSTICS_LIMIT")
        return super().write(value)


def _execute(
    ctx: QualificationContext,
    name: str,
    operation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Continue independent steps after failure, never serialize exception values."""
    ctx.now = utc_now()
    print(f"Qualifying {name}...", flush=True)
    output = BoundedDiagnostics()
    try:
        with redirect_stdout(output), redirect_stderr(output):
            result = operation()
        diagnostics = output.getvalue().encode()
        if diagnostics:
            ctx.check_secrets(diagnostics)
            ctx.write_bytes(f"outputs/{name}-diagnostics.txt", diagnostics)
        if name == "research-universe" or (
            name == "providers" and ctx.read_json("state/bulk-universe-mode.json") is not None
        ):
            from money.qualification.universe import _large_write

            _large_write(ctx, f"outputs/{name}-result.json", json_bytes(result))
        else:
            ctx.write_json(f"outputs/{name}-result.json", result)
        return result
    except Exception:
        # Pydantic input values, HTTP URLs, subprocess logs and tracebacks are
        # deliberately excluded from status. A step's explicit codes are kept.
        ctx.block(
            f"{name.upper().replace('-', '_')}_FAILED",
            f"Review the {name} inputs and prerequisites, then rerun the same command. No raw exception was printed or saved.",
        )
        return {}
    finally:
        output.close()


def _artifacts(ctx: QualificationContext) -> tuple[tuple[str, str], ...]:
    """Inventory and reread actual bytes, including provider machinery's output."""
    directory = ctx.root / "artifacts"
    if directory.is_symlink():
        raise ValueError("QUALIFICATION_PATH_UNSAFE")
    if directory.exists():
        for path in sorted(directory.iterdir()):
            if not re.fullmatch(r"[a-f0-9]{64}\.(json|bin)", path.name):
                raise ValueError("QUALIFICATION_ARTIFACT_NAME_INVALID")
            ctx.verify_artifact(path.stem, f"artifacts/{path.name}")
    return tuple(sorted(ctx.artifact_refs))


def assemble_manifest(
    ctx: QualificationContext, outputs: Sequence[dict[str, Any]]
) -> tuple[Path, str] | None:
    """Assemble only successful stage fields; never accept an operator manifest."""
    if usage_mode(ctx.environ) == UsageMode.PERSONAL_RESEARCH:
        ctx.block(
            "PERSONAL_RESEARCH_COMMERCIAL_RELEASE_FORBIDDEN",
            "Personal research does not create a production manifest or grant public display, "
            "redistribution or commercial rights. A separate hosted-commercial run requires "
            "reviewed provider rights and every existing release requirement.",
        )
        return None
    fields: dict[str, Any] = {"qlib_enabled": qlib_enabled(ctx.environ)}
    for output in outputs:
        for key, value in output.get("manifest_fields", {}).items():
            if key in fields and fields[key] != value:
                raise ValueError("QUALIFICATION_STAGE_FIELD_CONFLICT")
            fields[key] = value
    # Provider stage also exposes data-only inputs for snapshot qualification.
    providers = outputs[0] if outputs else {}
    for key in ("instruments", "provider_qualifications", "filing_document_storage_hosts", "issuer_source_policy"):
        if key in providers:
            fields[key] = providers[key]
    instruments = fields.pop("instruments", [])
    if instruments:
        fields["instruments"] = []
        fields["instrument_catalog"] = ctx.artifact(instruments)
    if "qualified_universe" in providers:
        from money.qualification.universe_catalog import eligibility_catalogs

        # An empty, honestly unresolved universe is an upstream prerequisite,
        # not an artifact serializer failure. Never call the nonempty admission
        # contract with [] and mislabel it BUNDLE_OR_ACCEPTANCE_FAILED.
        fields["eligibility_catalogs"] = (
            eligibility_catalogs(ctx, providers["qualified_universe"])
            if providers["qualified_universe"] else ()
        )
        fields["universe_account_binding_sha256"] = providers.get("universe_account_binding_sha256")
        fields["universe_provenance"] = providers.get("universe_provenance")
    inputs_hash = hashlib.sha256(json_bytes(fields)).hexdigest()
    ctx.write_json(
        "outputs/release-inputs.json", {"inputs_hash": inputs_hash, "manifest_fields": fields}
    )
    ctx.template(
        "reviews/release.json",
        {
            "prepared_by": None,
            "reviewed_by": None,
            "reviewed_at": None,
            "valid_until": None,
            "approved_inputs_hash": None,
            "approval_evidence_path": None,
        },
    )
    ctx.template(
        "reviews/release.instructions.json",
        {
            "action": "After all component stages pass, independently review outputs/release-inputs.json and record its exact inputs_hash in release.json. Supply the actual approval bytes, not a claimed hash. Reruns preserve your edits.",
            "schema": ReleaseReview.model_json_schema(),
            "scope": "Bundle qualification only; does not certify hosted acceptance.",
        },
    )
    review_raw = ctx.read_bytes("reviews/release.json")
    try:
        review = ReleaseReview.model_validate_json(review_raw or b"null")
        evidence = ctx.read_bytes(review.approval_evidence_path)
        if (
            not review.reviewed_at <= ctx.now < review.valid_until
            or review.approved_inputs_hash != inputs_hash
            or not evidence
        ):
            raise ValueError("RELEASE_REVIEW_INVALID")
    except (ValueError, TypeError):
        ctx.block(
            "RELEASE_APPROVAL_REQUIRED",
            "When component qualification passes, independently review outputs/release-inputs.json and complete reviews/release.json with real approval evidence.",
        )
        return None
    if ctx.blockers:
        return None
    ctx.artifact(evidence)
    ctx.artifact(review_raw)
    fields["reviewed_by"] = review.reviewed_by
    fields["qualification_artifacts"] = _artifacts(ctx)
    manifest = LiveManifest.model_validate(fields)
    for provider in manifest.provider_qualifications:
        for dataset in provider.datasets:
            provider.require(dataset, ctx.now)
    payload = manifest.model_dump(mode="json")
    if not manifest.qlib_enabled:
        payload.pop("qlib_registry_id", None)
        payload.pop("qlib_artifact_hash", None)
    raw = json_bytes(payload)
    digest = hashlib.sha256(raw).hexdigest()
    # Validate a staging filename first. A failed admission never replaces the
    # last manifest; all referenced artifact bytes are already inventoried.
    ctx.write_bytes("manifest.pending.json", raw)
    admitted = load_manifest(ctx.root / "manifest.pending.json", digest)
    if not admitted.reviewed_instruments:
        raise ValueError("QUALIFICATION_CATALOGUE_EMPTY")
    ctx.write_bytes("manifest.json", raw)
    load_manifest(ctx.root / "manifest.json", digest)
    return ctx.root / "manifest.json", digest


def production_environment(
    ctx: QualificationContext,
    manifest_path: Path,
    digest: str,
    outputs: Sequence[dict[str, Any]],
) -> dict[str, str]:
    """Legacy acceptance aliases live only in subprocess memory, never JSON."""
    environ = dict(ctx.environ)
    environ["MONEY_QLIB_ENABLED"] = "true" if qlib_enabled(ctx.environ) else "false"
    if not qlib_enabled(ctx.environ):
        environ.pop("MONEY_QLIB_QUALIFIED_MODEL", None)
        environ.pop("MONEY_QLIB_ARTIFACT_HASH", None)
    environ.update(
        {
            "MONEY_RUN_PRODUCTION_INTEGRATION": "1",
            "MONEY_LIVE_MANIFEST": str(manifest_path),
            "MONEY_LIVE_MANIFEST_SHA256": digest,
            "TRADING212_METADATA_API_KEY": environ.get("TRADING212_API_KEY", ""),
            "TRADING212_METADATA_API_SECRET": environ.get("TRADING212_API_SECRET", ""),
        }
    )
    allowed = {
        "MONEY_NATIVE_QUALIFICATION_SNAPSHOT",
        "MONEY_NATIVE_INFERENCE_CONFIG",
        "MONEY_NATIVE_QUALIFICATION_REPORTS",
        "MONEY_NATIVE_QUALIFICATION_LEAN_REPORT",
        "MONEY_QLIB_ARTIFACT_HASH",
        "MONEY_QLIB_QUALIFIED_MODEL",
        "MONEY_LEAN_QUALIFICATION_CONFIG",
    }
    for output in outputs:
        for name, value in output.get("production_environment", {}).items():
            if name not in allowed or not isinstance(value, str):
                raise ValueError("QUALIFICATION_CHILD_ENVIRONMENT_INVALID")
            if not qlib_enabled(ctx.environ) and name in {
                "MONEY_QLIB_QUALIFIED_MODEL",
                "MONEY_QLIB_ARTIFACT_HASH",
            }:
                raise ValueError("QLIB_DISABLED_CANNOT_REFERENCE_MODEL")
            ctx.check_secrets(value.encode())
            environ[name] = value if name == "MONEY_QLIB_ARTIFACT_HASH" else str(ctx.root / value)
    # Do not forward an unrelated OpenAI secret to another provider (especially
    # anonymous local inference). Legacy aliases are child-only and selected.
    environ.pop("MONEY_NATIVE_INFERENCE_API_KEY", None)
    selections = [
        output.get("manifest_fields", {}).get(role)
        for output in outputs
        for role in ("tradingagents", "ai_hedge_fund", "crewai")
        if role in output.get("manifest_fields", {})
    ]
    if selections:
        chosen = [InferenceSelection.model_validate(item) for item in selections]
        if all(item == chosen[0] for item in chosen):
            variable = chosen[0].credential_environment_variable
            if chosen[0].authentication != "none" and variable:
                secret = ctx.environ.get(variable)
                if secret:
                    environ["MONEY_NATIVE_INFERENCE_API_KEY"] = secret
    return environ


def validate_and_test(
    ctx: QualificationContext,
    path: Path,
    digest: str,
    outputs: Sequence[dict[str, Any]],
) -> bool:
    environ = production_environment(ctx, path, digest, outputs)
    # The explicit suite cannot inherit a developer's -k/-m or skip plugin.
    environ.pop("PYTEST_ADDOPTS", None)
    environ.pop("PYTEST_PLUGINS", None)
    validation = run_captured(
        [
            "uv",
            "run",
            "python",
            "scripts/validate_live.py",
            "--manifest",
            str(path),
            "--sha256",
            digest,
        ],
        cwd=ctx.repo,
        environ=environ,
    )
    ctx.check_secrets(validation.output)
    if validation.returncode != 0:
        ctx.block(
            "MANIFEST_VALIDATION_FAILED",
            "Correct the qualification inputs rejected by the existing validate_live.py; production gates have not been changed.",
        )
        return False
    ctx.write_json(
        "outputs/manifest-validation.json",
        {
            "manifest_sha256": digest,
            "returncode": validation.returncode,
            "scope": "OFFLINE_VALIDATION_ONLY",
        },
    )
    # JUnit may contain arbitrary assertion locals. A child plugin sends only
    # aggregate counts through stdout, never writes raw reports to disk.
    environ["MONEY_QUALIFICATION_TEST_SUMMARY"] = "1"
    tests = run_captured(
        [
            "uv",
            "run",
            "pytest",
            "tests/production",
            "-q",
            "-p",
            "money.qualification.pytest_summary",
        ],
        cwd=ctx.repo,
        environ=environ,
        timeout=7200,
    )
    ctx.check_secrets(tests.output)
    summaries = re.findall(rb"MONEY_QUALIFICATION_TEST_COUNTS=(\{[^\n]+\})", tests.output)
    if len(summaries) != 1:
        ctx.block(
            "PRODUCTION_TEST_REPORT_MISSING",
            "Resolve production pytest startup/reporting failure and rerun; no success has been inferred from its exit code.",
        )
        return False
    counts = json.loads(summaries[0])
    ctx.write_json(
        "outputs/production-tests.json",
        {"manifest_sha256": digest, "returncode": tests.returncode, **counts},
    )
    successful = (
        tests.returncode == 0
        and counts.get("passed", 0) > 0
        and counts.get("collected", 0) == counts.get("passed", 0)
        and not any(
            counts.get(name, 0)
            for name in ("skipped", "failed", "errors", "xfailed", "xpassed", "deselected")
        )
    )
    if not successful:
        ctx.block(
            "PRODUCTION_INTEGRATION_NOT_PASSED",
            "Resolve failures, skips or missing tests shown by outputs/production-tests.json, then rerun. Skips are not success.",
        )
    return successful


def run(ctx: QualificationContext) -> dict[str, Any]:
    if usage_mode(ctx.environ) == UsageMode.PERSONAL_RESEARCH:
        from money.qualification.research_testing import run_research_testing

        return run_research_testing(ctx)
    from money.qualification.native import (
        run_cio_stage,
        run_first_pass_stage,
        run_inference_stage,
        run_native_preflight_stage,
    )
    from money.qualification.providers import run_provider_stages
    from money.qualification.quant import (
        prepare_lean_audit_inputs,
        prepare_lean_inputs,
        run_lean_stage,
        run_qlib_stage,
    )

    enabled = qlib_enabled(ctx.environ)
    selected_usage = usage_mode(ctx.environ)
    ctx.write_json("outputs/research-mode.json", {
        "qlib_enabled": enabled,
        "usage_mode": selected_usage,
        "issuer_source_policy": issuer_source_policy(ctx.environ),
        "usage_policy_version": USAGE_POLICY_VERSION,
        "commercial_release_permitted": selected_usage != UsageMode.PERSONAL_RESEARCH,
        "raw_data_redistribution_permitted": False,
    })
    for name, value in EXPECTED_ENVIRONMENT.items():
        if ctx.environ.get(name, "").lower() != value:
            ctx.block(
                "PRODUCTION_CONFIGURATION_REQUIRED",
                "Production bundle completion requires production/hosted/live with synthetic demo disabled. Local inference probes can run without satisfying this gate; the runner will not change production settings.",
            )
    # New operator runs always discover the full universe. Existing individual
    # review-preparation files are audit history, never a selection list.
    from money.qualification.universe_policy import UNIVERSE_POLICY_VERSION

    ctx.template(
        "state/bulk-universe-mode.json", {"universe_policy_version": UNIVERSE_POLICY_VERSION}
    )
    providers = _execute(ctx, "providers", lambda: run_provider_stages(ctx))
    inference = _execute(ctx, "inference", lambda: run_inference_stage(ctx))
    preflight = _execute(ctx, "native-preflight", lambda: run_native_preflight_stage(ctx))
    qlib = _execute(ctx, "qlib", lambda: run_qlib_stage(ctx))
    prepare_lean_inputs(ctx)
    snapshot = _execute(ctx, "snapshot", lambda: run_snapshot_stage(ctx, providers))
    if snapshot.get("complete") is True:
        prepare_lean_audit_inputs(
            ctx, ResearchSnapshot.model_validate(ctx.read_json("outputs/snapshot.json"))
        )
    # Successful files from a previous invocation are not permission to execute
    # after a current withdrawal, failed provider refresh or failed host audit.
    prerequisites = (inference, preflight, qlib, snapshot)
    firms: dict[str, Any] = {"complete": False}
    lean: dict[str, Any] = {"complete": False}
    cio: dict[str, Any] = {"complete": False}
    if all(result.get("complete") is True for result in prerequisites):
        firms = _execute(ctx, "first-pass", lambda: run_first_pass_stage(ctx))
    else:
        ctx.block(
            "FIRST_PASS_CURRENT_PREREQUISITES_REQUIRED",
            "Resolve this run's inference, native/security/egress, enabled quantitative stages and frozen-evidence blockers before any paid native first-pass execution.",
        )
    prepare_lean_inputs(ctx)
    if firms.get("complete") is True:
        lean = _execute(ctx, "lean", lambda: run_lean_stage(ctx))
    else:
        ctx.block(
            "LEAN_FIRST_PASS_REQUIRED",
            "Review reviews/lean-inputs.json; execution waits for every report selected by the frozen snapshot mode.",
        )
    if lean.get("complete") is True:
        cio = _execute(ctx, "cio", lambda: run_cio_stage(ctx))
    else:
        ctx.block(
            "CIO_LOCKED_REPORTS_AND_LEAN_REQUIRED",
            "CrewAI CIO/Red Team waits for this run's validated first-pass lock and genuine LEAN result.",
        )
    outputs = [providers, inference, preflight, qlib, snapshot, firms, lean, cio]
    for name, result in (
        ("providers", providers),
        ("inference", inference),
        ("native-preflight", preflight),
        ("qlib", qlib),
        ("first-pass", firms),
        ("lean", lean),
        ("cio", cio),
    ):
        if result.get("complete") is not True and not ctx.blockers:
            ctx.block(
                "QUALIFICATION_STAGE_INCOMPLETE",
                f"The {name} stage did not provide successful runtime evidence; review its generated inputs and rerun.",
            )
    assembled: tuple[Path, str] | None = None
    try:
        ctx.now = utc_now()
        assembled = assemble_manifest(ctx, outputs)
        if assembled and validate_and_test(ctx, *assembled, outputs):
            report: dict[str, Any] = {
                "status": "QUALIFICATION COMPLETE",
                "manifest_path": str(assembled[0]),
                "manifest_sha256": assembled[1],
                "blockers": [],
                "hosted_acceptance": "NOT_RUN",
                "production_ready": False,
                "updated_at": ctx.now.isoformat(),
                "last_update_stage": "qualification-runner",
                "universe_provenance": providers.get("universe_provenance"),
            }
            ctx.write_json("status.json", report)
            from money.qualification.diagnostics import write_qualification_diagnostics

            write_qualification_diagnostics(ctx, report=report)
            return report
    except Exception:
        ctx.block(
            "BUNDLE_OR_ACCEPTANCE_FAILED",
            "Resolve schema/artifact/acceptance prerequisites and rerun; no manifest success is being claimed.",
        )
    report = {
        "status": "QUALIFICATION BLOCKED",
        "blockers": ctx.blockers,
        "manifest_sha256": assembled[1] if assembled else None,
        "production_ready": False,
        "updated_at": ctx.now.isoformat(),
        "last_update_stage": "qualification-runner",
        "universe_provenance": providers.get("universe_provenance"),
        "downstream_stage_results_current": True,
    }
    ctx.write_json("status.json", report)
    from money.qualification.diagnostics import write_qualification_diagnostics

    write_qualification_diagnostics(ctx, report=report)
    return report


def main(argv: Sequence[str] | None = None, *, environment: Mapping[str, str] | None = None) -> int:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        environ = dict(os.environ if environment is None else environment)
        repo = Path(__file__).resolve().parents[3]
        output = args.output
        if output is None:
            local_inference = bool(environ.get("MONEY_INFERENCE_CONFIG")) and any(
                selection.is_local
                for selection in load_inference_selections(repo, environ).values()
            )
            # Local experiments must not overwrite production review templates,
            # status or the last admitted bundle simply by selecting a config.
            output = Path(
                "data/qualified/local-inference" if local_inference else "data/qualified/live"
            )
        ctx = QualificationContext(
            root=output,
            repo=repo,
            environ=environ,
            now=utc_now(),
        )
        with ctx.locked():
            report = run(ctx)
        print(report["status"])
        if report["status"] == "RESEARCH RUN COMPLETE":
            print("Personal research only. No production manifest, release or trade approval.")
            return 0
        if report["status"] == "QUALIFICATION COMPLETE":
            print("MONEY_LIVE_MANIFEST=/app/data/qualified/live/manifest.json")
            print(f"MONEY_LIVE_MANIFEST_SHA256={report['manifest_sha256']}")
            print("Hosted end-to-end acceptance remains a separate deployment requirement.")
            return 0
        for blocker in report["blockers"]:
            print(f"- {blocker['code']}: {blocker['action']}")
        print(f"Review inputs and status: {ctx.root}. Rerun this same command to resume.")
        return 2
    except Exception:
        print("QUALIFICATION BLOCKED")
        print(
            "- RUNNER_INITIALIZATION_FAILED: Check the output directory, exclusive runner lock, and installed Money dependencies. No raw exception or credential was printed."
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
