"""Offline artifact eligibility only: this module never executes or activates code."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from money.schemas.contracts import Contract

ArtifactHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ValidationKind = Literal["POINT_IN_TIME", "WALK_FORWARD", "OUT_OF_SAMPLE", "REGRESSION"]
REQUIRED_VALIDATIONS = frozenset({"POINT_IN_TIME", "WALK_FORWARD", "OUT_OF_SAMPLE", "REGRESSION"})


class OfflineValidationEvidence(Contract):
    kind: ValidationKind
    passed: bool
    tested_artifact_hash: ArtifactHash
    dataset_hash: ArtifactHash
    report_hash: ArtifactHash
    completed_at: AwareDatetime


class IndependentApproval(Contract):
    reviewer_id: str = Field(min_length=1, max_length=200)
    approved: bool
    reviewed_artifact_hash: ArtifactHash
    reviewed_validation_hashes: tuple[ArtifactHash, ...]
    reviewed_at: AwareDatetime


class PromotionEvidence(Contract):
    artifact_id: str = Field(min_length=1, max_length=200)
    artifact_hash: ArtifactHash
    proposed_by: str = Field(min_length=1, max_length=200)
    validations: tuple[OfflineValidationEvidence, ...]
    approval: IndependentApproval | None = None


class PromotionAssessment(Contract):
    artifact_id: str
    artifact_hash: ArtifactHash
    assessed_at: AwareDatetime
    state: Literal["BLOCKED", "ELIGIBLE_FOR_MANUAL_PROMOTION"]
    reasons: tuple[str, ...]
    activated: Literal[False] = False


def assess_promotion(evidence: PromotionEvidence, as_of: datetime) -> PromotionAssessment:
    """Assess immutable evidence from trusted storage; do not trust an agent's assertion.

    The caller must authenticate the reviewer and verify hashes against stored
    artifact/report bytes. This pure gate deliberately has no execution endpoint,
    shell, Docker, registry write, or automatic production promotion operation.
    """
    item = PromotionEvidence.model_validate_json(evidence.model_dump_json())
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("promotion assessment time must be timezone-aware")
    reasons = []
    if not item.proposed_by.strip():
        reasons.append("PROPOSER_IDENTITY_MISSING")
    kinds = [validation.kind for validation in item.validations]
    if set(kinds) != REQUIRED_VALIDATIONS:
        reasons.append("REQUIRED_VALIDATION_MISSING")
    if len(kinds) != len(set(kinds)):
        reasons.append("DUPLICATE_VALIDATION_KIND")
    hashes = {validation.report_hash for validation in item.validations}
    for validation in item.validations:
        if not validation.passed:
            reasons.append(f"{validation.kind}_FAILED")
        if validation.tested_artifact_hash != item.artifact_hash:
            reasons.append("VALIDATION_ARTIFACT_MISMATCH")
        if validation.completed_at > as_of:
            reasons.append("VALIDATION_FROM_FUTURE")
    approval = item.approval
    if approval is None or not approval.approved:
        reasons.append("INDEPENDENT_APPROVAL_REQUIRED")
    elif (
        not approval.reviewer_id.strip()
        or approval.reviewer_id.strip().casefold() == item.proposed_by.strip().casefold()
    ):
        reasons.append("REVIEWER_NOT_INDEPENDENT")
    else:
        if approval.reviewed_artifact_hash != item.artifact_hash:
            reasons.append("APPROVAL_ARTIFACT_MISMATCH")
        if set(approval.reviewed_validation_hashes) != hashes:
            reasons.append("APPROVAL_VALIDATION_MISMATCH")
        if approval.reviewed_at > as_of or any(
            approval.reviewed_at < validation.completed_at for validation in item.validations
        ):
            reasons.append("APPROVAL_TIME_INVALID")
    return PromotionAssessment(
        artifact_id=item.artifact_id,
        artifact_hash=item.artifact_hash,
        assessed_at=as_of,
        state="BLOCKED" if reasons else "ELIGIBLE_FOR_MANUAL_PROMOTION",
        reasons=tuple(dict.fromkeys(reasons)),
    )
