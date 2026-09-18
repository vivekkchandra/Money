"""Independent native research for the explicit local, non-release workflow.

Company enrichment is not an admission requirement here. The exact upstream
source, installed dependency/security audit, immutable snapshot and bounded
native capabilities still are. Python process restrictions are not represented
as hosted OS/container egress qualification.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from money.adapters.native import NativeRunSettings
from money.adapters.native_process import (
    NativeProcessPolicy,
    native_failure_code,
)
from money.adapters.native_subprocess import IsolatedNativeRunner, bridge_fingerprint
from money.adapters.upstream import (
    AIHedgeFundAdapter,
    InvalidUpstreamReport,
    TradingAgentsAdapter,
    _validate_report,
)
from money.qualification.core import QualificationContext
from money.research.call_telemetry import InferenceReceipt, capture_calls, emit_calls
from money.research.inference_config import InferenceSelection
from money.research.native_environments import prepare_native_environments
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    FirmReport,
    ResearchMandate,
    ResearchSnapshot,
    TradingAgentsResearchReport,
    content_hash,
    utc_now,
)

SCOPE = "LOCAL_RESEARCH_TESTING"
_FIRMS = ("tradingagents", "ai_hedge_fund")
_SCHEMAS: dict[str, type[FirmReport]] = {
    "tradingagents": TradingAgentsResearchReport,
    "ai_hedge_fund": AIHedgeFundResearchReport,
}


def _failure_code(error: Exception) -> str:
    """Expose only fixed diagnostic categories, never provider exception text."""
    return native_failure_code(error)


def run_local_native_preflight(ctx: QualificationContext) -> dict[str, Any]:
    """Provision and audit two isolated closures, never the shared Money stack."""
    try:
        prepared = prepare_native_environments(ctx)
    except Exception:
        ctx.block(
            "LOCAL_NATIVE_ENVIRONMENT_CONFIGURATION_REQUIRED",
            "The pinned native environment definition could not be verified. Check "
            "data/configuration/native-runtimes/engines.json and UPSTREAM_LOCK.txt; "
            "no exception text or credential was recorded.",
        )
        prepared = {
            "complete": False, "runtimes": {}, "pins": {}, "artifacts": [],
            "security": {"passed": False, "findings": [], "artifacts": []},
            "execution_identity": None,
        }
    for firm, runtime in prepared["runtimes"].items():
        if runtime.get("complete") is True:
            continue
        errors = runtime.get("errors") or ["NATIVE_ENVIRONMENT_UNAVAILABLE"]
        security_failure = any("SECURITY" in code for code in errors)
        code = (
            "LOCAL_NATIVE_DEPENDENCY_SECURITY_REQUIRED" if security_failure
            else "LOCAL_NATIVE_ENVIRONMENT_REQUIRED_" + firm.upper()
        )
        ctx.block(
            code,
            firm + ": " + ", ".join(errors) + ". See outputs/native-environments/"
            + firm + ".json for source, lock, import and unsuppressed audit evidence. "
            "Rerun the same research command to resume automatic setup; no shared-environment "
            "install, source repin or advisory suppression is permitted.",
        )
    bridge_hash = bridge_fingerprint(ctx.repo)
    result = {
        **prepared,
        "scope": SCOPE,
        "verified_at": ctx.now.isoformat(),
        "bridge_sha256": bridge_hash,
        "execution_identity": content_hash({
            "runtimes": prepared["execution_identity"], "bridge_sha256": bridge_hash,
        }),
        "qlib_enabled": False,
        "hosted_egress_qualified": False,
        "production_qualified": False,
    }
    ctx.write_json("outputs/research-native-preflight.json", result)
    return result


def _validate_snapshot(ctx: QualificationContext, snapshot: ResearchSnapshot) -> ResearchSnapshot:
    # Revalidation rejects model_construct/model_copy changes and verifies every hash.
    frozen = ResearchSnapshot.model_validate_json(snapshot.model_dump_json())
    if (
        frozen.purpose != "RESEARCH_TESTING"
        or frozen.usage_mode != "PERSONAL_RESEARCH"
        or frozen.qlib_enabled
        or not frozen.universe_hash
    ):
        raise ValueError("LOCAL_RESEARCH_SNAPSHOT_PURPOSE_REQUIRED")
    instrument = frozen.instrument
    if (
        instrument.provider != "trading212"
        or instrument.instrument_type != "STOCK"
        or instrument.quote_currency not in {"GBP", "GBX"}
        or instrument.currently_available is not True
        or not instrument.source_id.strip()
        or not instrument.verified_at <= frozen.created_at <= ctx.now
        or not ctx.now < instrument.verified_at + timedelta(hours=24)
    ):
        raise ValueError("LOCAL_RESEARCH_CURRENT_BROKER_MEMBERSHIP_REQUIRED")
    if any(
        record.provider in {"money-demo", "test", "synthetic"}
        or record.retrieval_time > ctx.now
        or (record.critical and (record.fresh_until <= ctx.now or record.conflicting))
        or (frozen.historical and not record.available_at(frozen.cutoff_for(record)))
        for record in frozen.evidence
    ):
        raise ValueError("LOCAL_RESEARCH_EVIDENCE_UNAVAILABLE")
    _validate_broker_binding(ctx, frozen)
    return frozen


def _validate_broker_binding(ctx: QualificationContext, snapshot: ResearchSnapshot) -> None:
    """Bind native execution to all actual frozen broker members and raw bytes.

    A hash-shaped string, invented metadata record, edited projection or partial
    universe is not sufficient. No optional company data participates here.
    """
    from money.qualification.universe import _restore_response
    from money.qualification.universe_admission import current_research_rows
    from money.qualification.universe_normalize import normalize_universe
    from money.qualification.universe_policy import UNIVERSE_POLICY_VERSION

    try:
        if snapshot.universe_hash is None:
            raise ValueError("FROZEN_UNIVERSE_REQUIRED")
        frozen = json.loads(ctx.verify_artifact(
            snapshot.universe_hash, "artifacts/" + str(snapshot.universe_hash) + ".json",
        ))
        reference = frozen["source_provenance"]
        provenance = json.loads(ctx.verify_artifact(*reference))
        observed = datetime.fromisoformat(provenance["retrieved_at"])
        if (
            frozen.get("purpose") != "RESEARCH_TESTING"
            or frozen.get("universe_policy_version") != UNIVERSE_POLICY_VERSION
            or frozen.get("production_qualified") is not False
            or not observed <= datetime.fromisoformat(frozen["frozen_at"]) <= snapshot.created_at
        ):
            raise ValueError("FROZEN_UNIVERSE_INVALID")
        _, raw = _restore_response(ctx, provenance["response_artifacts"]["instruments"])
        master = {
            "universe_policy_version": UNIVERSE_POLICY_VERSION,
            "scope": provenance["scope"], "status": "REFRESHED",
            "provenance": provenance, "universe_provenance": reference,
            "stocks": normalize_universe(raw, (), observed_at=observed),
        }
        admitted = current_research_rows(ctx, master)
        members = [
            {key: row.get(key) for key in (
                "trading212_id", "isin", "name", "quote_currency", "instrument_type",
                "instrument_row_sha256", "observed_at", "valid_until",
            )}
            for row in sorted(admitted, key=lambda item: item["trading212_id"])
        ]
        if frozen["members"] != members:
            raise ValueError("FROZEN_COMPLETE_UNIVERSE_MISMATCH")
        member = next(row for row in members if row["trading212_id"] == snapshot.instrument.source_id)
        if (
            snapshot.instrument.quote_currency != member["quote_currency"]
            or snapshot.instrument.verified_at != observed
            or snapshot.instrument.company != (member["name"] or member["trading212_id"])
        ):
            raise ValueError("FROZEN_SELECTED_IDENTITY_MISMATCH")
        metadata = [record for record in snapshot.evidence if record.provider == "trading212"]
        if len(metadata) != 1:
            raise ValueError("FROZEN_METADATA_RECORD_REQUIRED")
        record = metadata[0]
        if (
            record.payload.kind != "instrument_metadata"
            or record.source_id != member["trading212_id"]
            or record.evidence_id != "t212:" + cast(str, member["instrument_row_sha256"])
            or record.observation_time != observed
            or record.retrieval_time != observed
            or record.fresh_until != datetime.fromisoformat(cast(str, member["valid_until"]))
            or json.loads(record.payload.excerpt) != member
        ):
            raise ValueError("FROZEN_METADATA_RECORD_MISMATCH")
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration, OSError) as error:
        raise ValueError("LOCAL_RESEARCH_AUTHENTICATED_UNIVERSE_BINDING_REQUIRED") from error


def _report(
    ctx: QualificationContext,
    firm: str,
    snapshot: ResearchSnapshot,
    selection: InferenceSelection,
    settings: NativeRunSettings,
    runtime: Mapping[str, Any],
    bridge_hash: str,
) -> FirmReport:
    if runtime.get("complete") is not True or runtime.get("security", {}).get("passed") is not True:
        raise ValueError("LOCAL_NATIVE_AUDITED_ENVIRONMENT_REQUIRED")
    transport = selection.inference(ctx.environ)
    adapter_type = TradingAgentsAdapter if firm == "tradingagents" else AIHedgeFundAdapter
    runner = IsolatedNativeRunner(
        interpreter=Path(runtime["python"]),
        role=cast(Literal["tradingagents", "ai_hedge_fund"], firm),
        selection=selection,
        settings=settings,
        policy=NativeProcessPolicy(
            gateway_hosts=transport.allowed_network_hosts,
            gateway_port=transport.allowed_network_port,
            timeout_seconds=settings.timeout_seconds,
        ),
        credential=transport.configuration.api_key,
        repository=ctx.repo,
        expected_python_version=runtime["python_version"],
        expected_inventory_sha256=runtime["inventory_sha256"],
        expected_bridge_sha256=bridge_hash,
        expected_environment_sha256=runtime["environment_sha256"],
    )
    return adapter_type(cast(Any, runner)).research(
        ResearchMandate(quote_currencies=("GBP", "GBX")), snapshot
    )


def compare_independent_reports(
    snapshot: ResearchSnapshot, reports: Sequence[FirmReport]
) -> dict[str, Any]:
    """Compare sealed claims without inventing semantic agreement or confidence.

    This is an auditable, deterministic aggregation/debate input, not a claim
    that the native CIO, Red Team or LEAN executed.
    """
    if len(reports) != 2 or {report.firm for report in reports} != set(_FIRMS):
        raise ValueError("LOCAL_RESEARCH_BOTH_INDEPENDENT_REPORTS_REQUIRED")
    validated: dict[str, FirmReport] = {
        report.firm: _validate_report(report, snapshot, _SCHEMAS[report.firm], report.firm)
        for report in reports
    }
    left, right = (validated[firm] for firm in _FIRMS)
    agreements: list[dict[str, Any]] = []
    differences: list[dict[str, Any]] = []
    for first in left.claims:
        for second in right.claims:
            common = sorted(set(first.evidence_ids) & set(second.evidence_ids))
            if first.family != second.family or not common:
                continue
            pair = {
                "tradingagents_claim_id": first.claim_id,
                "ai_hedge_fund_claim_id": second.claim_id,
                "shared_evidence_ids": common,
                "tradingagents_statement": first.statement,
                "ai_hedge_fund_statement": second.statement,
            }
            if " ".join(first.statement.casefold().split()) == " ".join(
                second.statement.casefold().split()
            ):
                agreements.append(pair)
            else:
                differences.append(
                    pair | {"assessment": "DIFFERENT_INTERPRETATIONS_NOT_VERIFIED_CONTRADICTION"}
                )
    return {
        "scope": SCOPE,
        "status": "LIMITED_COMPARISON",
        "snapshot_hash": snapshot.hash,
        "methodology": "Exact normalized claims and shared cited evidence, never confidence averaging.",
        "report_hashes": {firm: content_hash(report) for firm, report in validated.items()},
        "agreements": agreements,
        "differences_requiring_assessment": differences,
        "missing_data": list(snapshot.missing_data),
        "confidence": "NOT_AGGREGATED",
        "thesis_robustness": "NOT_ESTABLISHED_BY_TEXT_COMPARISON",
        "cio_completed": False,
        "red_team_completed": False,
        "lean_validated": False,
        "production_qualified": False,
    }


def run_local_independent_research(
    ctx: QualificationContext,
    snapshot: ResearchSnapshot,
    selections: Mapping[str, InferenceSelection],
    settings: NativeRunSettings | None = None,
) -> dict[str, Any]:
    """Run two real native firms independently, resuming only verified sealed bytes."""
    frozen = _validate_snapshot(ctx, snapshot)
    settings = settings or NativeRunSettings()
    if not settings.verify_source_pin:
        raise ValueError("LOCAL_NATIVE_SOURCE_ATTESTATION_REQUIRED")
    if any(firm not in selections or not selections[firm].is_local for firm in _FIRMS):
        raise ValueError("PERSONAL_RESEARCH_LOCAL_INFERENCE_REQUIRED")
    preflight = run_local_native_preflight(ctx)
    result: dict[str, Any] = {
        "scope": SCOPE,
        "complete": False,
        "state": "RESEARCH_ELIGIBLE",
        "snapshot_hash": frozen.hash,
        "qlib_enabled": False,
        "production_qualified": False,
        "reports": [],
        "artifacts": [],
        "firm_failures": [],
        "firm_runs": [],
        "execution_limits": {
            "agent_timeout_seconds": settings.timeout_seconds,
            "native_max_calls": settings.max_calls,
            "inference_timeout_seconds": {
                firm: selections[firm].timeout_seconds for firm in _FIRMS
            },
        },
        "debate_completed": False,
    }
    if not preflight["complete"]:
        ctx.write_json("outputs/research-first-pass.json", result)
        return result
    # Provisioning may take minutes. Recheck the unchanged broker/evidence
    # expiry at actual execution time; never extend membership to fit setup.
    ctx.now = max(ctx.now, utc_now())
    frozen = _validate_snapshot(ctx, frozen)
    identity = content_hash(
        {
            "snapshot_hash": frozen.hash,
            "runtime": preflight["execution_identity"],
            # A fresh passing audit is required before this lookup. Its receipt
            # timestamp does not change the source/dependencies that made an
            # existing independent report, so it is not part of the cache key.
            "selections": {firm: selections[firm].model_dump(mode="json") for firm in _FIRMS},
            "limits": vars(settings),
        }
    )
    reports: list[FirmReport] = []
    references: list[tuple[str, str]] = []
    for firm in _FIRMS:
        started = time.monotonic()
        calls: list[InferenceReceipt] = []
        cache_hit = False
        failure: str | None = None
        try:
            name = "local-research-firm-" + firm
            cached = ctx.cache(name, identity, 3600)
            if cached is not None:
                cache_hit = True
                reference = tuple(cached["artifact"])
                raw = ctx.verify_artifact(*reference)
                report = _SCHEMAS[firm].model_validate_json(raw)
            else:
                # No peer report is passed or visible in either firm-private capability.
                try:
                    with capture_calls() as calls:
                        report = _report(
                            ctx, firm, frozen, selections[firm], settings,
                            preflight["runtimes"][firm], preflight["bridge_sha256"],
                        )
                finally:
                    emit_calls(calls)
            report = _validate_report(report, frozen, _SCHEMAS[firm], firm)
            if report.created_at > ctx.now + timedelta(seconds=settings.timeout_seconds * 2):
                raise InvalidUpstreamReport("native report time is outside this bounded run")
            ctx.check_secrets(report.model_dump_json().encode())
            reference = ctx.artifact(report.model_dump_json().encode())
            ctx.checkpoint(name, identity, {"artifact": reference}, artifacts=(reference,))
            ctx.write_json("outputs/research-reports/" + firm + ".json", report.model_dump(mode="json"))
            reports.append(report)
            references.append(reference)
        except Exception as error:
            # Raw provider/native exception text may contain secrets or untrusted content.
            code = _failure_code(error)
            failure = code
            result["firm_failures"].append({"firm": firm, "code": code})
            ctx.block(
                "LOCAL_NATIVE_REPORT_REQUIRED_" + firm.upper(),
                code + ": the bounded native firm did not return a valid, pinned, evidence-cited report. "
                + (
                    f"Whole-firm budget {settings.timeout_seconds:g}s exhausted; "
                    f"individual model timeout remains {selections[firm].timeout_seconds}s. "
                    "Inspect outputs/research-first-pass.json firm_runs; incomplete accounting "
                    "does not mean no LLM calls occurred. The local workflow budget is "
                    "MONEY_RESEARCH_AGENT_TIMEOUT_SECONDS (maximum 1800). "
                    if code == "NATIVE_AGENT_TIMEOUT" else
                    "Check the reported failure category and local model/runtime diagnostics. "
                )
                + "Peer reports remain sealed.",
            )
        finally:
            result["firm_runs"].append({
                "firm": firm, "status": "FAILED" if failure else "SUCCEEDED",
                "duration_seconds": max(0.0, time.monotonic() - started),
                "cache_hit": cache_hit, "error_code": failure,
                "agent_timeout_seconds": settings.timeout_seconds,
                "inference_timeout_seconds": selections[firm].timeout_seconds,
                "llm_calls_recorded": sum(call.provider_calls for call in calls),
                # Killing an agent may prevent its in-flight receipts returning.
                "call_accounting_complete": failure not in {
                    "NATIVE_AGENT_TIMEOUT", "NATIVE_SUBPROCESS_FAILED",
                },
                "calls": [call.model_dump(mode="json") for call in calls],
            })
    result["reports"] = [report.model_dump(mode="json") for report in reports]
    result["artifacts"] = references
    if len(reports) == 2:
        # Comparison consumes only the completed, independently validated
        # reports. A failed comparison must not leave a first-pass lock behind.
        try:
            comparison = compare_independent_reports(frozen, reports)
            ctx.check_secrets(json.dumps(comparison).encode())
            comparison_reference = ctx.artifact(comparison)
            ctx.write_json("outputs/research-comparison.json", comparison)
        except Exception:
            ctx.block(
                "LOCAL_NATIVE_COMPARISON_REQUIRED",
                "Both original firm reports are preserved, but their comparison did not "
                "validate. Resume this command; no first-pass lock or LEAN run was admitted.",
            )
            ctx.write_json("outputs/research-first-pass.json", result)
            return result
        references.append(comparison_reference)
        seal = {
            "scope": SCOPE,
            "state": "FIRST_PASS_LOCKED",
            "snapshot_hash": frozen.hash,
            "input_identity": identity,
            "qlib_enabled": False,
            "reports": result["reports"],
            "comparison_artifact": comparison_reference,
            "native_preflight_artifact": ctx.artifact(preflight),
            "production_qualified": False,
        }
        references.append(ctx.artifact(seal))
        result.update({"complete": True, "state": "FIRST_PASS_LOCKED", "research_state": "RESEARCHED"})
        result.update({"debate_completed": True, "debate_status": "LIMITED_COMPARISON"})
    ctx.write_json("outputs/research-first-pass.json", result)
    return result
