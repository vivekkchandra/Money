"""Synthetic runner tests only: none of these files are production evidence."""

import hashlib
from datetime import timedelta

import pytest
from test_instrument_catalogue import NOW, fixture_entry
from test_provider_probes import Fetcher, sample

from money.data.provider_probes import probe_companies_house, probe_eodhd
from money.qualification import providers
from money.qualification.core import QualificationContext


def context(tmp_path, **updates):
    return QualificationContext(
        root=tmp_path / "bundle",
        repo=tmp_path,
        environ={
            "TRADING212_API_KEY": "fixture-broker-credential",
            "TRADING212_API_SECRET": "fixture-broker-secret",
            "EODHD_API_KEY": "fixture-market-credential",
            "COMPANIES_HOUSE_API_KEY": "fixture-company-credential",
        },
        now=updates.get("now", NOW),
    )


def broker_row(**updates):
    return {
        "ticker": "FIXTUREl_EQ",
        "type": "STOCK",
        "isin": "GB00BH4HKS39",
        "currencyCode": "GBX",
        "name": "Synthetic test instrument",
        **updates,
    }


def mock_broker(monkeypatch, rows=None):
    class Broker:
        calls = 0

        def __init__(self, key, secret):
            assert key == "fixture-broker-credential"
            assert secret == "fixture-broker-secret"

        def instruments(self):
            Broker.calls += 1
            return rows if rows is not None else (broker_row(),)

    monkeypatch.setattr(providers, "Trading212MetadataProvider", Broker)
    return Broker


def review_path():
    return "inputs/instruments/" + providers._candidate_key(broker_row()) + ".json"


def stamp():
    return {
        "status": "REVIEWED",
        "prepared_by": "test-preparer",
        "reviewed_by": "test-reviewer",
        "reviewed_at": NOW.isoformat(),
        "valid_until": (NOW + timedelta(hours=2)).isoformat(),
    }


def reviewed_instrument(ctx):
    entry = fixture_entry()
    value = {
        "review": stamp(),
        "discovered": broker_row(),
        "metadata": entry.metadata.model_dump(mode="json"),
        "identifiers": sample().model_dump(mode="json"),
        "eligibility_evidence_files": ["inputs/test-isa-evidence.txt"],
        "ethical_evidence_files": ["inputs/test-ethics-evidence.txt"],
    }
    ctx.write_json(review_path(), value)
    ctx.write_bytes("inputs/test-isa-evidence.txt", b"Synthetic reviewed ISA test evidence")
    ctx.write_bytes("inputs/test-ethics-evidence.txt", b"Synthetic reviewed ethical test evidence")
    return value


def mock_probes(monkeypatch):
    calls = {"eodhd": 0, "companies-house": 0}

    def market(key, samples, now, artifacts):
        calls["eodhd"] += 1
        return probe_eodhd(key, samples, now, artifacts, fetcher=Fetcher())

    def companies(key, samples, now, artifacts):
        calls["companies-house"] += 1
        return probe_companies_house(key, samples, now, artifacts, fetcher=Fetcher())

    monkeypatch.setattr(providers, "probe_eodhd", market)
    monkeypatch.setattr(providers, "probe_companies_house", companies)
    return calls


def rights(ctx, name):
    evidence = "inputs/" + name + "-test-rights.txt"
    ctx.write_bytes(evidence, b"Synthetic review bytes, not production licence evidence")
    ctx.write_json(
        f"inputs/provider-rights/{name}.json",
        {
            "status": "REVIEWED",
            "rights_evidence_file": evidence,
            "review": {
                "provider": name,
                "reviewed_by": "test-licensing-reviewer",
                "usage_purpose": "test only",
                "storage_policy": "test only",
                "redistribution": "PROHIBITED",
                "attribution": "Test only",
                "source_documentation": "https://example.test/licence",
                "reviewed_at": NOW.isoformat(),
                "valid_until": (NOW + timedelta(hours=2)).isoformat(),
            },
        },
    )


def codes(ctx):
    return {item["code"] for item in ctx.blockers}


def test_discovery_keeps_only_individual_gbp_gbx_candidates_and_no_inference(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    mock_broker(
        monkeypatch,
        (
            broker_row(),
            broker_row(ticker="GBPTESTl_EQ", currencyCode="GBP"),
            broker_row(ticker="ETFTESTl_EQ", type="ETF"),
            broker_row(ticker="USTEST_EQ", currencyCode="USD"),
        ),
    )
    result = providers.run_provider_stages(ctx)
    assert result["candidate_counts"] == {"GBP": 1, "GBX": 1}
    assert result["eligible_counts"] == {"GBP": 0, "GBX": 0}
    assert result["eligibility_reviews"] == []
    value = ctx.read_json(review_path())
    assert value["discovered"]["isin"] == "GB00BH4HKS39"
    assert value["identifiers"]["isin"] is None
    assert value["identifiers"]["companies_house_number"] is None
    assert value["identifiers"]["provider_symbols"] == []
    assert value["metadata"]["isa_available"] is None
    assert value["metadata"]["currently_available"] is None
    assert value["metadata"]["business_activities"] == []
    assert "LIVE_METADATA_AND_FRESH_REVIEW_JOIN_REQUIRED" in codes(ctx)
    assert "CH_FINANCIAL_DOCUMENT_QUALIFICATION_REQUIRED" in codes(ctx)


def test_templates_never_overwrite_operator_changes(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    mock_broker(monkeypatch)
    providers.run_provider_stages(ctx)
    edited = ctx.read_json(review_path())
    edited["review"]["prepared_by"] = "Operator work in progress"
    ctx.write_json(review_path(), edited)
    providers.run_provider_stages(ctx)
    assert ctx.read_json(review_path()) == edited


def test_review_proof_hashes_match_actual_bytes_and_rights_resume_reuses_probes(
    tmp_path, monkeypatch
):
    ctx = context(tmp_path)
    broker = mock_broker(monkeypatch)
    calls = mock_probes(monkeypatch)
    reviewed_instrument(ctx)
    first = providers.run_provider_stages(ctx)
    assert first["eligible_counts"] == {"GBP": 0, "GBX": 1}
    assert first["provider_qualifications"] == []
    assert calls == {"eodhd": 1, "companies-house": 1}
    eligible = first["eligibility_reviews"][0]
    refs = dict(first["artifact_refs"])
    for name in ("eligibility_proof_hash", "ethical_proof_hash"):
        digest = eligible[name]
        assert hashlib.sha256(ctx.read_bytes(refs[digest])).hexdigest() == digest
    rights(ctx, "eodhd")
    rights(ctx, "companies-house")
    resumed = context(tmp_path, now=NOW + timedelta(minutes=1))
    result = providers.run_provider_stages(resumed)
    assert calls == {"eodhd": 1, "companies-house": 1}
    assert broker.calls == 2  # Current broker membership is always refreshed.
    assert (
        result["eligibility_reviews"][0]["eligibility_proof_hash"]
        == eligible["eligibility_proof_hash"]
    )
    qualified = {item["provider"]: item for item in result["provider_qualifications"]}
    assert set(qualified["eodhd"]["datasets"]) == {"ohlcv", "corporate_action", "news"}
    assert qualified["companies-house"]["datasets"] == ["filing"]
    assert all(item["publication_times"] == "AS_RETRIEVED" for item in qualified.values())
    assert "PROVIDER_RIGHTS_REVIEW_REQUIRED" not in codes(resumed)
    assert "CH_FINANCIAL_DOCUMENT_QUALIFICATION_REQUIRED" in codes(resumed)


@pytest.mark.parametrize(
    "change",
    ["same_reviewer", "expired", "wrong_isin", "false_isa", "unknown_ethics", "missing_bytes"],
)
def test_unverified_or_conflicting_review_never_enters_universe(tmp_path, monkeypatch, change):
    ctx = context(tmp_path)
    mock_broker(monkeypatch)
    calls = mock_probes(monkeypatch)
    value = reviewed_instrument(ctx)
    if change == "same_reviewer":
        value["review"]["reviewed_by"] = value["review"]["prepared_by"]
    elif change == "expired":
        value["identifiers"]["valid_until"] = NOW.isoformat()
    elif change == "wrong_isin":
        value["discovered"]["isin"] = "GB00WRONG000"
    elif change == "false_isa":
        value["metadata"]["isa_available"] = False
    elif change == "unknown_ethics":
        value["metadata"]["business_activities"] = ["unknown"]
    else:
        value["ethical_evidence_files"] = ["inputs/nonexistent.txt"]
    ctx.write_json(review_path(), value)
    result = providers.run_provider_stages(ctx)
    assert result["eligibility_reviews"] == []
    assert "INSTRUMENT_REVIEW_REJECTED" in codes(ctx)
    assert calls == {"eodhd": 0, "companies-house": 0}


def test_removed_broker_stock_cannot_resume_from_old_discovery(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    mock_broker(monkeypatch)
    mock_probes(monkeypatch)
    reviewed_instrument(ctx)
    assert providers.run_provider_stages(ctx)["eligibility_reviews"]
    mock_broker(monkeypatch, ())
    assert providers.run_provider_stages(context(tmp_path))["eligibility_reviews"] == []


def test_tampered_probe_cache_triggers_actual_retrieval(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    mock_broker(monkeypatch)
    calls = mock_probes(monkeypatch)
    reviewed_instrument(ctx)
    providers.run_provider_stages(ctx)
    cache = ctx.read_json("state/providers/eodhd.json")
    ctx.write_bytes("artifacts/" + cache["path"], b"{}")
    # The content-addressed tampered report prevents qualification even if a new
    # observation would have the same bytes; it is never silently accepted.
    result = providers.run_provider_stages(context(tmp_path))
    assert calls["eodhd"] == 2
    assert not result["provider_qualifications"]


def test_provider_exception_secrets_do_not_escape_to_reports(tmp_path, monkeypatch, capsys):
    ctx = context(tmp_path)

    class BrokenBroker:
        def __init__(self, *args):
            pass

        def instruments(self):
            raise ValueError("fixture-broker-secret")

    monkeypatch.setattr(providers, "Trading212MetadataProvider", BrokenBroker)
    providers.run_provider_stages(ctx)
    assert "fixture-broker-secret" not in capsys.readouterr().out
    assert "fixture-broker-secret" not in str(ctx.blockers)
    for path in ctx.root.rglob("*"):
        if path.is_file():
            assert b"fixture-broker-secret" not in path.read_bytes()


def test_document_probe_does_not_relax_public_production_fetch_gate():
    from test_filing_documents import (
        FixtureTransport,
        fetch,
        identifiers,
        proof,
        qualification,
        service,
    )

    scope = qualification().model_copy(update={"production_qualified": False})
    transport = FixtureTransport()
    adapter = service(transport, admission=scope)
    with pytest.raises(ValueError, match="PROVIDER_UNQUALIFIED"):
        fetch(adapter)
    assert transport.calls == []
    discovered = adapter.inspect_document(identifiers(), "filing-123")
    assert discovered["status"] == "UNQUALIFIED_REPRESENTATION_OBSERVATION"
    assert discovered["document_content_hash"] == proof().document_content_hash
    assert discovered["accounting_currency"] is None
    assert "raw_document" not in discovered
    bundle = adapter.probe_document(identifiers(), "filing-123", proof())
    assert bundle.conversion_source_attestation == "INJECTED_UNATTESTED"
    assert not adapter.qualification.production_qualified
    with pytest.raises(ValueError, match="PROVIDER_UNQUALIFIED"):
        fetch(adapter)


def test_document_inspection_never_guesses_unreviewed_storage_host():
    from test_filing_documents import FixtureTransport, identifiers, qualification, service

    transport = FixtureTransport()
    transport.storage_host = "unreviewed.example.test"
    adapter = service(
        transport, admission=qualification().model_copy(update={"production_qualified": False})
    )
    with pytest.raises(ValueError, match="STORAGE_HOST_UNQUALIFIED"):
        adapter.inspect_document(identifiers(), "filing-123")


def test_document_probe_rejects_using_an_already_qualified_scope():
    from test_filing_documents import FixtureTransport, identifiers, service

    with pytest.raises(ValueError, match="FILING_ADMISSION_SCOPE_INVALID"):
        service(FixtureTransport()).inspect_document(identifiers(), "filing-123")


def test_document_host_discovery_does_not_follow_unreviewed_redirect():
    from test_filing_documents import FixtureTransport, identifiers, qualification, service

    class DiscoveryTransport(FixtureTransport):
        def inspect_storage_host(self, url, mime):
            return "observed-storage.example.test"

        def content(self, url, mime):
            raise AssertionError("Unreviewed storage must not be contacted")

    adapter = service(
        DiscoveryTransport(),
        admission=qualification().model_copy(update={"production_qualified": False}),
    )
    observed = adapter.inspect_document(identifiers(), "filing-123")
    assert observed["status"] == "UNREVIEWED_STORAGE_HOST"
    assert observed["storage_host"] == "observed-storage.example.test"
    assert observed["document_content_hash"] is None


def test_financial_observation_requires_pinned_parser_not_injected_test_parser(tmp_path):
    from test_filing_documents import (
        NOW as DOCUMENT_NOW,
    )
    from test_filing_documents import (
        FixtureTransport,
        identifiers,
        proof,
        qualification,
        service,
    )

    ctx = context(tmp_path, now=DOCUMENT_NOW)
    adapter = service(
        FixtureTransport(),
        admission=qualification().model_copy(update={"production_qualified": False}),
    )
    with pytest.raises(ValueError, match="FILING_NATIVE_PARSER_ATTESTATION_REQUIRED"):
        providers._document_observation(ctx, adapter, identifiers(), proof(), set())


def test_financial_documents_qualify_only_after_review_and_reuse_actual_observation(
    tmp_path, monkeypatch
):
    from test_filing_documents import NOW as DOCUMENT_NOW
    from test_filing_documents import FixtureTransport, identifiers, proof, qualification, service

    ctx = context(tmp_path, now=DOCUMENT_NOW)
    base = qualification().model_copy(update={"datasets": ("filing",)})

    # Deliberately synthetic attestation stub: test artifacts stay in tmp_path.
    class SyntheticPinnedAdapter:
        calls = 0

        def __init__(self, key, scope, *, storage_hosts, clock):
            assert not scope.production_qualified
            self.storage_hosts = {}
            self.adapter = service(FixtureTransport(), admission=scope)

        def probe_document(self, identity, filing_id, currency_proof):
            SyntheticPinnedAdapter.calls += 1
            observed = self.adapter.probe_document(identity, filing_id, currency_proof)
            return observed.model_copy(
                update={
                    "conversion_source_attestation": "PINNED_SOURCE_VERIFIED",
                    "conversion_source_hash": providers.SINGLE_MODULE_SOURCE_DIGESTS[
                        "stream_read_xbrl"
                    ],
                }
            )

    monkeypatch.setattr(providers, "CompaniesHouseFilingDocuments", SyntheticPinnedAdapter)
    reviewed = stamp() | {
        "reviewed_at": DOCUMENT_NOW.isoformat(),
        "valid_until": (DOCUMENT_NOW + timedelta(hours=1)).isoformat(),
    }
    ctx.write_bytes("inputs/financial-units.txt", b"Synthetic original GBP-units review")
    ctx.write_json(
        "inputs/financial-documents.json",
        {
            "review": reviewed,
            "rights_cover_financial_documents": False,
            "storage_hosts": [],
            "documents": [
                {
                    "ticker": "VOD.L",
                    "filing_id": "filing-123",
                    "accounting_currency": "GBP",
                    "document_content_hash": proof().document_content_hash,
                    "currency_evidence_file": "inputs/financial-units.txt",
                }
            ],
        },
    )

    def stage_result():
        return {
            "provider_qualifications": [base.model_dump(mode="json")],
            "eligibility_reviews": [{"identifiers": identifiers().model_dump(mode="json")}],
            "instruments": [],
        }

    result = stage_result()
    providers._financial_documents(ctx, result, set(), clock=lambda: DOCUMENT_NOW)
    assert result["provider_qualifications"][0]["datasets"] == ["filing"]
    assert "CH_FINANCIAL_DOCUMENT_QUALIFICATION_FAILED" in codes(ctx)
    assert SyntheticPinnedAdapter.calls == 1
    review = ctx.read_json("inputs/financial-documents.json")
    review["rights_cover_financial_documents"] = True
    ctx.write_json("inputs/financial-documents.json", review)
    result = stage_result()
    refs = set()
    providers._financial_documents(
        context(tmp_path, now=DOCUMENT_NOW), result, refs, clock=lambda: DOCUMENT_NOW
    )
    assert set(result["provider_qualifications"][0]["datasets"]) == {"filing", "financial"}
    assert SyntheticPinnedAdapter.calls == 1
    first_hash = result["provider_qualifications"][0]["qualification_report_hash"]
    result = stage_result()
    providers._financial_documents(
        context(tmp_path, now=DOCUMENT_NOW + timedelta(minutes=1)),
        result,
        refs,
        clock=lambda: DOCUMENT_NOW + timedelta(minutes=1),
    )
    assert result["provider_qualifications"][0]["qualification_report_hash"] == first_hash
    assert SyntheticPinnedAdapter.calls == 1
    assert any(digest == first_hash for digest, path in refs)


def supplemental_source_fixture(ctx, monkeypatch):
    from test_filing_documents import NOW as DOCUMENT_NOW
    from test_filing_documents import reviewed_manifest

    from money.schemas.contracts import EvidenceRecord

    monkeypatch.setattr(providers, "utc_now", lambda: DOCUMENT_NOW)
    instrument = reviewed_manifest().instruments[0]
    spread = EvidenceRecord.model_validate(
        instrument.spread_evidence.model_dump() | {"provider": "reviewed-market-source", "hash": ""}
    )
    instrument = instrument.model_copy(update={"spread_evidence": spread})
    result = {"instruments": [instrument.model_dump(mode="json")], "provider_qualifications": []}
    ctx.write_bytes("inputs/source-rights.txt", b"Synthetic source licence review evidence")
    ctx.write_bytes("inputs/source-quotation.txt", b"Synthetic observed bid/ask source evidence")
    ctx.write_json(
        "inputs/supplemental-sources.json", {"source_review_files": ["inputs/market-source.json"]}
    )
    admission = {
        "review": stamp()
        | {
            "reviewed_at": DOCUMENT_NOW.isoformat(),
            "valid_until": (DOCUMENT_NOW + timedelta(hours=1)).isoformat(),
        },
        "provider": "reviewed-market-source",
        "usage_purpose": "Test only",
        "storage_policy": "Test only",
        "redistribution": "PROHIBITED",
        "attribution": "Test source",
        "source_documentation": "https://example.test/source",
        "rights_evidence_file": "inputs/source-rights.txt",
        "publication_times": "AS_RETRIEVED",
        "maximum_age_seconds": 3600,
        "observations": [
            {
                "ticker": instrument.metadata.ticker,
                "evidence": spread.model_dump(mode="json"),
                "source_evidence_file": "inputs/source-quotation.txt",
                "publication_evidence_file": None,
            }
        ],
    }
    ctx.write_json("inputs/market-source.json", admission)
    return result, admission


def test_supplemental_source_admission_covers_only_exact_observed_dataset_bytes(
    tmp_path, monkeypatch
):
    ctx = context(tmp_path)
    result, _ = supplemental_source_fixture(ctx, monkeypatch)
    refs = set()
    providers._additional_sources(ctx, result, refs)
    qualification = result["additional_provider_qualifications"][0]
    assert qualification["datasets"] == ["financial"]
    assert qualification["currencies"] == ["GBX"]
    assert qualification["publication_times"] == "AS_RETRIEVED"
    assert any(digest == qualification["qualification_report_hash"] for digest, _ in refs)
    providers._filter_source_coverage(ctx, result)
    assert len(result["instruments"]) == 1
    assert not ctx.blockers


@pytest.mark.parametrize(
    "change",
    [
        "missing_rights",
        "missing_source",
        "old_publication",
        "historical_without_proof",
        "unobserved_dataset",
        "unmatched_record",
    ],
)
def test_supplemental_source_cannot_invent_access_rights_or_pit(tmp_path, monkeypatch, change):
    from money.schemas.contracts import EvidenceRecord

    ctx = context(tmp_path)
    result, admission = supplemental_source_fixture(ctx, monkeypatch)
    if change == "missing_rights":
        admission["rights_evidence_file"] = "inputs/missing.txt"
    elif change == "missing_source":
        admission["observations"][0]["source_evidence_file"] = "inputs/missing.txt"
    elif change == "historical_without_proof":
        admission["publication_times"] = "ORIGINAL_PUBLICATION_VERIFIED"
    elif change == "unobserved_dataset":
        admission["datasets"] = ["financial", "ohlcv"]
    else:
        record = admission["observations"][0]["evidence"]
        changed = {"hash": "", "source_id": "unmatched"}
        if change == "old_publication":
            from test_filing_documents import NOW as DOCUMENT_NOW

            changed = {"hash": "", "publication_time": DOCUMENT_NOW - timedelta(days=1)}
        record = EvidenceRecord.model_validate(record | changed).model_dump(mode="json")
        admission["observations"][0]["evidence"] = record
        if change == "old_publication":
            result["instruments"][0]["spread_evidence"] = record
    ctx.write_json("inputs/market-source.json", admission)
    providers._additional_sources(ctx, result, set())
    assert result["additional_provider_qualifications"] == []
    providers._filter_source_coverage(ctx, result)
    assert result["instruments"] == []


def test_source_qualification_does_not_admit_different_unreviewed_record(tmp_path, monkeypatch):
    from money.schemas.contracts import EvidenceRecord

    ctx = context(tmp_path)
    result, _ = supplemental_source_fixture(ctx, monkeypatch)
    providers._additional_sources(ctx, result, set())
    assert result["additional_provider_qualifications"]
    changed = EvidenceRecord.model_validate(
        result["instruments"][0]["spread_evidence"] | {"source_id": "unreviewed", "hash": ""}
    )
    result["instruments"][0]["spread_evidence"] = changed.model_dump(mode="json")
    providers._filter_source_coverage(ctx, result)
    assert result["instruments"] == []


def test_document_probe_uses_advancing_clock_and_rejects_expiry_during_io():
    from test_filing_documents import NOW as DOCUMENT_NOW
    from test_filing_documents import (
        FixtureTransport,
        identifiers,
        parse_synthetic,
        proof,
        qualification,
    )

    from money.data.uk.filing_documents import CompaniesHouseFilingDocuments

    observed_times = iter((DOCUMENT_NOW, DOCUMENT_NOW + timedelta(hours=2)))
    adapter = CompaniesHouseFilingDocuments(
        "synthetic-key",
        qualification().model_copy(update={"production_qualified": False}),
        transport=FixtureTransport(),
        parser=parse_synthetic,
        clock=lambda: next(observed_times),
    )
    with pytest.raises(ValueError, match="IDENTIFIER_MAPPING_STALE"):
        adapter.probe_document(identifiers(), "filing-123", proof())


def test_bad_optional_source_candidate_does_not_remove_valid_candidate(tmp_path, monkeypatch):
    from money.schemas.contracts import EvidenceRecord

    ctx = context(tmp_path)
    result, _ = supplemental_source_fixture(ctx, monkeypatch)
    providers._additional_sources(ctx, result, set())
    invalid = dict(result["instruments"][0])
    invalid["metadata"] = invalid["metadata"] | {"ticker": "OPTIONAL.L"}
    invalid["identifiers"] = invalid["identifiers"] | {
        "ticker": "OPTIONAL.L",
        "trading212_id": "OPTIONAL_EQ",
    }
    invalid["spread_evidence"] = EvidenceRecord.model_validate(
        invalid["spread_evidence"] | {"source_id": "not-reviewed", "hash": ""}
    ).model_dump(mode="json")
    result["instruments"].append(invalid)
    providers._filter_source_coverage(ctx, result)
    assert len(result["instruments"]) == 1
    assert result["candidate_exclusions"][0]["ticker"] == "OPTIONAL.L"
    assert not ctx.blockers


@pytest.mark.parametrize("change", ["unattested", "lineage", "unreviewed_host"])
def test_document_cache_reconstructs_proof_bytes_instead_of_trusting_mutable_value(
    tmp_path, change
):
    from test_filing_documents import NOW as DOCUMENT_NOW
    from test_filing_documents import FixtureTransport, identifiers, proof, qualification, service

    from money.schemas.contracts import EvidenceRecord

    ctx = context(tmp_path, now=DOCUMENT_NOW)

    class SyntheticAdapter:
        storage_hosts = {}
        calls = 0

        def probe_document(self, identity, filing_id, currency_proof):
            self.calls += 1
            if self.calls > 1:
                raise ValueError("TEST_NATIVE_REPROBE_REQUIRED")
            adapter = service(
                FixtureTransport(),
                admission=qualification().model_copy(update={"production_qualified": False}),
            )
            observed = adapter.probe_document(identity, filing_id, currency_proof)
            return observed.model_copy(
                update={
                    "conversion_source_attestation": "PINNED_SOURCE_VERIFIED",
                    "conversion_source_hash": providers.SINGLE_MODULE_SOURCE_DIGESTS[
                        "stream_read_xbrl"
                    ],
                }
            )

    adapter = SyntheticAdapter()
    _, path = providers._document_observation(
        ctx, adapter, identifiers(), proof(), set(), clock=lambda: DOCUMENT_NOW
    )
    changed = ctx.read_json(path)
    if change == "unattested":
        changed["conversion_source_attestation"] = "INJECTED_UNATTESTED"
        changed["conversion_source_hash"] = None
    elif change == "lineage":
        changed["evidence"][0] = EvidenceRecord.model_validate(
            changed["evidence"][0] | {"source_id": "other-company-record", "hash": ""}
        ).model_dump(mode="json")
    else:
        changed["storage_host"] = "unreviewed.example.test"
    changed_ref = ctx.artifact(changed)
    checkpoint_path = (
        next((ctx.root / "state").glob("financial-document-*.json"))
        .relative_to(ctx.root)
        .as_posix()
    )
    checkpoint = ctx.read_json(checkpoint_path)
    # The checkpoint still verifies its original artifact. Its editable value
    # points elsewhere and claims qualification; neither assertion is authority.
    checkpoint["value"] = {"bundle_ref": changed_ref, "qualified": True}
    ctx.write_json(checkpoint_path, checkpoint)
    with pytest.raises(ValueError, match="TEST_NATIVE_REPROBE_REQUIRED"):
        providers._document_observation(
            ctx, adapter, identifiers(), proof(), set(), clock=lambda: DOCUMENT_NOW
        )
    assert adapter.calls == 2
