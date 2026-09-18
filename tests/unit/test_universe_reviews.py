"""Synthetic review-preparation fixtures; never production qualification proof."""

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from money.data.security import FetchResult, ProviderFailure
from money.qualification.core import QualificationContext
from money.qualification.universe import _review_work
from money.qualification.universe_reviews import DOCUMENTS, prepare_universe_reviews

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)


@pytest.fixture
def ctx(tmp_path):
    return QualificationContext(tmp_path, tmp_path, {}, NOW)


def stock(**updates):
    return {
        "universe_member": True, "trading212_id": "FIXl_EQ", "name": "Fixture PLC",
        "isin": "GB00BH4HKS39", "quote_currency": "GBX", "eodhd_symbol": "FIX.LSE",
        "eodhd_mapping_state": "MAPPED", "eodhd_identity": {"Name": "Fixture PLC", "Code": "FIX"},
        "qualification_state": "UNRESOLVED_ETHICAL", **updates,
    }


def source(ctx, row, *, company=False, expired=False):
    response = {
        "ISIN": row["isin"], "Code": "FIX", "Name": "Fixture PLC", "CurrencyCode": "GBX",
        "IsDelisted": False, "Type": "Common Stock", "CountryISO": "GB",
        "Description": "Synthetic detailed business text, not ethical approval.",
    }
    host, path = "eodhd.com", "/api/fundamentals/FIX.LSE"
    if company:
        host, path = "api.company-information.service.gov.uk", "/company/00000001"
        response = {
            "company_number": "00000001", "company_name": "FIXTURE PLC",
            "company_status": "active", "type": "plc", "sic_codes": ["00000"],
        }
    stamp = NOW - timedelta(days=2) if expired else NOW
    digest, artifact = ctx.artifact({
        "version": "money-bulk-provider-response-v2",
        "request": {"version": 2, "host": host, "path": path, "query": []},
        "observed_at": stamp.isoformat(), "valid_until": (stamp + timedelta(days=1)).isoformat(),
        "response": response,
    })
    row.setdefault("provider_evidence", []).append({"sha256": digest, "path": artifact})
    return artifact


def approve_rights(ctx, provider="eodhd", *, provider_approved=True, expired=False):
    ctx.write_bytes("inputs/synthetic-license.txt", b"Synthetic test licence; not real evidence.")
    stamp = NOW - timedelta(days=2) if expired else NOW
    ctx.write_json(f"inputs/provider-rights/{provider}.json", {
        "status": "REVIEWED" if provider_approved else "UNRESOLVED",
        "rights_evidence_file": "inputs/synthetic-license.txt",
        "review": {
            "provider": provider, "reviewed_by": "Synthetic reviewer", "usage_purpose": "research",
            "storage_policy": "Synthetic bounded local use", "redistribution": "PROHIBITED",
            "attribution": "Synthetic", "source_documentation": "Synthetic test documentation",
            "reviewed_at": stamp.isoformat(), "valid_until": (stamp + timedelta(days=1)).isoformat(),
        },
    })
    ctx.write_json(f"inputs/universe/source-rights/{provider}.json", {
        "review": {
            "status": "REVIEWED", "prepared_by": "Synthetic preparer",
            "reviewed_by": "Synthetic reviewer", "reviewed_at": stamp.isoformat(),
            "valid_until": (stamp + timedelta(days=1)).isoformat(),
        },
        "provider": provider, "permitted_use": "ethical-research",
        "dataset_scopes": ["issuer-profile"] if provider == "eodhd" else ["company", "filing"],
        "evidence_files": ["inputs/synthetic-license.txt"],
    })


def dossiers(ctx):
    queue = ctx.read_json("outputs/ethics-work-queue.json")
    return [ctx.read_json(item["dossier"]) for item in queue["groups"]]


def test_global_unsigned_templates_preserve_every_human_input_on_resume(ctx):
    ctx.write_json("inputs/universe/account-scope.json", {"operator_notes": "Do not overwrite."})
    before = ctx.read_bytes("inputs/universe/account-scope.json")
    result = prepare_universe_reviews(ctx, [stock()], {"retrieved_at": NOW.isoformat()})
    assert result["security_count"] == result["issuer_groups"] == 1
    assert ctx.read_bytes("inputs/universe/account-scope.json") == before
    rights = ctx.read_json("inputs/provider-rights/eodhd.json")
    assert rights["status"] == "UNRESOLVED"
    assert rights["review"]["reviewed_by"] is None
    assert rights["review"]["reviewed_at"] is None
    assert ctx.read_json("inputs/universe/ethics.json") is None
    assert not result["production_qualified"]
    assert result["legacy_account_review"] == "DEPRECATED_IGNORED_NOT_APPROVED"
    assert (ctx.root / "outputs/REVIEW_TASKS.md").is_file()
    assert not (ctx.root / "inputs/instruments").exists()


def test_no_account_type_buy_claim_or_signature_is_fabricated(ctx):
    prepare_universe_reviews(ctx, [], {})
    assert ctx.read_json("inputs/universe/account-scope.json") is None
    assert ctx.read_json("inputs/universe/account-scope.schema.json") is None
    for provider in ("eodhd", "companies-house"):
        rights = ctx.read_json(f"inputs/provider-rights/{provider}.json")
        assert rights["status"] == "UNRESOLVED"
        assert rights["review"]["reviewed_by"] is None
        assert rights["review"]["reviewed_at"] is None


def test_superseded_account_review_instructions_are_preserved_as_audit_only(ctx):
    original = b"Historical unsigned account review instructions."
    ctx.write_bytes("outputs/ACCOUNT_SCOPE_REVIEW.md", original)
    prepare_universe_reviews(ctx, [], {})
    current = ctx.read_bytes("outputs/ACCOUNT_SCOPE_REVIEW.md")
    digest = hashlib.sha256(original).hexdigest()
    assert ctx.verify_artifact(digest, f"artifacts/{digest}.bin") == original
    assert current.startswith(b"# Deprecated account-scope review")
    assert b"ignored, not approved" in current
    prepare_universe_reviews(ctx, [], {})
    assert ctx.read_bytes("outputs/ACCOUNT_SCOPE_REVIEW.md") == current


def test_unapproved_source_content_is_not_copied_into_ethical_dossiers(ctx):
    row = stock(issuer_facts={"Description": "Untrusted derived text"})
    source(ctx, row)
    before = copy.deepcopy(row)
    prepare_universe_reviews(ctx, [row], {})
    dossier = dossiers(ctx)[0]
    assert row == before
    assert dossier["approved_source_facts"] == []
    assert dossier["sources"][0]["content_use"] == "REFERENCES_ONLY"
    assert "business text" not in json.dumps(dossier)
    assert "Untrusted derived text" not in json.dumps(dossier)
    assert not dossier["approval_granted_by_preparation"]


def test_both_rights_reviews_admit_verified_facts_but_not_ethics_approval(ctx):
    row = stock(issuer_facts={"Description": "Forged projection"})
    source(ctx, row)
    approve_rights(ctx)
    prepare_universe_reviews(ctx, [row], {})
    dossier = dossiers(ctx)[0]
    assert "Synthetic detailed business text" in json.dumps(dossier["approved_source_facts"])
    assert "Forged projection" not in json.dumps(dossier)
    assert dossier["status"] == "DOSSIER_PREPARATION_ONLY"
    assert "complete_material_exposure_review" not in dossier
    assert ctx.read_json("inputs/universe/ethics.json") is None


def test_recorded_ethical_state_is_not_overridden_by_preparation(ctx):
    row = stock(ethical_state="ETHICALLY_CLEARED")
    prepare_universe_reviews(ctx, [row], {})
    dossier = dossiers(ctx)[0]
    assert dossier["members"][0]["recorded_ethical_state"] == "ETHICALLY_CLEARED"
    assert dossier["status"] == "DOSSIER_PREPARATION_ONLY"
    assert dossier["human_review_status"] == "NOT_REQUIRED"
    assert dossier["unresolved_exposures"] == dossier["required_exclusions"]
    assert not dossier["approval_granted_by_preparation"]


@pytest.mark.parametrize("case", ["provider-unapproved", "rights-expired", "source-expired", "corrupt", "wrong-isin"])
def test_invalid_rights_or_source_never_creates_factual_dossier(ctx, case):
    row = stock()
    path = source(ctx, row, expired=case == "source-expired")
    approve_rights(ctx, provider_approved=case != "provider-unapproved", expired=case == "rights-expired")
    if case == "corrupt":
        ctx.write_bytes(path, b"Corrupted synthetic evidence")
    if case == "wrong-isin":
        row["isin"] = "JE00B4T3BW64"
    prepare_universe_reviews(ctx, [row], {})
    assert dossiers(ctx)[0]["approved_source_facts"] == []


def test_verified_issuer_groups_share_classes_but_name_alone_never_groups(ctx):
    rows = [
        stock(companies_house_state="MAPPED", companies_house_number="00000001", legal_company_name="FIXTURE PLC"),
        stock(trading212_id="FIXBl_EQ", isin="JE00B4T3BW64", companies_house_state="MAPPED",
              companies_house_number="00000001", legal_company_name="FIXTURE PLC"),
        stock(trading212_id="OTHERl_EQ", isin="GB00B63QSB39"),
    ]
    for row in rows[:2]:
        source(ctx, row, company=True)
    summary = prepare_universe_reviews(ctx, rows, {})
    assert summary["issuer_groups"] == 2
    company = next(item for item in dossiers(ctx) if item["grouping_basis"] == "VERIFIED_COMPANY_NUMBER")
    assert len(company["members"]) == 2
    assert company["approved_source_facts"] == []
    assert "sic_codes" not in json.dumps(company)


def test_nonmembers_excluded_and_bulk_review_work_redacts_source_text(ctx):
    rows = [stock(issuer_facts={"Description": "not rights-approved"}, recent_accounts_filings=[{"secret_text": "omit"}]),
            stock(universe_member=False)]
    summary = prepare_universe_reviews(ctx, rows, {})
    assert summary["security_count"] == 1
    work = _review_work(rows, {"scope": "LOCAL_INFERENCE_ONLY"})
    assert "issuer_facts" not in work["instruments"][0]
    assert "recent_accounts_filings" not in work["instruments"][0]
    assert work["instruments"][0]["source_content_use"] == "REFERENCES_ONLY"


class DocumentsFetcher:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def get(self, url, *, mime_types):
        self.calls.append(url)
        assert url in DOCUMENTS.values()
        assert "api_token" not in url
        if self.fail:
            raise ProviderFailure("PROVIDER_UNAVAILABLE")
        return FetchResult(b"Synthetic all available instruments documentation", "text/markdown")


def test_document_capture_is_bounded_exact_and_does_not_approve_scope(ctx, monkeypatch):
    monkeypatch.setattr("money.qualification.universe_reviews.utc_now", lambda: NOW)
    fetcher = DocumentsFetcher()
    result = prepare_universe_reviews(ctx, [], {}, fetch_documents=True, fetcher=fetcher)
    assert len(fetcher.calls) == result["official_documents_captured"] == 4
    documents = ctx.read_json("outputs/universe-review-documents.json")
    for document in documents["documents"]:
        raw = ctx.read_bytes(document["path"])
        assert hashlib.sha256(raw).hexdigest() == document["sha256"]
        assert document["observed_at"] == NOW.isoformat()
    assert "account_binding_established" not in documents
    assert "current_buy_availability_established" not in documents
    assert not documents["rights_approved"]
    prepare_universe_reviews(ctx, [], {}, fetch_documents=True, fetcher=fetcher)
    assert len(fetcher.calls) == 4


def test_offline_means_no_document_network_even_with_explicit_fetch(ctx):
    fetcher = DocumentsFetcher()
    result = prepare_universe_reviews(ctx, [], {}, fetch_documents=True, offline=True, fetcher=fetcher)
    assert not fetcher.calls
    assert result["official_documents_captured"] == 0


def test_unavailable_documents_leave_only_links_and_no_fake_hash(ctx, monkeypatch):
    monkeypatch.setattr("money.qualification.universe_reviews.utc_now", lambda: NOW)
    fetcher = DocumentsFetcher(fail=True)
    prepare_universe_reviews(ctx, [], {}, fetch_documents=True, fetcher=fetcher)
    documents = ctx.read_json("outputs/universe-review-documents.json")["documents"]
    assert all(item["status"] == "CAPTURE_UNAVAILABLE" for item in documents)
    assert all("sha256" not in item for item in documents)
    prepare_universe_reviews(ctx, [], {}, fetch_documents=True, fetcher=fetcher)
    assert len(fetcher.calls) == 4
    ctx.now += timedelta(minutes=6)
    prepare_universe_reviews(ctx, [], {}, fetch_documents=True, fetcher=fetcher)
    assert len(fetcher.calls) == 8


def test_known_secrets_cannot_be_persisted_in_review_preparation(ctx):
    ctx.environ = {"EODHD_API_KEY": "synthetic-forbidden-credential"}
    with pytest.raises(ValueError, match="QUALIFICATION_SECRET_DETECTED"):
        prepare_universe_reviews(ctx, [stock(name="synthetic-forbidden-credential")], {})
    for path in ctx.root.rglob("*.json"):
        assert b"synthetic-forbidden-credential" not in path.read_bytes()
