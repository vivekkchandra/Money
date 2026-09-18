"""Synthetic personal-use fixtures; no source licence or issuer is approved here."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from test_universe_reviews import NOW, dossiers, source, stock

from money.data.identifiers import InstrumentIdentifiers
from money.qualification.core import QualificationContext
from money.qualification.diagnostics import write_qualification_diagnostics
from money.qualification.first_candidate import _remaining_work
from money.qualification.universe_ethics import _evaluator, screen_universe
from money.qualification.universe_reviews import _rights, prepare_universe_reviews
from money.research.ethics import GlobalSourceApproval
from money.usage_policy import PersonalUseAudit


@pytest.fixture
def ctx(tmp_path):
    return QualificationContext(tmp_path, Path.cwd(), {"MONEY_USAGE_MODE": "personal_research"}, NOW)


def test_unsigned_personal_source_use_is_audit_not_approval(ctx):
    result = _rights(ctx, "eodhd")
    assert result["rights_status"] == "UNVERIFIED_PERSONAL_USE"
    assert result["approved_datasets"] == []
    assert not result["provider_rights_current"]
    assert not result["ethical_use_current"]
    assert "issuer-profile" in result["admissible_datasets"]
    assert result["ethical_use_admissible"]
    audit = PersonalUseAudit.model_validate(result["usage_audit"])
    assert not any((audit.redistribution, audit.public_raw_display, audit.resale, audit.external_sharing))
    with pytest.raises(ValueError, match="FORBIDDEN"):
        audit.require_release()
    assert ctx.read_json("inputs/provider-rights/eodhd.json") is None
    assert not any(field in result["usage_audit"] for field in ("reviewed_by", "reviewed_at", "approved"))


def test_personal_dossier_admits_exact_source_content_without_labeling_rights_approved(ctx):
    row = stock()
    source(ctx, row)
    result = prepare_universe_reviews(ctx, [row], {})
    dossier = dossiers(ctx)[0]
    assert not dossier["approved_source_facts"]
    assert len(dossier["admissible_source_facts"]) == 1
    assert dossier["sources"][0]["content_use"] == "UNVERIFIED_PERSONAL_USE"
    assert result["rights_approved_fact_sources"] == 0
    assert result["admissible_fact_sources"] == 1
    assert ctx.read_json("inputs/provider-rights/eodhd.json")["status"] == "UNRESOLVED"


def test_commercial_mode_still_needs_genuine_review(ctx):
    ctx.environ = {}
    rights = _rights(ctx, "eodhd")
    assert rights["admissible_datasets"] == []
    assert rights["usage_audit"] is None
    codes = {item["code"] for item in _remaining_work(ctx, stock(reasons=[]), None)}
    assert "PROVIDER_RIGHTS_REVIEW_REQUIRED:eodhd" in codes
    assert "ETHICAL_SOURCE_RIGHTS_REVIEW_REQUIRED:eodhd" in codes


def test_personal_candidate_retains_dataset_and_supplemental_gates_not_unsigned_rights(ctx):
    row = stock(reasons=["PROVIDER_RIGHTS_AND_DATASET_QUALIFICATION_REQUIRED"])
    codes = {item["code"] for item in _remaining_work(ctx, row, None)}
    assert "PROVIDER_DATASET_QUALIFICATION_REQUIRED" in codes
    assert "SUPPLEMENTAL_REVIEW_REQUIRED" in codes
    assert "HISTORICAL_PUBLICATION_AND_UNIVERSE_EVIDENCE_REQUIRED" in codes
    assert not any("RIGHTS" in code for code in codes)


def test_personal_dag_treats_rights_as_release_dependency_not_research_approval(ctx):
    ctx.environ["MONEY_QLIB_ENABLED"] = "false"
    result = write_qualification_diagnostics(ctx, master={"stocks": []}, report={"blockers": [
        {"code": "PROVIDER_RIGHTS_REVIEW_REQUIRED:eodhd", "action": "Historical manual review."},
    ]})
    nodes = {item["id"]: item for item in result["nodes"]}
    for stage in ("basic_identity", "snapshot", "independent_reports"):
        assert "provider_rights" not in nodes[stage]["depends_on"]
        assert "ethical_evidence" not in nodes[stage]["depends_on"]
    assert "ethical_evidence" not in nodes
    assert all("PROVIDER_RIGHTS" not in item["code"] for item in result["blockers"])
    assert result["legacy_non_research_requirements"][0]["code"] == "PROVIDER_RIGHTS_REVIEW_REQUIRED:eodhd"
    assert "provider_rights" in nodes["release"]["depends_on"]
    assert not nodes["provider_rights"]["blocking_for_personal_research"]
    assert nodes["provider_rights"]["commercial_public_release_blocked"]
    assert not result["qlib_enabled"]
    assert result["lean_mandatory"]
    assert "first_pass_locked" in nodes["lean"]["depends_on"]
    assert "qlib" not in nodes


def test_personal_unverified_source_basis_cannot_pretend_to_be_approved(ctx):
    result = _rights(ctx, "eodhd")
    kwargs = {
        "provider": "eodhd", "evidence_hashes": result["source_use_evidence_hashes"],
        "valid_until": NOW + timedelta(days=30), "rights_status": "UNVERIFIED_PERSONAL_USE",
    }
    with pytest.raises(ValueError, match="AUDIT_REQUIRED"):
        GlobalSourceApproval(**kwargs)
    record = GlobalSourceApproval(**kwargs, personal_use=result["usage_audit"])
    assert record.rights_status == "UNVERIFIED_PERSONAL_USE"
    with pytest.raises(ValueError, match="NOT_REVIEWED_APPROVAL"):
        GlobalSourceApproval(**{**kwargs, "rights_status": "REVIEWED"}, personal_use=result["usage_audit"])


@pytest.mark.parametrize("endpoint,allowed", [
    ("http://127.0.0.1:11434/v1/chat/completions", True),
    ("http://[::1]:11434/v1/chat/completions", True),
    ("https://inference.example/v1/chat/completions", False),
])
def test_personal_disclosures_are_not_sent_to_remote_inference(ctx, monkeypatch, endpoint, allowed):
    class Selection:
        def __init__(self):
            self.endpoint = endpoint

        def inference(self, environ):
            return self

        def complete(self, system, user):
            raise AssertionError("Selection must not execute inference")

    monkeypatch.setattr(
        "money.research.inference_config.load_inference_selections", lambda *args: {"crewai": Selection()}
    )
    assert (_evaluator(ctx) is not None) is allowed


def test_official_issuer_document_can_supply_one_pass_without_fundamentals(ctx, monkeypatch):
    identifiers = InstrumentIdentifiers(
        ticker="FIX", company_name="Fixture PLC", trading212_id="FIXl_EQ", exchange_ticker="FIX",
        exchange="LSE", isin="GB00BH4HKS39", quote_currency="GBX",
        provider_symbols=(("eodhd", "FIX.LSE"),), verified_at=NOW,
        valid_until=NOW + timedelta(days=1), source="Synthetic exact mapping",
    )
    row = stock(identity_valid=True, identifiers=identifiers.model_dump(mode="json"))
    content = (
        "Fixture PLC's only material group operations, subsidiaries and revenue streams are "
        "manufacturing soft drinks. This consolidated description covers 100% of group revenues "
        "and activities with no additional operating segments or investments."
    )
    raw = content.encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = "inputs/issuer-sources/fixture.txt"
    ctx.write_bytes(path, raw)
    document = {
        "provider": "official-issuer", "dataset": "business-disclosure",
        "text_path": path, "text_sha256": digest, "raw_path": path, "raw_sha256": digest,
        "source_url": "https://issuer.example/group", "retrieved_at": NOW,
        "published_at": None, "evidence_kind": "business_profile",
    }
    # Retrieval and exact corroboration are tested in test_issuer_sources; this
    # fixture isolates the ethics consumer's use of validated source documents.
    monkeypatch.setattr("money.qualification.universe_ethics.validated_issuer_documents", lambda *args: [document])
    calls = []

    def evaluate(system, user):
        calls.append(user)
        request = json.loads(user)
        supplied = request["documents"][0]
        citation = {"source_id": supplied["source_id"], "evidence_hash": digest, "quote": content}
        return json.dumps({
            "issuer_key": request["verified_issuer"]["issuer_key"], "legal_name": "Fixture PLC",
            "complete_material_business_scope": True, "scope_citations": [citation],
            "business_activities": [{"activity": "soft_drinks", "citations": [citation]}],
            "assessments": [{
                "category": category, "conclusion": "NO_MATERIAL_EXPOSURE",
                "basis": "COMPLETE_BUSINESS_SCOPE", "citations": [citation],
                "rationale": "Only soft-drinks operations comprise the complete consolidated group business.",
            } for category in request["excluded_activities"]],
        })

    monkeypatch.setattr("money.qualification.universe_ethics._evaluator", lambda _: evaluate)
    first = screen_universe(ctx, [row])
    assert first[row["isin"]]["clearance"].result == "PASS"
    second = screen_universe(ctx, [row])
    assert second[row["isin"]]["clearance"] == first[row["isin"]]["clearance"]
    assert len(calls) == 1
    assert ctx.read_json("inputs/provider-rights/official-issuer.json") is None
    ctx.environ = {}
    assert screen_universe(ctx, [row])[row["isin"]]["clearance"].result == "UNKNOWN"
