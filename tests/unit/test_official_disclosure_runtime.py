"""Synthetic source fixtures test admission, never constitute production evidence."""

import hashlib
from datetime import timedelta

import pytest
from test_filing_documents import (
    NOW,
    identifiers,
    prepare_snapshot_builder,
    qualification,
    reviewed_manifest,
)

from money.data.official_disclosures import (
    OfficialDisclosureProof,
    require_official_disclosures,
    validate_disclosure_records,
)
from money.data.source_policy import IssuerSourcePolicy
from money.research import live
from money.schemas.contracts import DocumentFact, EvidenceRecord, FinancialFact, content_hash
from money.usage_policy import PersonalUseAudit, UsageMode

URL = "https://issuer.example/reports/annual.xhtml"
PROVIDER = "official-issuer"
SOURCE_ID = "annual-report-synthetic"
BYTES = {
    "document": b"synthetic financial document; not a real issuer report",
    "identity": b"synthetic exact identity review",
    "conversion": b"synthetic technical conversion review",
    "currency": b"synthetic original GBP units review",
    "publication": b"synthetic original publication review",
}
DIGESTS = {key: hashlib.sha256(value).hexdigest() for key, value in BYTES.items()}


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(live, "utc_now", lambda: NOW)


def records():
    common = {
        "snapshot_id": "synthetic-source-review",
        "source": "Synthetic official issuer annual accounts",
        "provider": PROVIDER,
        "source_id": SOURCE_ID,
        "canonical_source_id": f"{PROVIDER}:{SOURCE_ID}",
        "observation_time": NOW - timedelta(days=200),
        "publication_time": NOW,
        "retrieval_time": NOW,
        "fresh_until": NOW + timedelta(hours=1),
        "pit_safe": True,
        "critical": True,
    }
    return (
        EvidenceRecord(
            **common,
            payload=DocumentFact(
                kind="filing", title="Synthetic annual accounts", excerpt="Fixture only", url=URL,
            ),
        ),
        EvidenceRecord(
            **common,
            payload=FinancialFact(
                metric="revenue", value=5000, unit="GBP", period_end=NOW - timedelta(days=200),
            ),
        ),
    )


def proof(evidence):
    ids = identifiers()
    return OfficialDisclosureProof(
        ticker=ids.ticker,
        trading212_id=ids.trading212_id,
        isin=ids.isin,
        eodhd_symbol="VOD.LSE",
        legal_name=ids.company_name,
        jurisdiction="GB",
        company_number=ids.companies_house_number,
        provider=PROVIDER,
        source_url=URL,
        source_id=SOURCE_ID,
        document_sha256=DIGESTS["document"],
        identity_evidence_hash=DIGESTS["identity"],
        conversion_evidence_hash=DIGESTS["conversion"],
        accounting_currency_evidence_hash=DIGESTS["currency"],
        accounting_currency="GBP",
        publication_times="AS_RETRIEVED",
        publication_time=NOW,
        retrieval_time=NOW,
        fresh_until=NOW + timedelta(hours=1),
        evidence_hashes=tuple(item.hash for item in evidence),
    )


def source_qualification():
    return qualification().model_copy(update={"provider": PROVIDER})


def official_manifest():
    raw = reviewed_manifest().model_dump()
    evidence = records()
    selected = proof(evidence)
    raw.update({
        "issuer_source_policy": "official_disclosures",
        "filing_document_storage_hosts": [],
        "qlib_enabled": False,
        "qlib_registry_id": None,
        "qlib_artifact_hash": None,
    })
    raw["instruments"][0].update({
        "filing_documents": [],
        "supplemental_evidence": evidence,
        "official_disclosures": [selected],
        "issuer_jurisdiction": "GB",
        "issuer_jurisdiction_proof_hash": selected.identity_evidence_hash,
    })
    raw["provider_qualifications"] = [
        item for item in raw["provider_qualifications"] if item["provider"] != "companies-house"
    ] + [source_qualification()]
    return live.LiveManifest.model_validate(raw)


def require(selected=None, evidence=None, **kwargs):
    evidence = records() if evidence is None else evidence
    selected = proof(evidence) if selected is None else selected
    return require_official_disclosures(
        (selected,), identifiers(), evidence,
        kwargs.pop("qualifications", {PROVIDER: source_qualification()}),
        kwargs.pop("now", NOW),
        jurisdiction="GB", jurisdiction_proof_hash=DIGESTS["identity"], **kwargs,
    )


def test_official_source_is_explicit_and_requires_no_ch_provider():
    manifest = official_manifest()
    assert manifest.issuer_source_policy == IssuerSourcePolicy.OFFICIAL_DISCLOSURES
    assert not any(item.provider == "companies-house" for item in manifest.provider_qualifications)
    assert not manifest.qlib_enabled
    assert manifest.lean is not None
    assert manifest.lean_qualification is not None


def test_legacy_route_keeps_companies_house_gate():
    raw = reviewed_manifest().model_dump()
    raw["provider_qualifications"] = [
        item for item in raw["provider_qualifications"] if item["provider"] != "companies-house"
    ]
    with pytest.raises(ValueError, match="REQUIRED_PROVIDER_MISSING"):
        live.LiveManifest.model_validate(raw)


@pytest.mark.parametrize("field", [
    "document_sha256", "identity_evidence_hash", "conversion_evidence_hash",
    "accounting_currency_evidence_hash",
])
def test_capture_without_required_technical_proofs_cannot_admit(field):
    raw = proof(records()).model_dump()
    raw.pop(field)
    with pytest.raises(ValueError):
        OfficialDisclosureProof.model_validate(raw)


@pytest.mark.parametrize("field,value", [
    ("ticker", "OTHER.L"), ("trading212_id", "OTHERl_EQ"), ("isin", "GB0006389398"),
    ("eodhd_symbol", "OTHER.LSE"), ("legal_name", "Unrelated Issuer PLC"),
    ("company_number", "99999999"),
])
def test_identity_conflict_still_rejects(field, value):
    evidence = records()
    selected = OfficialDisclosureProof.model_validate(proof(evidence).model_dump() | {field: value})
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        require(selected, evidence)


def test_official_company_number_does_not_claim_ch_api_mapping():
    evidence = records()
    ids = identifiers().model_copy(update={"companies_house_number": None})
    assert validate_disclosure_records(
        proof(evidence), ids, evidence, issuer_company_number="01833679",
    ) == evidence
    assert ids.companies_house_number is None
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        validate_disclosure_records(
            proof(evidence), ids, evidence, issuer_company_number="99999999",
        )


def test_pdf_or_filing_capture_alone_cannot_qualify_financial():
    evidence = records()
    second_filing = EvidenceRecord.model_validate(
        evidence[0].model_dump() | {"evidence_id": "other-filing", "hash": ""}
    )
    filing_only = (evidence[0], second_filing)
    with pytest.raises(ValueError, match="FINANCIAL_AND_FILING_REQUIRED"):
        require(proof(filing_only), filing_only)


@pytest.mark.parametrize("mutation", ["units", "conflict", "retrieval", "future_period", "source"])
def test_data_quality_and_extraction_provenance_remain_required(mutation):
    evidence = records()
    raw = evidence[1].model_dump()
    if mutation == "units":
        raw["payload"]["unit"] = "GBX"
    elif mutation == "conflict":
        raw["conflicting"] = True
    elif mutation == "retrieval":
        raw["retrieval_time"] = NOW + timedelta(seconds=1)
    elif mutation == "future_period":
        raw["payload"]["period_end"] = NOW + timedelta(days=1)
    else:
        raw["source_id"] = "other-source"
    changed = (evidence[0], EvidenceRecord.model_validate(raw | {"hash": ""}))
    with pytest.raises(ValueError, match="OFFICIAL_DISCLOSURE_"):
        require(proof(changed), changed)


def test_missing_record_cannot_be_substituted():
    evidence = records()
    with pytest.raises(ValueError, match="RECORD_MISSING"):
        require(proof(evidence), evidence[:1])


def test_historical_publication_requires_separate_actual_proof():
    raw = proof(records()).model_dump()
    raw.update({"publication_times": "ORIGINAL_PUBLICATION_VERIFIED"})
    with pytest.raises(ValueError, match="PUBLICATION_INVALID"):
        OfficialDisclosureProof.model_validate(raw)
    assert OfficialDisclosureProof.model_validate(
        raw | {"publication_evidence_hash": DIGESTS["publication"]}
    ).publication_evidence_hash == DIGESTS["publication"]


def test_as_retrieved_cannot_backdate_availability():
    raw = proof(records()).model_dump() | {"publication_time": NOW - timedelta(days=30)}
    with pytest.raises(ValueError, match="PUBLICATION_INVALID"):
        OfficialDisclosureProof.model_validate(raw)


def test_stale_evidence_cannot_be_revived_by_current_identity():
    evidence = records()
    old = tuple(EvidenceRecord.model_validate(item.model_dump() | {
        "retrieval_time": NOW - timedelta(days=1),
        "publication_time": NOW - timedelta(days=1),
        "fresh_until": NOW - timedelta(hours=1),
        "hash": "",
    }) for item in evidence)
    selected = OfficialDisclosureProof.model_validate(proof(old).model_dump() | {
        "retrieval_time": NOW - timedelta(days=1),
        "publication_time": NOW - timedelta(days=1),
        "fresh_until": NOW - timedelta(hours=1),
    })
    with pytest.raises(ValueError, match="FRESHNESS_INVALID"):
        require(selected, old)


def test_provider_technical_and_personal_rights_cannot_be_used_for_commercial_release():
    admitted = source_qualification().model_copy(update={
        "production_qualified": False,
        "qualified_by": None,
        "redistribution": "PROHIBITED",
        "personal_use": PersonalUseAudit(
            provider=PROVIDER, datasets=("filing", "financial"),
            endpoints=(URL,), attribution="Synthetic issuer", source_references=(URL,),
        ),
    })
    require(qualifications={PROVIDER: admitted}, usage_mode=UsageMode.PERSONAL_RESEARCH)
    with pytest.raises(ValueError, match="PROVIDER_UNQUALIFIED"):
        require(qualifications={PROVIDER: admitted})
    raw = official_manifest().model_dump()
    raw["provider_qualifications"] = (*raw["provider_qualifications"][:-1], admitted)
    with pytest.raises(ValueError, match="LIVE_MANIFEST_PERSONAL_USE_FORBIDDEN"):
        live.LiveManifest.model_validate(raw)


def test_manifest_policy_cannot_change_at_runtime(monkeypatch):
    monkeypatch.setenv("MONEY_ISSUER_SOURCE_POLICY", "companies_house")
    monkeypatch.delenv("MONEY_QLIB_ENABLED", raising=False)
    with pytest.raises(ValueError, match="SOURCE_POLICY_DIFFERS_FROM_PINNED_MANIFEST"):
        live.build_live_runtime(None, official_manifest())


def test_official_snapshot_uses_qualified_records_without_ch_requests(monkeypatch):
    _, _, calls = prepare_snapshot_builder(monkeypatch)
    manifest = official_manifest()
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Official-disclosure route must not call Companies House")

    monkeypatch.setattr(live, "CompaniesHouseProvider", forbidden)
    monkeypatch.setattr(live, "CompaniesHouseFilingDocuments", forbidden)
    snapshot = live.LiveSnapshotBuilder(manifest, None, qlib_enabled=False)(
        manifest.instruments[0].metadata
    )
    assert not any("companies-house" in call for call in calls)
    assert snapshot.issuer_source_policy == "official_disclosures"
    assert not snapshot.qlib_enabled
    assert snapshot.required_first_pass_firms == {"tradingagents", "ai_hedge_fund"}
    assert any(item.provider == PROVIDER and item.payload.kind == "financial"
               for item in snapshot.evidence)
    provenance = next(
        item for item in snapshot.evidence
        if item.source == "Official issuer document and conversion provenance"
    )
    assert DIGESTS["document"] in provenance.payload.excerpt
    assert DIGESTS["conversion"] in provenance.payload.excerpt
    assert BYTES["document"].decode() not in snapshot.model_dump_json()
    assert content_hash(snapshot.model_dump(mode="json", exclude={"hash"})) == snapshot.hash


@pytest.mark.parametrize("missing", [None, "document", "identity", "conversion", "currency"])
def test_manifest_checks_actual_official_document_and_conversion_bytes(tmp_path, missing):
    raw = official_manifest().model_dump(mode="json")
    artifact_bytes = BYTES | {"common": b"synthetic reviewed qualification evidence"}
    refs = {}
    for label, content in artifact_bytes.items():
        target = tmp_path / f"{label}.txt"
        target.write_bytes(content)
        refs[label] = (hashlib.sha256(content).hexdigest(), target.name)
    common = refs["common"][0]
    for field in ("eligibility_proof_hash", "ethical_proof_hash", "corporate_action_coverage_hash"):
        raw["instruments"][0][field] = common
    raw["native_egress_verification_hash"] = common
    for field in ("historical_eligibility_hash", "survivorship_audit_hash", "corporate_action_audit_hash"):
        raw["lean_qualification"][field] = common
    for provider in raw["provider_qualifications"]:
        provider["qualification_report_hash"] = common
    raw["qualification_artifacts"] = [ref for label, ref in refs.items() if label != missing]
    manifest = live.LiveManifest.model_validate(raw)
    content = manifest.model_dump_json().encode()
    path = tmp_path / "manifest.json"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    if missing:
        with pytest.raises(ValueError, match="QUALIFICATION_ARTIFACT_MISSING"):
            live.load_manifest(path, digest)
    else:
        assert live.load_manifest(path, digest) == manifest
