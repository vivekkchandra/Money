"""Synthetic fixtures for issuer screening diagnostics, not qualification evidence."""

import hashlib
from datetime import timedelta

import pytest
from test_universe_reviews import NOW, approve_rights, source, stock

from money.data.provider_probes import ProviderAdmissionReview
from money.qualification.core import QualificationContext
from money.qualification.diagnostics import write_qualification_diagnostics
from money.qualification.first_candidate import _remaining_work
from money.qualification.universe_reviews import _rights, prepare_universe_reviews


@pytest.fixture
def ctx(tmp_path):
    return QualificationContext(tmp_path, tmp_path, {}, NOW)


def approve_global(ctx, *, scopes=None):
    """One real-format global review over synthetic test-only licence bytes."""
    approve_rights(ctx)
    value = ctx.read_json("inputs/provider-rights/eodhd.json")
    value["review"]["ethical_research_datasets"] = scopes or ["issuer-profile"]
    ctx.write_json("inputs/provider-rights/eodhd.json", value)
    # No independently reviewed ethical-source supplement is required.
    ctx.write_json("inputs/universe/source-rights/eodhd.json", {"status": "UNRESOLVED"})


def test_one_global_provider_review_authorizes_ethical_use_without_second_review(ctx):
    approve_global(ctx)
    result = _rights(ctx, "eodhd")
    assert result["provider_rights_current"]
    assert result["ethical_use_current"]
    assert result["approved_datasets"] == ["issuer-profile"]
    assert result["ethical_scope_origin"] == "PROVIDER_REVIEW"
    assert result["valid_until"] == (NOW + timedelta(days=1)).isoformat()
    assert hashlib.sha256(ctx.read_bytes("inputs/provider-rights/eodhd.json")).hexdigest() in result[
        "approval_evidence_hashes"
    ]


@pytest.mark.parametrize("scopes", [["unknown-dataset"], ["issuer-profile", "unknown-dataset"]])
def test_unrecognized_scope_never_grants_ethical_rights(ctx, scopes):
    approve_global(ctx, scopes=scopes)
    assert _rights(ctx, "eodhd")["approved_datasets"] == []


def test_plain_research_wording_does_not_infer_ethical_source_permission(ctx):
    approve_global(ctx)
    value = ctx.read_json("inputs/provider-rights/eodhd.json")
    value["review"].pop("ethical_research_datasets")
    ctx.write_json("inputs/provider-rights/eodhd.json", value)
    result = _rights(ctx, "eodhd")
    assert result["provider_rights_current"]
    assert not result["ethical_use_current"]


def test_expired_global_rights_cannot_admit_ethical_source_facts(ctx):
    approve_global(ctx)
    ctx.now = NOW + timedelta(days=1)
    assert not _rights(ctx, "eodhd")["provider_rights_current"]
    assert _rights(ctx, "eodhd")["approved_datasets"] == []


def test_optional_scope_field_preserves_legacy_review_serialization(ctx):
    approve_rights(ctx)
    value = ctx.read_json("inputs/provider-rights/eodhd.json")
    payload = {
        **value["review"],
        "rights_evidence_hash": hashlib.sha256(ctx.read_bytes(value["rights_evidence_file"])).hexdigest(),
    }
    assert "ethical_research_datasets" not in ProviderAdmissionReview.model_validate(payload).model_dump()
    assert _rights(ctx, "eodhd")["ethical_scope_origin"] == "LEGACY_GLOBAL_SOURCE_REVIEW"


def test_preparation_does_not_create_second_ethics_or_source_review_templates(ctx):
    prepare_universe_reviews(ctx, [], {})
    assert ctx.read_json("inputs/universe/ethics.json") is None
    assert ctx.read_json("inputs/universe/source-rights/eodhd.json") is None
    assert ctx.read_json("inputs/provider-rights/eodhd.json")["review"]["reviewed_by"] is None


def test_only_unknown_screenings_require_human_evidence_resolution(ctx):
    rows = [
        stock(isin=isin, ethical_state=state, trading212_id=f"FIX{index}l_EQ")
        for index, (isin, state) in enumerate([
            ("GB00BH4HKS39", "PASS"), ("JE00B4T3BW64", "FAIL"),
            ("GB00B63QSB39", "UNKNOWN"), ("GB0006389398", "NOT_YET_SCREENED"),
        ])
    ]
    summary = prepare_universe_reviews(ctx, rows, {})
    queue = ctx.read_json("outputs/ethics-work-queue.json")
    assert summary["ethical_screening_counts"] == {
        "PASS": 1, "FAIL": 1, "UNKNOWN": 1, "NOT_YET_SCREENED": 1,
    }
    assert [item["screening_state"] for item in queue["human_review_groups"]] == ["UNKNOWN"]
    assert len(queue["machine_work_groups"]) == 1
    assert not summary["ethics_approved_by_preparation"]


def test_verified_shared_issuer_pass_produces_one_reuse_record(ctx):
    rows = [
        stock(ethical_state="PASS", companies_house_state="MAPPED",
              companies_house_number="00000001", legal_company_name="FIXTURE PLC"),
        stock(ethical_state="PASS", trading212_id="FIXBl_EQ", isin="JE00B4T3BW64",
              companies_house_state="MAPPED", companies_house_number="00000001",
              legal_company_name="FIXTURE PLC"),
    ]
    for row in rows:
        source(ctx, row, company=True)
    summary = prepare_universe_reviews(ctx, rows, {})
    assert summary["ethical_screening_counts"]["PASS"] == 1
    assert summary["security_count"] == 2
    queue = ctx.read_json("outputs/ethics-work-queue.json")
    assert not queue["human_review_groups"]
    assert "Reuse" in queue["groups"][0]["next_action"]


def test_pass_diagnostics_keep_other_gates_without_an_extra_ethical_review(ctx):
    approve_global(ctx)
    row = stock(ethical_state="PASS", reasons=[], ethical_screening={"result": "PASS"})
    blockers = _remaining_work(ctx, row, None)
    codes = {item["code"] for item in blockers}
    assert not any("ETHICAL" in code for code in codes)
    assert "SUPPLEMENTAL_REVIEW_REQUIRED" in codes
    assert "HISTORICAL_PUBLICATION_AND_UNIVERSE_EVIDENCE_REQUIRED" in codes


def test_dag_ethical_counts_only_include_current_candidate_universe(ctx):
    write_qualification_diagnostics(ctx, master={"stocks": [
        stock(universe_member=True, ethical_state="UNKNOWN"),
        stock(universe_member=True, ethical_state="NOT_YET_SCREENED"),
        stock(universe_member=False, ethical_state="NOT_YET_SCREENED", instrument_type="ETF"),
    ]}, report={"blockers": []})
    dag = ctx.read_json("outputs/qualification-blocker-dag.json")
    node = next(node for node in dag["nodes"] if node["id"] == "ethical_evidence")
    assert node["screening_counts"] == {
        "PASS": 0, "FAIL": 0, "UNKNOWN": 1, "NOT_YET_SCREENED": 1,
    }
