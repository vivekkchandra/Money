"""Bounded post-lock challenges; corrections never replace sealed first-pass reports."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal, Protocol, Self

from pydantic import AwareDatetime, Field, model_validator

from money.adapters.upstream import InvalidUpstreamReport
from money.schemas.contracts import (
    AuditFinding,
    CIOAuditReport,
    Claim,
    Contract,
    FirmReport,
    LeanValidationReport,
    ResearchSnapshot,
    Usage,
    content_hash,
)

Respondent = Literal["tradingagents", "ai_hedge_fund", "qlib", "lean", "cio"]


class Challenge(Contract):
    challenge_id: str
    author: Literal["CIO Contradiction Analyst"] = "CIO Contradiction Analyst"
    respondent: Respondent
    claim_id: str | None
    original_report_hash: str
    question: str = Field(min_length=1, max_length=4000)
    evidence_ids: tuple[str, ...]
    original_claim: Claim | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def claim_identity(self) -> Self:
        if self.original_claim is not None and self.original_claim.claim_id != self.claim_id:
            raise ValueError("challenge original claim identity differs")
        return self


class ChallengeInvocation(Contract):
    component: Literal["tradingagents", "ai_hedge_fund", "crewai", "qlib", "lean"]
    provider: str
    model: str
    prompt_version: str
    upstream_sha: str
    calls: int = Field(ge=0, le=2)
    usage: Usage = Field(default_factory=Usage)
    runtime: Literal["live", "demo", "deterministic"]


class ChallengeResponse(Contract):
    challenge_id: str
    respondent: Respondent
    snapshot_hash: str
    original_report_hash: str
    position: Literal["MAINTAIN", "WITHDRAW", "INSUFFICIENT_EVIDENCE"]
    explanation: str = Field(min_length=1, max_length=4000)
    evidence_ids: tuple[str, ...]
    corrected_claim: Claim | None = None
    invocation: ChallengeInvocation | None = Field(default=None, exclude_if=lambda value: value is None)


class ChallengeSourceCheck(Contract):
    evidence_id: str
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    quoted_fact: str = Field(min_length=5, max_length=500)


class ChallengeVerification(Contract):
    finding: AuditFinding
    invocation: ChallengeInvocation | None = Field(default=None, exclude_if=lambda value: value is None)
    evidence_checks: tuple[ChallengeSourceCheck, ...] = Field(default=(), exclude_if=lambda value: not value)


class ChallengeExchange(Contract):
    challenge: Challenge
    response: ChallengeResponse | None = None
    verification: AuditFinding | None = None
    verification_invocation: ChallengeInvocation | None = Field(default=None, exclude_if=lambda value: value is None)
    verification_evidence_checks: tuple[ChallengeSourceCheck, ...] = Field(default=(), exclude_if=lambda value: not value)
    resolved: bool = False
    reason: str


class CrossExaminationRound(Contract):
    number: int = Field(ge=1, le=2)
    exchanges: tuple[ChallengeExchange, ...]


class CrossExaminationPacket(Contract):
    snapshot_id: str
    snapshot_hash: str
    report_hashes: tuple[tuple[str, str], ...]
    lean_hash: str
    initial_audit_hash: str
    challenges: tuple[Challenge, ...] = Field(max_length=24)
    rounds: tuple[CrossExaminationRound, ...] = Field(max_length=2)
    unresolved_challenge_ids: tuple[str, ...]
    material_disagreement: bool
    issued_at: AwareDatetime
    hash: str = ""

    @model_validator(mode="after")
    def seal(self) -> Self:
        if tuple(item.number for item in self.rounds) != tuple(range(1, len(self.rounds) + 1)):
            raise ValueError("cross-examination rounds must be consecutive and bounded")
        if self.material_disagreement != bool(self.unresolved_challenge_ids):
            raise ValueError("unresolved cross-examination cannot be hidden")
        pending = {challenge.challenge_id: challenge for challenge in self.challenges}
        if len(pending) != len(self.challenges):
            raise ValueError("cross-examination challenge identities must be unique")
        originals = dict(self.report_hashes)
        if len(self.report_hashes) != 3 or set(originals) != {"tradingagents", "ai_hedge_fund", "qlib"}:
            raise ValueError("cross-examination requires exactly three original firm hashes")
        originals.update({"lean": self.lean_hash, "cio": self.initial_audit_hash})
        if any(originals.get(challenge.respondent) != challenge.original_report_hash for challenge in self.challenges):
            raise ValueError("challenge original report hash differs from sealed provenance")
        for round_ in self.rounds:
            if {exchange.challenge.challenge_id for exchange in round_.exchanges} != set(pending):
                raise ValueError("each round must address exactly the pending challenges")
            if len(round_.exchanges) != len(pending):
                raise ValueError("cross-examination exchange identities must be unique")
            for exchange in round_.exchanges:
                challenge = pending[exchange.challenge.challenge_id]
                if exchange.challenge != challenge:
                    raise ValueError("cross-examination cannot revise an original challenge")
                response, verification = exchange.response, exchange.verification
                if ((response and response.invocation and response.invocation.runtime == "demo")
                        or (exchange.verification_invocation and exchange.verification_invocation.runtime == "demo")):
                    raise ValueError("unqualified native correspondence cannot enter a sealed packet")
                if response is not None and (
                    response.challenge_id != challenge.challenge_id
                    or response.respondent != challenge.respondent
                    or response.snapshot_hash != self.snapshot_hash
                    or response.original_report_hash != challenge.original_report_hash
                    or not set(response.evidence_ids) <= set(challenge.evidence_ids)
                    or (response.corrected_claim is not None
                        and not set(response.corrected_claim.evidence_ids) <= set(challenge.evidence_ids))
                ):
                    raise ValueError("cross-examination response identity differs")
                if verification is not None and not set(verification.evidence_ids) <= set(challenge.evidence_ids):
                    raise ValueError("cross-examination verification exceeded permitted evidence")
                if any(check.evidence_id not in challenge.evidence_ids for check in exchange.verification_evidence_checks):
                    raise ValueError("cross-examination source check exceeded permitted evidence")
                if exchange.resolved:
                    if (response is None or response.position != "MAINTAIN" or verification is None
                            or verification.state != "VERIFIED" or verification.claim_id != challenge.claim_id
                            or not set(verification.evidence_ids).intersection(response.evidence_ids)):
                        raise ValueError("cross-examination resolution needs independent cited verification")
                    del pending[challenge.challenge_id]
        if set(self.unresolved_challenge_ids) != set(pending) or len(self.unresolved_challenge_ids) != len(pending):
            raise ValueError("cross-examination unresolved set differs from actual exchanges")
        digest = content_hash(self.model_dump(mode="json", exclude={"hash"}))
        if self.hash and self.hash != digest:
            raise ValueError("cross-examination artifact hash differs")
        object.__setattr__(self, "hash", digest)
        return self


class ResponseCapability(Protocol):
    def __call__(self, snapshot: ResearchSnapshot, own_report: FirmReport | None,
                 challenge: Challenge, round_number: int) -> ChallengeResponse: ...


class VerificationCapability(Protocol):
    def __call__(self, snapshot: ResearchSnapshot, challenge: Challenge,
                 response: ChallengeResponse) -> AuditFinding | ChallengeVerification: ...


def run_cross_examination(
    snapshot: ResearchSnapshot,
    reports: tuple[FirmReport, ...],
    lean: LeanValidationReport,
    audit: CIOAuditReport,
    *,
    first_pass_locked: bool,
    responders: Mapping[str, ResponseCapability] | None = None,
    verifier: VerificationCapability | None = None,
    maximum_rounds: int = 2,
    maximum_challenges: int = 24,
) -> CrossExaminationPacket:
    """Only the downstream control plane, after durable lock/audit, calls this.

    An LLM response cannot certify itself. Any resolution additionally requires
    the separately supplied independent verification capability. Default missing
    responders or verifiers retain material disagreement and produce no signal.
    """
    if not first_pass_locked or not audit.completed:
        raise InvalidUpstreamReport("cross-examination requires locked first pass and completed initial CIO audit")
    if not 0 <= maximum_rounds <= 2:
        raise ValueError("cross-examination allows at most two rounds")
    if not 1 <= maximum_challenges <= 24:
        raise ValueError("cross-examination allows at most 24 challenges")
    if len(reports) != 3 or {r.firm for r in reports} != {"tradingagents", "ai_hedge_fund", "qlib"}:
        raise InvalidUpstreamReport("cross-examination requires all three sealed first-pass reports")
    if lean.snapshot_id != snapshot.snapshot_id or audit.snapshot_id != snapshot.snapshot_id or any(
        report.snapshot_id != snapshot.snapshot_id or report.snapshot_hash != snapshot.hash for report in reports
    ):
        raise InvalidUpstreamReport("cross-examination snapshot identity differs")
    facts = ResearchSnapshot.model_validate_json(snapshot.model_dump_json())
    by_firm: dict[str, FirmReport] = {
        report.firm: type(report).model_validate_json(report.model_dump_json()) for report in reports
    }
    by_claim = {claim.claim_id: report for report in by_firm.values() for claim in report.claims}
    if len(by_claim) != sum(len(report.claims) for report in reports):
        raise InvalidUpstreamReport("cross-examination found duplicate original claim identities")
    allowed_ids = {item.evidence_id for item in facts.evidence}
    findings = [finding for finding in audit.findings if finding.state in {"UNSUPPORTED", "CONTRADICTED"}]
    if audit.material_disagreement and not findings:
        findings = [AuditFinding(auditor="CIO Contradiction Analyst", claim_id=claim_id,
            state="UNSUPPORTED", explanation="Initial CIO audit records unresolved material disagreement.")
            for claim_id in by_claim]
        if not findings:
            findings = [AuditFinding(auditor="CIO Contradiction Analyst", state="UNSUPPORTED",
                explanation="Initial CIO audit records unresolved material disagreement without supported claims.")]
    if len(findings) > maximum_challenges:
        raise InvalidUpstreamReport("cross-examination challenge budget exceeded")
    challenges: dict[str, Challenge] = {}
    for index, finding in enumerate(findings):
        owner = by_claim.get(finding.claim_id or "")
        if finding.claim_id and owner is None:
            raise InvalidUpstreamReport("cross-examination audit references an unknown claim")
        target: Respondent = owner.firm if owner else "lean" if finding.auditor == "LEAN Auditor" else "cio"
        original_hash = content_hash(owner) if owner else content_hash(lean if target == "lean" else audit)
        original_claim = next((claim for claim in owner.claims if claim.claim_id == finding.claim_id), None) if owner else None
        permitted = tuple(dict.fromkeys((*finding.evidence_ids, *(original_claim.evidence_ids if original_claim else ()))))
        challenge = Challenge(challenge_id=f"challenge-{index + 1}", respondent=target,
            claim_id=finding.claim_id, original_report_hash=original_hash,
            question=finding.explanation, evidence_ids=permitted, original_claim=original_claim)
        if not set(challenge.evidence_ids) <= allowed_ids:
            raise InvalidUpstreamReport("challenge cites evidence outside the locked snapshot")
        challenges[challenge.challenge_id] = challenge
    unresolved = dict(challenges)
    rounds = []
    capabilities = responders or {}
    for number in range(1, maximum_rounds + 1):
        if not unresolved:
            break
        exchanges = []
        any_response = False
        for challenge in tuple(unresolved.values()):
            capability = capabilities.get(challenge.respondent)
            if capability is None:
                exchanges.append(ChallengeExchange(challenge=challenge, reason="RESPONDER_UNAVAILABLE"))
                continue
            original = by_firm.get(challenge.respondent)
            own_report = type(original).model_validate_json(original.model_dump_json()) if original else None
            response = ChallengeResponse.model_validate_json(capability(
                ResearchSnapshot.model_validate_json(facts.model_dump_json()), own_report,
                Challenge.model_validate_json(challenge.model_dump_json()), number,
            ).model_dump_json())
            any_response = True
            if response.invocation is not None and response.invocation.runtime == "demo":
                raise InvalidUpstreamReport("unqualified native challenge response cannot enter live cross-examination")
            if (response.challenge_id != challenge.challenge_id or response.respondent != challenge.respondent
                    or response.snapshot_hash != facts.hash or response.original_report_hash != challenge.original_report_hash
                    or not set(response.evidence_ids) <= set(challenge.evidence_ids)):
                raise InvalidUpstreamReport("cross-examination response provenance differs")
            if response.corrected_claim is not None and (
                response.corrected_claim.claim_id in by_claim
                or not response.corrected_claim.evidence_ids
                or not set(response.corrected_claim.evidence_ids) <= set(challenge.evidence_ids)
            ):
                raise InvalidUpstreamReport("correction cannot overwrite original claim or add external evidence")
            verification = None
            verification_invocation = None
            verification_evidence_checks: tuple[ChallengeSourceCheck, ...] = ()
            resolved = False
            reason = "INDEPENDENT_VERIFICATION_UNAVAILABLE"
            if verifier is not None and response.position == "MAINTAIN":
                checked = verifier(
                    ResearchSnapshot.model_validate_json(facts.model_dump_json()), challenge, response
                )
                if isinstance(checked, ChallengeVerification):
                    verification = AuditFinding.model_validate_json(checked.finding.model_dump_json())
                    verification_invocation = checked.invocation
                    verification_evidence_checks = checked.evidence_checks
                    evidence_hashes = {record.evidence_id: record.hash for record in facts.evidence}
                    if any(evidence_hashes.get(check.evidence_id) != check.record_hash
                           or check.evidence_id not in challenge.evidence_ids
                           for check in verification_evidence_checks):
                        raise InvalidUpstreamReport("challenge source check differs from frozen evidence hash")
                    if verification_invocation is not None and verification_invocation.runtime == "demo":
                        raise InvalidUpstreamReport("unqualified native verification cannot resolve a live challenge")
                else:
                    verification = AuditFinding.model_validate_json(checked.model_dump_json())
                if (verification.claim_id != challenge.claim_id
                        or not set(verification.evidence_ids) <= set(challenge.evidence_ids)):
                    raise InvalidUpstreamReport("challenge verification provenance differs")
                resolved = bool(verification.state == "VERIFIED" and verification.evidence_ids
                                and set(verification.evidence_ids).intersection(response.evidence_ids))
                reason = "INDEPENDENTLY_VERIFIED" if resolved else "MATERIAL_DISAGREEMENT_UNRESOLVED"
            elif response.position != "MAINTAIN":
                reason = "ORIGINAL_MATERIAL_CLAIM_WITHDRAWN_OR_UNSUPPORTED"
            if resolved:
                del unresolved[challenge.challenge_id]
            exchanges.append(ChallengeExchange(challenge=challenge, response=response,
                verification=verification, verification_invocation=verification_invocation,
                verification_evidence_checks=verification_evidence_checks,
                resolved=resolved, reason=reason))
        rounds.append(CrossExaminationRound(number=number, exchanges=tuple(exchanges)))
        if not any_response:
            break  # no fake second round when every respondent is unavailable
    return CrossExaminationPacket(snapshot_id=facts.snapshot_id, snapshot_hash=facts.hash,
        report_hashes=tuple(sorted((name, content_hash(report)) for name, report in by_firm.items())),
        lean_hash=content_hash(lean), initial_audit_hash=content_hash(audit),
        challenges=tuple(challenges.values()), rounds=tuple(rounds),
        unresolved_challenge_ids=tuple(unresolved), material_disagreement=bool(unresolved), issued_at=datetime.now(UTC))
