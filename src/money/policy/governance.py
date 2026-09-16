"""Deterministic evidence gates; confidence and stretch objectives cannot override them."""

from collections import Counter
from datetime import datetime
from typing import Literal

from money.adapters.eligibility import eligibility_failures
from money.schemas.contracts import (
    CIOAuditReport,
    EvidenceIndependenceReport,
    FirmReport,
    LeanValidationReport,
    RedTeamReport,
    ResearchMandate,
    ResearchSnapshot,
    ResearchState,
)


def snapshot_failures(snapshot: ResearchSnapshot, now: datetime) -> tuple[str, ...]:
    failures: list[str] = []
    if not any(e.critical and e.payload.kind == "ohlcv" for e in snapshot.evidence):
        failures.append("CRITICAL_PRICE_MISSING")
    for record in snapshot.evidence:
        if record.critical and record.fresh_until <= now:
            failures.append("CRITICAL_EVIDENCE_STALE")
        if record.critical and record.conflicting:
            failures.append("CRITICAL_EVIDENCE_CONFLICT")
        if record.critical and not record.available_at(snapshot.cutoff_for(record)):
            failures.append("CRITICAL_EVIDENCE_PIT_UNKNOWN")
    return tuple(dict.fromkeys(failures))


def _overlap(families: list[set[str]]) -> float:
    count = sum(len(s) for s in families)
    return 1 - len(set().union(*families)) / count if count else 1.0


def evidence_independence(
    snapshot: ResearchSnapshot,
    reports: tuple[FirmReport, ...],
) -> EvidenceIndependenceReport:
    by_id = {e.evidence_id: e for e in snapshot.evidence}
    source_sets: list[set[str]] = []
    providers: set[str] = set()
    families: set[str] = set()
    for report in reports:
        source_set: set[str] = set()
        for claim in report.claims:
            for evidence_id in claim.evidence_ids:
                if evidence_id not in by_id:
                    raise ValueError("report cites evidence outside the snapshot")
                record = by_id[evidence_id]
                source_set.add(record.canonical_source_id)
                providers.add(record.provider)
                families.add(claim.family)
        source_sets.append(source_set)
    source_overlap = _overlap(source_sets)
    model_overlap = _overlap(
        [
            {r.model_family} | ({r.llm_provider_family} if r.llm_provider_family else set())
            for r in reports
        ]
    )
    feature_overlap = _overlap([set(r.feature_families) for r in reports])
    argument_overlap = _overlap([set(r.argument_families) for r in reports])
    sources = set().union(*source_sets)
    strength: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    if len(sources) >= 3 and len(families) >= 2 and source_overlap < 0.6:
        strength = "MEDIUM"
    if (
        len(sources) >= 5
        and len(families) >= 3
        and len(providers) >= 2
        and max(source_overlap, model_overlap, feature_overlap, argument_overlap) < 0.4
    ):
        strength = "HIGH"
    limitations = ["Agreement between firms is not independent confirmation."]
    if len(providers) < 2:
        limitations.append("Evidence depends on a single provider.")
    if source_overlap >= 0.5:
        limitations.append("Firms substantially share original sources.")
    if model_overlap >= 0.5:
        limitations.append("Shared model or LLM provider families reduce independence.")
    return EvidenceIndependenceReport(
        unique_sources=len(sources),
        unique_providers=len(providers),
        evidence_families=tuple(sorted(families)),
        source_overlap=source_overlap,
        model_overlap=model_overlap,
        feature_overlap=feature_overlap,
        argument_overlap=argument_overlap,
        strength=strength,
        limitations=tuple(limitations),
    )


def consensus(
    mandate: ResearchMandate,
    snapshot: ResearchSnapshot,
    reports: tuple[FirmReport, ...],
    lean: LeanValidationReport,
    audit: CIOAuditReport,
    red_team: RedTeamReport,
    independence: EvidenceIndependenceReport,
    now: datetime,
    *,
    rounds: int = 0,
) -> tuple[ResearchState, tuple[str, ...]]:
    """Return research quality only. No BUY/SELL vote, position or order is constructed."""
    if not 0 <= rounds <= 2:
        raise ValueError("at most two cross-examination rounds are permitted")
    failures = eligibility_failures(snapshot.instrument, mandate, now) + snapshot_failures(
        snapshot, now
    )
    if failures:
        return ResearchState.REJECT, failures
    if Counter(r.firm for r in reports) != Counter(("tradingagents", "ai_hedge_fund", "qlib")):
        return ResearchState.INSUFFICIENT_EVIDENCE, ("FIRST_PASS_INCOMPLETE",)
    if any(
        r.snapshot_hash != snapshot.hash or r.snapshot_id != snapshot.snapshot_id for r in reports
    ):
        return ResearchState.INSUFFICIENT_EVIDENCE, ("REPORT_SNAPSHOT_MISMATCH",)
    if audit.snapshot_id != snapshot.snapshot_id or lean.snapshot_id != snapshot.snapshot_id:
        return ResearchState.INSUFFICIENT_EVIDENCE, ("AUDIT_SNAPSHOT_MISMATCH",)
    if red_team.state == "VETO" or lean.state == "FAIL":
        return ResearchState.REJECT, ("RED_TEAM_OR_VALIDATION_VETO",)
    reasons: list[str] = []
    if any(not report.claims for report in reports):
        reasons.append("FIRST_PASS_FIRM_ABSTAINED")
    if any(r.runtime != "live" for r in reports):
        reasons.append("DEMONSTRATION_ONLY")
    if lean.state != "PASS":
        reasons.append("LEAN_INSUFFICIENT_EVIDENCE")
    elif not (
        lean.observations >= 30
        and lean.walk_forward
        and lean.out_of_sample
        and lean.pit_safe
        and lean.survivorship_checked
        and lean.costs_included
        and lean.sensitivity_checked
        and lean.spread_bps is not None
        and lean.slippage_bps is not None
    ):
        reasons.append("LEAN_VALIDATION_METADATA_INCOMPLETE")
    if not audit.completed:
        reasons.append("CIO_AUDIT_INCOMPLETE")
    if audit.material_disagreement:
        reasons.append("UNRESOLVED_MATERIAL_DISAGREEMENT")
    if any(f.state in {"UNSUPPORTED", "CONTRADICTED"} for f in audit.findings):
        reasons.append("UNVERIFIED_CLAIMS")
    all_claims = [c for report in reports for c in report.claims]
    claims = {c.claim_id: c for c in all_claims}
    if len(claims) != len(all_claims):
        reasons.append("DUPLICATE_CLAIM_IDENTITY")
    available = {e.evidence_id for e in snapshot.evidence}
    verified: set[str] = set()
    for finding in audit.findings:
        if finding.state != "VERIFIED":
            continue
        claim = claims.get(finding.claim_id or "")
        if (
            claim is None
            or not finding.evidence_ids
            or not set(finding.evidence_ids) <= available
            or not set(finding.evidence_ids).intersection(claim.evidence_ids)
        ):
            reasons.append("AUDIT_EVIDENCE_LINK_INVALID")
        else:
            verified.add(claim.claim_id)
    if not claims or not set(claims).issubset(verified):
        reasons.append("CLAIM_VERIFICATION_INCOMPLETE")
    if reasons:
        return ResearchState.INSUFFICIENT_EVIDENCE, tuple(reasons)
    if independence.strength == "LOW" or red_team.state == "WARN":
        return ResearchState.WATCH, ("LIMITED_INDEPENDENCE_OR_RED_TEAM_WARNING",)
    # Calibrated component reliability is not yet established; strong status is reserved.
    return ResearchState.RESEARCH_CANDIDATE, ("REQUIRED_EVIDENCE_GATES_PASSED",)
