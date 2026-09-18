"""Synthetic unit fixtures exercise screening rules, never production evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from money.research.ethics import (
    BusinessActivity,
    EthicalAssessment,
    EthicalCitation,
    EthicalEvidence,
    ExclusionAssessment,
    GlobalSourceApproval,
    IssuerIdentity,
    ethical_policy_hash,
    screen_issuer,
    screening_cache_key,
)
from money.schemas.contracts import EXCLUDED_ACTIVITIES, content_hash

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)
SCOPE = (
    "Example Drinks plc's only material operations, subsidiaries and revenue streams "
    "are manufacture, distribution and sale of fruit soft drinks for consumer retail. "
    "The consolidated operating-segment statement covers 100% of group revenue, "
    "assets and activities, with no additional operating segments or investments."
)


@pytest.fixture
def issuer() -> IssuerIdentity:
    return IssuerIdentity(
        issuer_key="verified-company:example-drinks",
        legal_name="Example Drinks plc",
        isins=("GB0006389398",),
        identity_evidence_hashes=(content_hash({"verified_identity": "unit-test"}),),
    )


@pytest.fixture
def evidence(issuer: IssuerIdentity) -> EthicalEvidence:
    return EthicalEvidence(
        source_id="unit-fixture:annual-report",
        provider="fixture-provider",
        issuer_key=issuer.issuer_key,
        content=SCOPE,
        content_sha256=hashlib.sha256(SCOPE.encode()).hexdigest(),
        published_at=NOW - timedelta(days=90),
        retrieved_at=NOW - timedelta(hours=1),
        evidence_kind="annual_report",
    )


@pytest.fixture
def approval() -> GlobalSourceApproval:
    return GlobalSourceApproval(
        provider="fixture-provider",
        evidence_hashes=(content_hash({"global_rights": "unit-test"}),),
        valid_until=NOW + timedelta(days=365),
    )


def assessment(issuer: IssuerIdentity, evidence: EthicalEvidence) -> EthicalAssessment:
    citation = EthicalCitation(
        source_id=evidence.source_id,
        evidence_hash=evidence.content_sha256,
        quote=evidence.content,
    )
    return EthicalAssessment(
        issuer_key=issuer.issuer_key,
        legal_name=issuer.legal_name,
        complete_material_business_scope=True,
        scope_citations=(citation,),
        business_activities=(BusinessActivity(activity="soft_drinks", citations=(citation,)),),
        assessments=tuple(
            ExclusionAssessment(
                category=category,
                conclusion="NO_MATERIAL_EXPOSURE",
                basis="COMPLETE_BUSINESS_SCOPE",
                citations=(citation,),
                rationale=(
                    "The complete consolidated revenue and business account supports only "
                    f"consumer soft-drink operations, not material {category} operations."
                ),
            )
            for category in EXCLUDED_ACTIVITIES
        ),
    )


def run_screen(issuer, evidence, approval, response=None, **kwargs):
    evaluator = Mock(return_value=(response or assessment(issuer, evidence)).model_dump_json())
    result = screen_issuer(
        issuer,
        (evidence,),
        now=kwargs.pop("now", NOW),
        source_approvals=(approval,),
        evaluator=evaluator,
        evaluator_identity="fixture-evaluator",
        **kwargs,
    )
    return result, evaluator


def test_one_machine_pass_needs_no_human_or_second_reviewer(issuer, evidence, approval):
    result, evaluator = run_screen(issuer, evidence, approval)
    assert result.clearance.result == "PASS"
    assert result.clearance.valid_until == NOW + timedelta(days=30)
    assert set(result.clearance.assessed_exclusions) == set(EXCLUDED_ACTIVITIES)
    assert result.clearance.evidence_hashes == (evidence.content_sha256,)
    evaluator.assert_called_once()
    serialized = result.model_dump_json()
    assert all(name not in serialized for name in ("reviewed_by", "prepared_by", "reviewed_at"))
    assert result.clearance.screening_hash == content_hash(
        result.clearance.model_dump(mode="json", exclude={"screening_hash"})
    )


def test_unchanged_pass_reused_downstream_without_new_screening(issuer, evidence, approval):
    first, _ = run_screen(issuer, evidence, approval)
    reused, evaluator = run_screen(
        issuer, evidence, approval, previous=first, now=NOW + timedelta(days=5)
    )
    assert reused.reused
    assert reused.clearance == first.clearance
    evaluator.assert_not_called()


def test_multiple_verified_share_lines_reuse_one_issuer_pass(issuer, evidence, approval):
    first, _ = run_screen(issuer, evidence, approval)
    share_lines = issuer.model_copy(update={"isins": (*issuer.isins, "JE00B4T3BW64")})
    reused, evaluator = run_screen(share_lines, evidence, approval, previous=first)
    assert reused.reused
    assert reused.clearance.result == "PASS"
    assert set(reused.clearance.isins) == set(share_lines.isins)
    assert reused.clearance.screened_at == first.clearance.screened_at
    evaluator.assert_not_called()


def test_daily_source_retrieval_and_rights_renewal_do_not_repeat_ethical_question(
    issuer, evidence, approval
):
    first, _ = run_screen(issuer, evidence, approval)
    renewed = approval.model_copy(
        update={
            "valid_until": NOW + timedelta(days=366),
            "evidence_hashes": (content_hash({"renewed_global_rights": True}),),
        }
    )
    refreshed = evidence.model_copy(update={"retrieved_at": NOW + timedelta(days=1)})
    reused, evaluator = run_screen(
        issuer, refreshed, renewed, previous=first, now=NOW + timedelta(days=1)
    )
    assert reused.reused
    assert reused.clearance == first.clearance
    assert reused.global_rights_evidence_hashes == renewed.evidence_hashes
    evaluator.assert_not_called()


def test_supported_excluded_activity_fails(issuer, evidence, approval):
    exposed_text = SCOPE + " The group also earns 40% of revenue manufacturing military weapons."
    exposed = evidence.model_copy(
        update={
            "content": exposed_text,
            "content_sha256": hashlib.sha256(exposed_text.encode()).hexdigest(),
        }
    )
    response = assessment(issuer, exposed)
    response = response.model_copy(
        update={
            "assessments": (
                response.assessments[0].model_copy(
                    update={"conclusion": "MATERIAL_EXPOSURE", "basis": "EXPLICIT_EXPOSURE"}
                ),
                *response.assessments[1:],
            )
        }
    )
    result, _ = run_screen(issuer, exposed, approval, response=response)
    assert result.clearance.result == "FAIL"
    assert "ETHICAL_MATERIAL_EXPOSURE:defence" in result.clearance.reasons


@pytest.mark.parametrize("gap", ["scope", "category", "activities", "questions"])
def test_incomplete_evidence_never_becomes_pass(issuer, evidence, approval, gap):
    response = assessment(issuer, evidence)
    changes = {
        "scope": {"complete_material_business_scope": False},
        "category": {"assessments": response.assessments[:-1]},
        "activities": {"business_activities": ()},
        "questions": {"unresolved_questions": ("Unclear material joint venture activities",)},
    }
    result, _ = run_screen(
        issuer, evidence, approval, response=response.model_copy(update=changes[gap])
    )
    assert result.clearance.result == "UNKNOWN"


def test_missing_evidence_unknown_without_inference_or_manufactured_review(issuer):
    evaluator = Mock()
    result = screen_issuer(issuer, (), now=NOW, evaluator=evaluator)
    assert result.clearance.result == "UNKNOWN"
    assert result.clearance.reasons == ("ADMISSIBLE_ISSUER_BUSINESS_EVIDENCE_REQUIRED",)
    assert result.assessment is None
    evaluator.assert_not_called()


def test_unknown_category_cannot_proceed(issuer, evidence, approval):
    response = assessment(issuer, evidence)
    response = response.model_copy(
        update={
            "assessments": (
                response.assessments[0].model_copy(
                    update={"conclusion": "UNKNOWN", "basis": "INSUFFICIENT"}
                ),
                *response.assessments[1:],
            )
        }
    )
    result, _ = run_screen(issuer, evidence, approval, response=response)
    assert result.clearance.result == "UNKNOWN"
    assert "ETHICAL_EXPOSURE_UNKNOWN:defence" in result.clearance.reasons


def test_material_contradiction_reopens_cached_pass(issuer, evidence, approval):
    first, _ = run_screen(issuer, evidence, approval)
    result, evaluator = run_screen(
        issuer, evidence, approval, previous=first, credible_contradiction=True
    )
    assert result.clearance.result == "UNKNOWN"
    assert not result.reused
    assert "ETHICAL_CREDIBLE_CONTRADICTION_REQUIRES_RESCREEN" in result.clearance.reasons
    evaluator.assert_not_called()


def test_new_conflicting_source_invalidates_previous_clearance(issuer, evidence, approval):
    first, _ = run_screen(issuer, evidence, approval)
    changed_text = SCOPE + " A current regulatory notice disputes that activity disclosure."
    changed = evidence.model_copy(
        update={
            "content": changed_text,
            "content_sha256": hashlib.sha256(changed_text.encode()).hexdigest(),
        }
    )
    response = assessment(issuer, changed).model_copy(update={"conflicting_evidence": True})
    result, evaluator = run_screen(issuer, changed, approval, previous=first, response=response)
    assert result.cache_key != first.cache_key
    assert result.clearance.result == "UNKNOWN"
    assert result.clearance.reasons == ("ETHICAL_CONFLICTING_EVIDENCE",)
    evaluator.assert_called_once()


def test_expired_clearance_requires_new_screening(issuer, evidence, approval):
    first, _ = run_screen(issuer, evidence, approval)
    result, evaluator = run_screen(
        issuer, evidence, approval, previous=first, now=NOW + timedelta(days=30)
    )
    assert not result.reused
    assert result.clearance.screened_at == NOW + timedelta(days=30)
    evaluator.assert_called_once()


def test_policy_change_invalidates_cached_screening_without_dropping_existing_exclusions(
    issuer, evidence, approval
):
    first, _ = run_screen(issuer, evidence, approval)
    expanded = (*EXCLUDED_ACTIVITIES, "tobacco")
    result, evaluator = run_screen(
        issuer, evidence, approval, previous=first, excluded_activities=expanded
    )
    assert result.cache_key != first.cache_key
    assert result.clearance.result == "UNKNOWN"
    assert result.clearance.policy_hash == ethical_policy_hash(expanded)
    evaluator.assert_called_once()
    with pytest.raises(ValueError, match="EXCLUSIONS_CANNOT_BE_REMOVED"):
        ethical_policy_hash(EXCLUDED_ACTIVITIES[:-1])


def test_issuer_identity_change_invalidates_cache(issuer, evidence, approval):
    first, _ = run_screen(issuer, evidence, approval)
    renamed = issuer.model_copy(update={"legal_name": "Different verified issuer plc"})
    result, evaluator = run_screen(renamed, evidence, approval, previous=first)
    assert result.cache_key != first.cache_key
    assert not result.reused
    evaluator.assert_called_once()


@pytest.mark.parametrize("corruption", ["quote", "hash", "source"])
def test_unsupported_or_invented_citations_cannot_pass(issuer, evidence, approval, corruption):
    response = assessment(issuer, evidence)
    citation = response.assessments[0].citations[0]
    changes = {
        "quote": {"quote": "A fabricated sentence absent from the actual source bytes."},
        "hash": {"evidence_hash": hashlib.sha256(b"wrong source bytes").hexdigest()},
        "source": {"source_id": "invented-source"},
    }
    corrupted = response.assessments[0].model_copy(
        update={"citations": (citation.model_copy(update=changes[corruption]),)}
    )
    response = response.model_copy(update={"assessments": (corrupted, *response.assessments[1:])})
    result, _ = run_screen(issuer, evidence, approval, response=response)
    assert result.clearance.result == "UNKNOWN"


def test_source_hash_integrity_is_mandatory(evidence):
    with pytest.raises(ValidationError, match="SOURCE_HASH_MISMATCH"):
        EthicalEvidence.model_validate(evidence.model_dump() | {"content": "altered actual bytes"})


def test_global_rights_are_required_but_not_duplicated_per_issuer(issuer, evidence, approval):
    first, _ = run_screen(issuer, evidence, approval)
    no_rights = screen_issuer(issuer, (evidence,), now=NOW, previous=first)
    assert no_rights.clearance.result == "UNKNOWN"
    assert "GLOBAL_SOURCE_ETHICAL_USE_RIGHTS_REQUIRED" in no_rights.clearance.reasons
    other = issuer.model_copy(update={"issuer_key": "verified:other", "legal_name": "Other plc"})
    other_evidence = evidence.model_copy(update={"issuer_key": other.issuer_key})
    result, _ = run_screen(other, other_evidence, approval)
    assert result.clearance.result == "PASS"
    assert result.global_rights_evidence_hashes == first.global_rights_evidence_hashes


def test_expired_global_rights_do_not_silently_reuse_pass(issuer, evidence, approval):
    first, _ = run_screen(issuer, evidence, approval)
    result, evaluator = run_screen(
        issuer, evidence, approval.model_copy(update={"valid_until": NOW}), previous=first
    )
    assert result.clearance.result == "UNKNOWN"
    evaluator.assert_not_called()


def test_failed_evaluator_is_secret_free_and_unknown(issuer, evidence, approval):
    evaluator = Mock(side_effect=RuntimeError("https://private.example?token=SECRET_TEST_SENTINEL"))
    result = screen_issuer(
        issuer, (evidence,), now=NOW, source_approvals=(approval,), evaluator=evaluator
    )
    assert result.clearance.result == "UNKNOWN"
    assert "SECRET_TEST_SENTINEL" not in result.model_dump_json()
    assert result.clearance.reasons == ("ETHICAL_EVALUATION_FAILED_OR_UNSUPPORTED",)


@pytest.mark.parametrize("response", ["not json", '{"duplicate":1,"duplicate":2}', '{"ok":NaN}'])
def test_malformed_evaluator_output_fails_closed(issuer, evidence, approval, response):
    result = screen_issuer(
        issuer,
        (evidence,),
        now=NOW,
        source_approvals=(approval,),
        evaluator=Mock(return_value=response),
    )
    assert result.clearance.result == "UNKNOWN"


def test_only_news_cannot_establish_complete_issuer_scope(issuer, evidence, approval):
    result, _ = run_screen(issuer, evidence.model_copy(update={"evidence_kind": "news"}), approval)
    assert result.clearance.result == "UNKNOWN"


def test_all_evidence_is_bounded_without_silent_truncation(issuer, evidence, approval):
    result, evaluator = run_screen(issuer, evidence, approval, maximum_evidence_bytes=10)
    assert result.clearance.result == "UNKNOWN"
    assert "ETHICAL_EVIDENCE_EXCEEDS_BOUNDED_CONTEXT" in result.clearance.reasons
    evaluator.assert_not_called()


def test_prompt_explicitly_treats_documents_as_data_not_instructions(issuer, evidence, approval):
    result, evaluator = run_screen(issuer, evidence, approval)
    system, user = evaluator.call_args.args
    assert "UNTRUSTED DATA" in system
    assert "keyword absence" in system
    assert json.loads(user)["excluded_activities"] == sorted(EXCLUDED_ACTIVITIES)
    assert result.clearance.result == "PASS"


def test_cache_key_does_not_confuse_distinct_verified_issuers(issuer, evidence):
    another = issuer.model_copy(update={"issuer_key": "other-verified-company"})
    assert screening_cache_key(issuer, (evidence,)) != screening_cache_key(another, (evidence,))
