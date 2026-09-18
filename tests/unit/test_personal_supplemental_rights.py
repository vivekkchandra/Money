"""Synthetic supplemental-source policy regressions, not licence evidence."""

from datetime import timedelta

import pytest
from test_filing_documents import NOW
from test_qualification_providers import context, supplemental_source_fixture

from money.data.qualification import ProviderQualification
from money.qualification import providers
from money.usage_policy import UsageMode


@pytest.mark.parametrize("rights_field", [None, "omitted", "inputs/missing-rights.txt"])
def test_personal_supplemental_does_not_require_rights_signature_or_bytes(
    tmp_path, monkeypatch, rights_field
):
    ctx = context(tmp_path, now=NOW)
    ctx.environ["MONEY_USAGE_MODE"] = "personal_research"
    result, admission = supplemental_source_fixture(ctx, monkeypatch)
    if rights_field == "omitted":
        admission.pop("rights_evidence_file")
    else:
        admission["rights_evidence_file"] = rights_field
    ctx.write_json("inputs/market-source.json", admission)
    preserved = ctx.read_bytes("inputs/market-source.json")
    refs = set()
    providers._additional_sources(ctx, result, refs)
    assert len(result["additional_provider_qualifications"]) == 1
    qualified = ProviderQualification.model_validate(result["additional_provider_qualifications"][0])
    qualified.require("financial", NOW, usage_mode=UsageMode.PERSONAL_RESEARCH)
    assert qualified.personal_use.rights_status == "UNVERIFIED_PERSONAL_USE"
    assert qualified.production_qualified is False
    assert qualified.qualified_by is None
    assert qualified.redistribution == "PROHIBITED"
    assert ctx.read_bytes("inputs/market-source.json") == preserved
    assert any(digest == qualified.qualification_report_hash for digest, _ in refs)
    with pytest.raises(ValueError, match="PROVIDER_UNQUALIFIED"):
        qualified.require("financial", NOW)
    providers._filter_source_coverage(ctx, result)
    assert len(result["instruments"]) == 1


@pytest.mark.parametrize("rights_field", [None, "omitted", "inputs/missing-rights.txt"])
def test_commercial_supplemental_still_requires_rights_bytes(tmp_path, monkeypatch, rights_field):
    ctx = context(tmp_path, now=NOW)
    result, admission = supplemental_source_fixture(ctx, monkeypatch)
    if rights_field == "omitted":
        admission.pop("rights_evidence_file")
    else:
        admission["rights_evidence_file"] = rights_field
    ctx.write_json("inputs/market-source.json", admission)
    providers._additional_sources(ctx, result, set())
    assert result["additional_provider_qualifications"] == []
    providers._filter_source_coverage(ctx, result)
    assert result["instruments"] == []


@pytest.mark.parametrize("failure", [
    "unsigned_technical_review", "same_technical_reviewer", "expired_technical_review",
    "missing_source_bytes", "unproved_publication", "record_mismatch",
])
def test_personal_supplemental_preserves_technical_source_and_pit_gates(
    tmp_path, monkeypatch, failure
):
    ctx = context(tmp_path, now=NOW)
    ctx.environ["MONEY_USAGE_MODE"] = "personal_research"
    result, admission = supplemental_source_fixture(ctx, monkeypatch)
    admission["rights_evidence_file"] = None
    if failure == "unsigned_technical_review":
        admission["review"]["status"] = "UNRESOLVED"
    elif failure == "same_technical_reviewer":
        admission["review"]["reviewed_by"] = admission["review"]["prepared_by"]
    elif failure == "expired_technical_review":
        admission["review"]["valid_until"] = (NOW - timedelta(seconds=1)).isoformat()
    elif failure == "missing_source_bytes":
        admission["observations"][0]["source_evidence_file"] = "inputs/missing-source.txt"
    elif failure == "unproved_publication":
        admission["publication_times"] = "ORIGINAL_PUBLICATION_VERIFIED"
    elif failure == "record_mismatch":
        admission["observations"][0]["evidence"]["hash"] = ""
        admission["observations"][0]["evidence"]["source_id"] = "unmatched-synthetic-record"
    ctx.write_json("inputs/market-source.json", admission)
    providers._additional_sources(ctx, result, set())
    assert result["additional_provider_qualifications"] == []
    providers._filter_source_coverage(ctx, result)
    assert result["instruments"] == []
