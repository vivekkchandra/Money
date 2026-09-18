"""Causal work plan from recorded facts, never a gate or fabricated approval."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from money.qualification.core import QualificationContext, fingerprint
from money.qualification.universe_policy import RETIRED_BLOCKERS, UNIVERSE_POLICY_VERSION
from money.qualification.universe_status import live_metadata_state
from money.research.qlib_mode import qlib_enabled
from money.schemas.contracts import utc_now

# Dependency edges describe prerequisites, not unconditional future success.
STAGES = {
    "live_metadata": (),
    "identity": ("live_metadata",),
    "provider_access": (),
    "provider_enrichment": ("identity", "provider_access"),
    "provider_rights": (),
    "ethical_evidence": ("identity", "provider_rights"),
    "eligibility": ("identity", "provider_enrichment", "provider_rights", "ethical_evidence"),
    "supplemental_evidence": ("eligibility", "provider_rights"),
    "snapshot": ("eligibility", "supplemental_evidence"),
    "remote_inference": (),
    "inference_review": ("remote_inference",),
    "native_dependencies": (),
    "native_sources": ("native_dependencies",),
    "host_egress": ("remote_inference", "native_sources"),
    "first_pass": ("snapshot", "inference_review", "native_sources", "host_egress"),
    "lean_evidence": (),
    "lean": ("first_pass", "lean_evidence"),
    "cio_red_team": ("lean", "first_pass"),
    "release": ("cio_red_team",),
    "manifest": ("release",),
    "hosted_acceptance": ("manifest",),
}

BLOCKER_STAGE = {
    "TRADING212_LIVE_METADATA_REQUIRED": "live_metadata",
    "CH_FINANCIAL_DOCUMENT_QUALIFICATION_REQUIRED": "supplemental_evidence",
    "BULK_UNIVERSE_NO_QUALIFIED_MEMBERS": "eligibility",
    "QUALIFIED_INSTRUMENT_EVIDENCE_REQUIRED": "supplemental_evidence",
    "LOCAL_INFERENCE_NOT_HOSTED_QUALIFIED": "remote_inference",
    "REVIEWED_INFERENCE_SELECTIONS_REQUIRED": "inference_review",
    "NATIVE_DEPENDENCY_RESOLUTION_FAILED": "native_dependencies",
    "CHROMADB_SECURITY_ADVISORIES": "native_dependencies",
    "HOST_EGRESS_REMOTE_INFERENCE_REQUIRED": "host_egress",
    "HOST_EGRESS_TARGET_EVIDENCE_REQUIRED": "host_egress",
    "SNAPSHOT_CURRENT_INSTRUMENT_REQUIRED": "snapshot",
    "FIRST_PASS_CURRENT_PREREQUISITES_REQUIRED": "first_pass",
    "LEAN_FIRST_PASS_REQUIRED": "lean",
    "CIO_LOCKED_REPORTS_AND_LEAN_REQUIRED": "cio_red_team",
    "BUNDLE_OR_ACCEPTANCE_FAILED": "manifest",
    "RELEASE_APPROVAL_REQUIRED": "release",
    "QUALIFICATION_STAGE_RESULTS_STALE": "manifest",
}

CATEGORIES = {
    "live_metadata": ["ROOT", "AUTOMATIC_RETRY"],
    "identity": ["ACTIONABLE_NOW"],
    "provider_access": ["ROOT", "EXTERNAL_INFRASTRUCTURE"],
    "provider_enrichment": ["AUTOMATIC_RETRY"],
    "provider_rights": ["ROOT", "HUMAN_REVIEW"],
    "ethical_evidence": ["AUTOMATIC_RETRY"],
    "supplemental_evidence": ["HUMAN_REVIEW", "DOWNSTREAM/DERIVED"],
    "remote_inference": ["ROOT", "EXTERNAL_INFRASTRUCTURE"],
    "inference_review": ["HUMAN_REVIEW"],
    "native_dependencies": ["ROOT", "ACTIONABLE_NOW"],
    "native_sources": ["EXTERNAL_INFRASTRUCTURE"],
    "host_egress": ["EXTERNAL_INFRASTRUCTURE", "HUMAN_REVIEW"],
    "lean_evidence": ["HUMAN_REVIEW", "EXTERNAL_INFRASTRUCTURE"],
    "release": ["HUMAN_REVIEW", "DOWNSTREAM/DERIVED"],
}


def read_master(ctx: QualificationContext) -> tuple[dict[str, Any], str | None]:
    from money.qualification.universe import LEGACY_MASTER, MASTER, MAX_MASTER_BYTES

    raw = ctx.read_bytes(MASTER, MAX_MASTER_BYTES)
    if raw is None:
        # Filename compatibility never makes an old policy's classifications current.
        raw = ctx.read_bytes(LEGACY_MASTER, MAX_MASTER_BYTES)
    if raw is None:
        return {}, None
    master = json.loads(raw)
    if not isinstance(master, dict):
        raise ValueError("UNIVERSE_DIAGNOSTIC_INPUT_INVALID")
    return master, hashlib.sha256(raw).hexdigest()


def write_qualification_diagnostics(
    ctx: QualificationContext,
    *,
    master: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read saved state without network, advancing stages, or mutating approvals."""
    saved, source_hash = read_master(ctx)
    master = saved if master is None else master
    report = report if report is not None else ctx.read_json("status.json") or {}
    # Historical runner reports are audit records. Retired policy requirements
    # are not nodes in the current DAG and are never translated into approvals.
    report = {**report, "blockers": [
        item for item in report.get("blockers", [])
        if item.get("code") not in RETIRED_BLOCKERS
    ]}
    metadata = live_metadata_state(ctx, master)
    summary = master.get("summary") or {}
    progress = ctx.read_json("outputs/universe-enrichment-progress.json") or {}
    mode = ctx.read_json("outputs/research-mode.json") or {}
    recorded_mode = mode.get("qlib_enabled")
    enabled = (
        qlib_enabled(ctx.environ)
        if "MONEY_QLIB_ENABLED" in ctx.environ or type(recorded_mode) is not bool
        else recorded_mode
    )
    stages = dict(STAGES)
    if enabled:
        stages["qlib"] = ("supplemental_evidence",)
        stages["first_pass"] = (*stages["first_pass"], "qlib")
    nodes: list[dict[str, Any]] = []
    ethical_counts = {
        state: sum(
            row.get("ethical_state") == state
            for row in master.get("stocks", []) if row.get("universe_member") is True
        )
        for state in ("PASS", "FAIL", "UNKNOWN", "NOT_YET_SCREENED")
    }
    for name, dependencies in stages.items():
        codes = []
        for item in report.get("blockers", []):
            code = item.get("code", "")
            stage = (
                "native_sources"
                if code.startswith("NATIVE_SOURCE_UNQUALIFIED_")
                else BLOCKER_STAGE.get(code)
            )
            if stage == name:
                codes.append(code)
        nodes.append(
            {
                "id": name,
                "depends_on": list(dependencies),
                "classification": CATEGORIES.get(name, ["DOWNSTREAM/DERIVED"]),
                "reported_blockers": sorted(codes),
                "state": "CURRENT_RETRIEVAL_ONLY"
                if name == "live_metadata" and metadata["current"]
                else "NOT_QUALIFIED_BY_DIAGNOSTICS",
            }
        )
        if name == "ethical_evidence":
            nodes[-1]["screening_counts"] = ethical_counts
            nodes[-1]["classification"] = (
                ["AUTOMATIC_RETRY", "HUMAN_REVIEW"]
                if ethical_counts["UNKNOWN"] else ["AUTOMATIC_RETRY"]
            )
            nodes[-1]["action"] = (
                "Run one machine screening per verified issuer using globally rights-approved evidence. "
                "Reuse PASS; exclude FAIL. Human evidence resolution is only required for UNKNOWN/conflicts, "
                "not a second ethical approval."
            )
    mapped_codes = {code for node in nodes for code in node["reported_blockers"]}
    for item in report.get("blockers", []):
        if item.get("code") not in mapped_codes:
            nodes.append(
                {
                    "id": item["code"],
                    "depends_on": [],
                    "classification": ["ACTIONABLE_NOW"],
                    "reported_blockers": [item["code"]],
                    "state": "REQUIRES_SEPARATE_DIAGNOSIS",
                    "action": item.get("action"),
                }
            )
    report_ref = report.get("universe_provenance")
    stale_report = fingerprint(report_ref) != fingerprint(
        master.get("universe_provenance")
    ) or not report.get("updated_at")
    result = {
        "version": "money-qualification-causality-v2",
        "universe_policy_version": UNIVERSE_POLICY_VERSION,
        "scope": "DIAGNOSTICS_ONLY",
        "generated_at": ctx.now.isoformat(),
        "universe_sha256": source_hash,
        "universe_observed_at": master.get("observed_at"),
        "universe_metadata": metadata,
        "status_source_link_missing_or_changed": stale_report,
        "runner_updated_at": report.get("updated_at"),
        "qlib_enabled": enabled,
        "recorded_qlib_enabled": recorded_mode,
        "requested_mode_matches_recorded": recorded_mode is None or enabled is recorded_mode,
        "lean_mandatory": True,
        "nodes": nodes,
        "edges": [
            {"from": dependency, "to": name}
            for name, dependencies in stages.items()
            for dependency in dependencies
        ],
        "qualification_counts_as_recorded": summary,
        "approval_granted": False,
    }
    ctx.write_json("outputs/qualification-blocker-dag.json", result)
    observed = master.get("observed_at", "not recorded")
    fresh = (
        "current, hash-verified"
        if metadata["current"]
        else "saved live source remains fresh; reclassified offline, not a new authenticated refresh"
        if metadata.get("source_current")
        else "not currently verified (refresh required)"
    )
    lines = [
        "# Next actions — one genuine stock through mandatory LEAN",
        "",
        f"Saved live retrieval: {observed}; {fresh}. Diagnostics prepared {ctx.now.isoformat()}.",
        f"Raw {summary.get('raw_instruments', 'unknown')}; GBX {summary.get('gbx_stocks', 'unknown')}; identity-valid {summary.get('identity_valid', 'unknown')}; qualified {summary.get('qualified', 'unknown')}. These are recorded counts, not new live requests.",
        "",
        "## Smallest legitimate sequence",
        "",
        "1. Resolve EODHD access first: inspect universe-enrichment-progress.json. HTTP 402 search / HTTP 403 fundamentals require the provider to confirm account entitlement, quota and endpoint access; they are not identity mismatches. No suffix guessing or automatic subscription purchase. Then resume bounded enrichment; cached current evidence is reused.",
        "2. Complete global provider-rights reviews in inputs/provider-rights/{eodhd,companies-house}.json, including actual permitted-use/redistribution evidence and ethical_research_datasets when licensed. One provider approval covers all issuers using those datasets; no duplicate per-issuer/source-use review. Existing global source-rights scope supplements remain compatible. API access is not a licence. Account-type and ISA attestations are not requirements; legacy account-scope artifacts are ignored, not approved.",
        "3. Run one machine ethical screening per verified issuer from admissible source bytes via inputs/universe/ethical-evidence.json. Review outputs/ethics-work-queue.json: reuse PASS, exclude FAIL, acquire missing evidence for NOT_YET_SCREENED; human intervention only resolves UNKNOWN/conflicts. No independent second ethical reviewer or repeated downstream screening. Exact shared issuer identities reuse a clearance; similar names do not. Select by evidence completeness, not returns; the complete universe remains in discovery.",
        "4. Rerun the finalizer. One genuinely eligible member is enough; other unresolved members do not veto it. For that member attach the existing SupplementalReview via inputs/universe/supplemental.json: spread/cost/action/PIT/financial sources and up to four relevant accounts filing documents in inputs/financial-documents.json where applicable. Empty dataset samples are not qualifications.",
        "5. In parallel, resolve native dependency/security and pinned-source blockers (outputs/native-blocker-diagnosis.json). Provision a reviewed remote HTTPS inference endpoint for the existing private worker, exact role selections/budgets in reviews/inference.json, and real Linux/container allow/deny enforcement in reviews/native-egress.json. Local Ollama is NOT hosted inference; do not point Railway at Mac loopback or expose Ollama.",
        "6. Run build_live_qualification.py: complete qualified-universe snapshot → bounded screening → independent sealed TradingAgents / AI-Hedge-Fund reports"
        + (
            " plus independently qualified numeric Qlib"
            if enabled
            else " (Qlib explicitly disabled)"
        )
        + " → FIRST_PASS_LOCKED. Disabled Qlib never means promoted/qualified.",
        "7. LEAN remains mandatory: prepare reviews/lean-inputs.json and generated audit inputs with historical eligibility/survivorship, corporate actions, approved costs/slippage, walk-forward/OOS, MAE/MFE/drawdown, pinned image/runtime and genuine execution. A first-pass lock is necessary but not sufficient. Then CIO evidence verification, contradictions, Red Team and at most two cross-examination rounds; independent release review and hosted acceptance remain separate.",
        "",
        "## Causal diagnostics",
        "",
        "BULK_UNIVERSE_NO_QUALIFIED_MEMBERS → SNAPSHOT_CURRENT_INSTRUMENT_REQUIRED → FIRST_PASS_CURRENT_PREREQUISITES_REQUIRED → LEAN_FIRST_PASS_REQUIRED → CIO_LOCKED_REPORTS_AND_LEAN_REQUIRED → bundle/release. These stage-wait messages can clear on rerun after prerequisites genuinely pass; their substantive evidence gates do not disappear.",
        "CH_FINANCIAL_DOCUMENT_QUALIFICATION_REQUIRED and QUALIFIED_INSTRUMENT_EVIDENCE_REQUIRED include genuine human evidence work, but need a resolved instrument. LEAN audit preparation can happen while first-pass execution waits.",
        "A REFRESHED universe is not a qualified universe. A standalone finalizer cannot certify downstream stages. Older status without a source hash/timestamp is historical, not a current live-fetch failure.",
        "",
        "## Resume (normal Mac terminal)",
        "",
        "```sh",
        f"railway run --service Money --environment production sh -c 'MONEY_QLIB_ENABLED={str(enabled).lower()} MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/finalize_live_universe.py'",
        f"railway run --service Money --environment production sh -c 'MONEY_QLIB_ENABLED={str(enabled).lower()} MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/build_live_qualification.py'",
        "```",
        "",
        f"Provider progress is recorded in outputs/universe-enrichment-progress.json (remaining unserviced: {progress.get('remaining_unserviced', 'not computed')}). Budget estimates are lower bounds, not a promise of access or qualification.",
        "No approval, trade, deployment change or production manifest is created by these diagnostics.",
    ]
    selected = ctx.read_json("outputs/first-qualification-candidate.json") or {}
    if (
        selected.get("universe_policy_version") == UNIVERSE_POLICY_VERSION
        and selected.get("source_universe_sha256") == source_hash
        and isinstance(selected.get("next_genuine_blocker"), dict)
    ):
        next_blocker = selected["next_genuine_blocker"]
        lines[5:5] = [
            "## Selected work priority", "",
            f"{selected.get('company')} ({selected.get('trading212_id')}): "
            f"{selected.get('recorded_qualification_state')}. "
            f"Next genuine blocker: {next_blocker.get('code')}.",
            str(next_blocker.get("action")),
            "See outputs/FIRST_STOCK_NEXT.md; this priority does not replace the complete universe.",
            "",
        ]
    ctx.write_bytes("outputs/NEXT_ACTIONS.md", ("\n".join(lines) + "\n").encode())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/qualified/local-inference"))
    parser.add_argument("--capture-documentation", action="store_true")
    args = parser.parse_args()
    try:
        ctx = QualificationContext(
            args.output, Path(__file__).resolve().parents[3], dict(os.environ), utc_now()
        )
        with ctx.locked():
            master, _ = read_master(ctx)
            from money.qualification.native_diagnosis import write_native_diagnosis
            from money.qualification.universe_progress import write_enrichment_progress
            from money.qualification.universe_reviews import prepare_universe_reviews

            write_enrichment_progress(ctx, master)
            prepare_universe_reviews(
                ctx,
                master.get("stocks", []),
                master.get("provenance", {}),
                fetch_documents=args.capture_documentation,
            )
            write_native_diagnosis(ctx)
            result = write_qualification_diagnostics(ctx, master=master)
        print("DIAGNOSTICS PREPARED — no qualification or approval granted")
        print(
            f"Recorded GBX stocks: {result['qualification_counts_as_recorded'].get('gbx_stocks', 'unknown')}"
        )
        print("Review outputs/NEXT_ACTIONS.md and outputs/qualification-blocker-dag.json")
        return 0
    except Exception:
        print(
            "DIAGNOSTICS BLOCKED — check saved evidence integrity and output paths; no raw exception printed"
        )
        return 2
