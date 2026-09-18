"""Synthetic transports/parsers exercise contracts, never claim live qualification."""

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from zipfile import ZIP_STORED, ZipFile

import pytest

from money.backtest.lean import LeanStudyParameters
from money.data.identifiers import InstrumentIdentifiers
from money.data.qualification import ProviderQualification
from money.data.security import FetchResult, ProviderFailure, SourceSecurityError
from money.data.uk import filing_documents as documents
from money.data.uk.filing_documents import (
    API_HOST,
    DOCUMENT_HOST,
    CompaniesHouseDocumentTransport,
    CompaniesHouseFilingDocuments,
    FinancialCurrencyProof,
    ReviewedStorageHost,
)
from money.schemas.contracts import (
    EvidenceRecord,
    FinancialFact,
    InstrumentMetadata,
    PriceBar,
    ResearchSnapshot,
    content_hash,
)
from money.signals.generation import SignalPolicy

NOW = datetime(2026, 9, 16, 10, tzinfo=UTC)
DOCUMENT = b'<html xmlns="http://www.w3.org/1999/xhtml"><body>synthetic</body></html>'
MIME = "application/xhtml+xml"
LINK = f"https://{DOCUMENT_HOST}/document/doc-123"
STORAGE = "reviewed-storage.example.com"
REVIEW = ReviewedStorageHost(host=STORAGE, review_evidence_hash="c" * 64)


def identifiers():
    return InstrumentIdentifiers(
        ticker="VOD.L",
        company_name="Vodafone",
        trading212_id="VODl_EQ",
        exchange_ticker="VOD",
        exchange="XLON",
        isin="GB00BH4HKS39",
        quote_currency="GBX",
        companies_house_number="01833679",
        provider_symbols=(("eodhd", "VOD.LSE"),),
        verified_at=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(hours=1),
        source="synthetic identifier mapping",
    )


def qualification():
    return ProviderQualification(
        provider="companies-house",
        datasets=("filing", "financial"),
        earliest_observation=NOW - timedelta(days=365),
        publication_times="AS_RETRIEVED",
        maximum_age_seconds=3600,
        production_qualified=True,
        qualified_by="synthetic test review only",
        qualification_report_hash="a" * 64,
        verified_at=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=1),
        attribution="Companies House",
        source_documentation=f"https://{DOCUMENT_HOST}",
    )


def proof(content=DOCUMENT):
    return FinancialCurrencyProof(
        company_number="01833679",
        filing_id="filing-123",
        currency="GBP",
        evidence_hash="b" * 64,
        document_content_hash=hashlib.sha256(content).hexdigest(),
    )


def parse_synthetic(archive, **kwargs):
    # The fixture deliberately does not pretend to parse XBRL.
    assert ZipFile(BytesIO(archive)).namelist()
    return (
        EvidenceRecord(
            snapshot_id=kwargs["snapshot_id"],
            source="Companies House filing",
            provider="stream-read-xbrl",
            source_id=kwargs["source_id"],
            canonical_source_id="companies-house:" + kwargs["source_id"],
            observation_time=NOW - timedelta(days=200),
            publication_time=kwargs["publication_time"],
            retrieval_time=kwargs["retrieved_at"],
            fresh_until=NOW + timedelta(days=1),
            pit_safe=True,
            payload=FinancialFact(
                metric="cash", value="123", unit="GBP", period_end=NOW - timedelta(days=200)
            ),
        ),
    )


class FixtureTransport:
    def __init__(self, *, content=DOCUMENT, mime=MIME):
        self.calls = []
        self.filing = {
            "transaction_id": "filing-123",
            "category": "accounts",
            "date": "2026-01-10",
            "links": {"document_metadata": LINK},
        }
        self.document = {
            "id": "doc-123",
            "company_number": "01833679",
            "created_at": "2026-01-10T01:00:00Z",
            "updated_at": "2026-02-10T01:00:00Z",
            "resources": {mime: {"content_length": len(content)}},
        }
        self.result = FetchResult(content, mime)
        self.storage_host = DOCUMENT_HOST
        self.raw_filing = None
        self.raw_document = None

    def metadata(self, url):
        self.calls.append(("metadata", url))
        if "/filing-history/" in url:
            return self.raw_filing or json.dumps(self.filing).encode()
        return self.raw_document or json.dumps(self.document).encode()

    def content(self, url, mime):
        self.calls.append(("content", url, mime))
        return self.result, self.storage_host


def service(transport, *, parser=parse_synthetic, admission=None, storage_hosts=()):
    return CompaniesHouseFilingDocuments(
        "synthetic-key",
        admission or qualification(),
        transport=transport,
        parser=parser,
        clock=lambda: NOW,
        storage_hosts=storage_hosts,
    )


def fetch(adapter, *, currency_proof=None):
    return adapter.fetch(
        identifiers(),
        "filing-123",
        "snapshot-123",
        currency_proof or proof(adapter.transport.result.content),
    )


def test_documents_bind_company_filing_and_never_backdate_availability_or_expose_raw():
    transport = FixtureTransport()
    bundle = fetch(service(transport))
    assert transport.calls == [
        ("metadata", f"https://{API_HOST}/company/01833679/filing-history/filing-123"),
        ("metadata", LINK),
        ("content", LINK + "/content", MIME),
    ]
    assert bundle.retrieval_time == bundle.availability_time == NOW
    assert bundle.original_publication_time is None
    assert bundle.document_created_at < NOW
    assert bundle.document_updated_at < NOW
    assert bundle.evidence[0].publication_time == NOW
    assert not bundle.evidence[0].available_at(NOW - timedelta(seconds=1))
    assert bundle.point_in_time_status == "AS_RETRIEVED"
    assert bundle.content_hash == hashlib.sha256(DOCUMENT).hexdigest()
    assert (
        bundle.metadata_hash == hashlib.sha256(json.dumps(transport.document).encode()).hexdigest()
    )
    assert (
        bundle.filing_index_hash
        == hashlib.sha256(json.dumps(transport.filing).encode()).hexdigest()
    )
    assert bundle.raw_document == DOCUMENT
    assert "raw_document" not in bundle.model_dump()
    assert "synthetic</body>" not in bundle.model_dump_json()
    assert "synthetic</body>" not in repr(bundle)
    assert not bundle.raw_redistribution_allowed
    assert bundle.licensing_status == "REVIEW_REQUIRED"
    assert bundle.currency_evidence_hash == "b" * 64
    assert bundle.evidence[0].provider == "companies-house"
    assert bundle.conversion_parser == "stream-read-xbrl"
    assert bundle.conversion_expected_upstream_sha == "b95b48bbf50727648cebcba56634b17dc9e60ad3"
    assert bundle.conversion_source_attestation == "INJECTED_UNATTESTED"
    assert bundle.conversion_source_hash is None


@pytest.mark.parametrize("mime", ["application/xml", "application/xhtml+xml", "application/zip"])
def test_only_explicitly_offered_machine_representations_reach_converter(mime):
    content = DOCUMENT
    if mime == "application/zip":
        buffer = BytesIO()
        with ZipFile(buffer, "w", compression=ZIP_STORED) as archive:
            archive.writestr("accounts.xhtml", DOCUMENT)
        content = buffer.getvalue()
    transport = FixtureTransport(content=content, mime=mime)
    seen = []

    def parser(archive, **kwargs):
        seen.append(ZipFile(BytesIO(archive)).read(ZipFile(BytesIO(archive)).namelist()[0]))
        assert kwargs["verified_currency"] == "GBP"  # never infer from GBX quote
        return parse_synthetic(archive, **kwargs)

    assert fetch(service(transport, parser=parser)).mime_type == mime
    assert seen == [DOCUMENT]


def test_pdf_only_is_not_fabricated_into_financial_evidence():
    transport = FixtureTransport(content=b"%PDF", mime="application/pdf")
    with pytest.raises(ValueError, match="MACHINE_READABLE_UNAVAILABLE"):
        fetch(service(transport))
    assert len(transport.calls) == 2


@pytest.mark.parametrize(
    "link",
    [
        "http://" + DOCUMENT_HOST + "/document/doc-123",
        "https://localhost/document/doc-123",
        "file:///document/doc-123",
        "https://127.0.0.1/document/doc-123",
        "https://user:secret@" + DOCUMENT_HOST + "/document/doc-123",
        LINK + "?secret=bad",
        LINK + "/../other",
        LINK + "%2fother",
        "https://" + DOCUMENT_HOST + ".example.com/document/doc-123",
    ],
)
def test_untrusted_document_links_never_receive_credentials(link):
    transport = FixtureTransport()
    transport.filing["links"]["document_metadata"] = link
    with pytest.raises(SourceSecurityError):
        fetch(service(transport))
    assert len(transport.calls) == 1


@pytest.mark.parametrize("filing_id", ["../foo", "a/b", "x?key=1", "a" * 129, ""])
def test_strict_transaction_ids_reject_before_network(filing_id):
    transport = FixtureTransport()
    with pytest.raises(ValueError, match="IDENTIFIER_INVALID"):
        service(transport).fetch(identifiers(), filing_id, "snapshot", proof())
    assert not transport.calls


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"transaction_id": "other"}, "IDENTITY_OR_CATEGORY_MISMATCH"),
        ({"category": "mortgage"}, "IDENTITY_OR_CATEGORY_MISMATCH"),
        ({"links": []}, "DOCUMENT_LINK_MISSING"),
        ({"date": None}, "DATE_INVALID"),
        ({"date": "2026-99-99"}, "DATE_INVALID"),
        ({"date": "20260916"}, "DATE_INVALID"),
        ({"date": "2026-09-17"}, "PIT_VIOLATION"),
    ],
)
def test_malformed_or_future_filing_index_fails_closed(updates, code):
    transport = FixtureTransport()
    transport.filing.update(updates)
    with pytest.raises(ValueError, match=code):
        fetch(service(transport))


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("id", "different", "IDENTITY_MISMATCH"),
        ("company_number", "99999999", "IDENTITY_MISMATCH"),
        ("created_at", "2026-09-17T00:00:00Z", "PIT_VIOLATION"),
        ("updated_at", "2026-01-01T00:00:00", "PIT_VIOLATION"),
        ("updated_at", {}, "TIMESTAMP_INVALID"),
    ],
)
def test_metadata_identity_and_timestamp_proof(field, value, code):
    transport = FixtureTransport()
    transport.document[field] = value
    with pytest.raises(ValueError, match=code):
        fetch(service(transport))


@pytest.mark.parametrize("length", [-1, 0, True, 5_000_001, "100", None])
def test_declared_representation_size_rejected_before_download(length):
    transport = FixtureTransport()
    transport.document["resources"][MIME]["content_length"] = length
    with pytest.raises(SourceSecurityError, match="DECLARED_SIZE_INVALID"):
        fetch(service(transport))
    assert len(transport.calls) == 2


@pytest.mark.parametrize(
    "result", [FetchResult(b"short", MIME), FetchResult(DOCUMENT, "text/html")]
)
def test_download_must_match_offered_content_length_and_mime(result):
    transport = FixtureTransport()
    transport.result = result
    with pytest.raises(SourceSecurityError, match="REPRESENTATION_MISMATCH"):
        fetch(service(transport))


@pytest.mark.parametrize(
    "raw,code",
    [
        (b'{"resources":{},"resources":{}}', "DUPLICATE_JSON_KEY"),
        (b'{"x":NaN}', "NONFINITE_JSON"),
        (b"[]", "METADATA_INVALID"),
        (b"\xff", "METADATA_INVALID"),
        (b" " * 500_001, "METADATA_SIZE_LIMIT"),
    ],
)
def test_untrusted_metadata_json_is_bounded_and_strict(raw, code):
    transport = FixtureTransport()
    transport.raw_document = raw
    with pytest.raises(SourceSecurityError, match=code):
        fetch(service(transport))


@pytest.mark.parametrize(
    "content",
    [
        b'<!DOCTYPE html SYSTEM "https://evil.example"><html/>',
        b'<!ENTITY secret SYSTEM "file:///etc/passwd">',
        "<!DOCTYPE html><html/>".encode("utf-16"),
    ],
)
def test_entities_are_rejected_before_native_parser(content):
    transport = FixtureTransport(content=content)
    with pytest.raises(SourceSecurityError, match="EXTERNAL_ENTITY_DENIED"):
        fetch(service(transport, parser=lambda *args, **kwargs: pytest.fail("unsafe parse")))


@pytest.mark.parametrize("name", ["../accounts.xml", "accounts.exe"])
def test_unsafe_archive_members_rejected_before_parser(name):
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_STORED) as archive:
        archive.writestr(name, DOCUMENT)
    transport = FixtureTransport(content=buffer.getvalue(), mime="application/zip")
    with pytest.raises(SourceSecurityError):
        fetch(service(transport, parser=lambda *args, **kwargs: pytest.fail("unsafe parse")))


@pytest.mark.parametrize(
    "content",
    [
        b'<?xml version="1.0" encoding="UTF-7"?><html/>',
        b'<?xml version="1.0" encoding="windows-1252"?><html/>',
        b"<html>\xff</html>",
    ],
)
def test_unqualified_encodings_never_reach_parser(content):
    transport = FixtureTransport(content=content)
    with pytest.raises(SourceSecurityError, match="ENCODING_DENIED"):
        fetch(service(transport, parser=lambda *args, **kwargs: pytest.fail("unsafe encoding")))


def test_missing_native_parser_and_empty_conversion_are_explicit_not_demo():
    def missing(*args, **kwargs):
        raise ModuleNotFoundError("stream_read_xbrl")

    with pytest.raises(ValueError, match="XBRL_PARSER_UNAVAILABLE"):
        fetch(service(FixtureTransport(), parser=missing))
    with pytest.raises(ValueError, match="FINANCIAL_FACTS_MISSING"):
        fetch(service(FixtureTransport(), parser=lambda *args, **kwargs: ()))


def test_converter_cannot_backdate_publication_or_substitute_provenance():
    def backdated(archive, **kwargs):
        records = parse_synthetic(archive, **kwargs)
        return (records[0].model_copy(update={"publication_time": NOW - timedelta(days=1)}),)

    with pytest.raises(ValueError, match="CONVERSION_PROVENANCE_MISMATCH"):
        fetch(service(FixtureTransport(), parser=backdated))


@pytest.mark.parametrize("update", [{"company_number": "99999999"}, {"filing_id": "other"}])
def test_currency_review_must_belong_to_same_filing_before_network(update):
    transport = FixtureTransport()
    with pytest.raises(ValueError, match="CURRENCY_PROOF_MISMATCH"):
        fetch(service(transport), currency_proof=proof().model_copy(update=update))
    assert not transport.calls


def test_revised_document_cannot_inherit_earlier_currency_review():
    transport = FixtureTransport(content=DOCUMENT + b" ")
    with pytest.raises(ValueError, match="CURRENCY_PROOF_CONTENT_MISMATCH"):
        fetch(
            service(transport, parser=lambda *args, **kwargs: pytest.fail("unreviewed units")),
            currency_proof=proof(),
        )


@pytest.mark.parametrize("boundary", ["maximum_age", "qualification", "identifier", "original"])
def test_converted_fact_expiry_cannot_outlive_any_reviewed_boundary(boundary):
    expiry = NOW + timedelta(seconds=120)
    admission, mapping = qualification(), identifiers()
    if boundary == "maximum_age":
        admission = admission.model_copy(update={"maximum_age_seconds": 120})
    elif boundary == "qualification":
        admission = admission.model_copy(update={"valid_until": expiry})
    elif boundary == "identifier":
        mapping = mapping.model_copy(update={"valid_until": expiry})

    def parser(archive, **kwargs):
        record = parse_synthetic(archive, **kwargs)[0]
        if boundary == "original":
            record = EvidenceRecord.model_validate(
                record.model_dump() | {"fresh_until": expiry, "hash": ""}
            )
        return (record,)

    bundle = service(FixtureTransport(), admission=admission, parser=parser).fetch(
        mapping, "filing-123", "snapshot", proof()
    )
    assert bundle.evidence[0].fresh_until == expiry
    assert (
        EvidenceRecord.model_validate(bundle.evidence[0].model_dump()).hash
        == bundle.evidence[0].hash
    )


def test_already_stale_converter_output_is_not_refreshed_by_download():
    def parser(archive, **kwargs):
        record = parse_synthetic(archive, **kwargs)[0]
        return (
            EvidenceRecord.model_validate(record.model_dump() | {"fresh_until": NOW, "hash": ""}),
        )

    with pytest.raises(ValueError, match="CRITICAL_DATA_STALE"):
        fetch(service(FixtureTransport(), parser=parser))


def test_default_conversion_records_source_attestation_only_after_native_gate(monkeypatch):
    from types import SimpleNamespace

    from money.adapters.native_attestation import SINGLE_MODULE_SOURCE_DIGESTS
    from money.data.uk import xbrl

    calls = []

    def attest(name):
        calls.append(name)
        return SINGLE_MODULE_SOURCE_DIGESTS[name]

    def imported(name):
        assert calls == [name]
        return SimpleNamespace(
            _COLUMNS=("companies_house_registered_number", "period_end", "cash_bank_in_hand"),
            _xbrl_to_rows=lambda item: [("01833679", (NOW - timedelta(days=200)).date(), 123)],
        )

    monkeypatch.setattr(xbrl, "require_pinned_module", attest)
    monkeypatch.setattr(xbrl, "import_module", imported)
    adapter = CompaniesHouseFilingDocuments(
        "synthetic-key", qualification(), transport=FixtureTransport(), clock=lambda: NOW
    )
    bundle = fetch(adapter)
    assert calls == ["stream_read_xbrl"]
    assert bundle.conversion_source_attestation == "PINNED_SOURCE_VERIFIED"
    assert bundle.conversion_source_hash == SINGLE_MODULE_SOURCE_DIGESTS["stream_read_xbrl"]


def test_injected_converter_cannot_claim_attested_hash_without_attestation_state():
    from money.data.uk.filing_documents import FilingDocumentBundle

    bundle = fetch(service(FixtureTransport()))
    with pytest.raises(ValueError, match="CONVERSION_ATTESTATION_MISMATCH"):
        FilingDocumentBundle.model_validate(
            bundle.model_dump() | {"raw_document": DOCUMENT, "conversion_source_hash": "f" * 64}
        )


def test_unqualified_provider_and_unreviewed_storage_host_fail_closed():
    transport = FixtureTransport()
    with pytest.raises(ValueError, match="PROVIDER_UNQUALIFIED"):
        fetch(
            service(
                transport,
                admission=qualification().model_copy(update={"production_qualified": False}),
            )
        )
    assert not transport.calls
    transport.storage_host = STORAGE
    with pytest.raises(SourceSecurityError, match="STORAGE_HOST_UNQUALIFIED"):
        fetch(service(transport))
    bundle = fetch(service(transport, storage_hosts=(REVIEW,)))
    assert bundle.storage_host_review_hash == REVIEW.review_evidence_hash


class StubSocket:
    def settimeout(self, value):
        assert 0 < value <= 20

    def shutdown(self, value):
        pass


class StubConnection:
    def __init__(self, *, status=302, location=None, error=None):
        self.status, self.location, self.error = status, location, error
        self.sock = StubSocket()
        self.requests = []
        self.closed = False

    def request(self, method, target, headers):
        self.requests.append((method, target, headers))

    def getresponse(self):
        if self.error:
            raise self.error
        return self

    def getheader(self, name):
        assert name == "Location"
        return self.location

    def close(self):
        self.closed = True


def stub_connection(monkeypatch, **kwargs):
    connection = StubConnection(**kwargs)
    calls = []

    def factory(host, address, timeout):
        calls.append((host, address, timeout))
        return connection

    monkeypatch.setattr(documents, "public_addresses", lambda *args: ("93.184.216.34",))
    monkeypatch.setattr(documents, "_PinnedHTTPS", factory)
    return connection, calls


def test_signed_cross_host_download_strips_basic_auth_and_enforces_bounded_second_hop(monkeypatch):
    signed = f"https://{STORAGE}/private/document?signature=secret"
    connection, calls = stub_connection(monkeypatch, location=signed)
    content_calls = []

    def get(fetcher, url, **kwargs):
        content_calls.append((url, kwargs))
        assert fetcher.maximum_redirects == 0
        assert fetcher.maximum_bytes == 5_000_000
        assert fetcher.timeout_seconds == 20
        assert fetcher.allowed_hosts == frozenset({STORAGE})
        assert kwargs == {"headers": {"Accept": MIME}, "mime_types": (MIME,)}
        return FetchResult(DOCUMENT, MIME)

    monkeypatch.setattr(documents.SafeFetcher, "get", get)
    result, host = CompaniesHouseDocumentTransport("synthetic-key", (REVIEW,)).content(
        LINK + "/content", MIME
    )
    assert result.content == DOCUMENT and host == STORAGE
    assert content_calls[0][0] == signed
    assert calls[0][:2] == (DOCUMENT_HOST, "93.184.216.34")
    assert (
        connection.requests[0][2]["Authorization"]
        == "Basic " + base64.b64encode(b"synthetic-key:").decode()
    )
    assert connection.closed
    assert "signature" not in repr(result)  # result contract does not carry signed URL


@pytest.mark.parametrize(
    "location",
    [
        f"https://{STORAGE}/document?signature=secret",
        "https://127.0.0.1/document",
        "file:///document",
        "http://" + DOCUMENT_HOST + "/document",
        "https://user:secret@" + DOCUMENT_HOST + "/document",
        None,
    ],
)
def test_unreviewed_redirects_rejected_without_second_request(monkeypatch, location):
    connection, _ = stub_connection(monkeypatch, location=location)
    monkeypatch.setattr(
        documents.SafeFetcher, "get", lambda *args, **kwargs: pytest.fail("redirect fetched")
    )
    with pytest.raises(SourceSecurityError):
        CompaniesHouseDocumentTransport("synthetic-key").content(LINK + "/content", MIME)
    assert connection.closed


def test_direct_document_content_keeps_credentials_only_at_official_host(monkeypatch):
    stub_connection(monkeypatch, status=200)
    seen = []

    def get(fetcher, url, **kwargs):
        seen.append((url, kwargs["headers"]))
        assert fetcher.maximum_redirects == 0
        return FetchResult(DOCUMENT, MIME)

    monkeypatch.setattr(documents.SafeFetcher, "get", get)
    result, host = CompaniesHouseDocumentTransport("synthetic-key").content(LINK + "/content", MIME)
    assert result.content == DOCUMENT and host == DOCUMENT_HOST
    assert seen[0][0] == LINK + "/content" and "Authorization" in seen[0][1]


@pytest.mark.parametrize(
    "status,retryable", [(401, False), (404, False), (406, False), (429, True), (503, True)]
)
def test_provider_http_failure_classification(monkeypatch, status, retryable):
    connection, _ = stub_connection(monkeypatch, status=status)
    with pytest.raises(ProviderFailure) as failure:
        CompaniesHouseDocumentTransport("synthetic-key").content(LINK + "/content", MIME)
    assert failure.value.retryable is retryable
    assert str(failure.value) == "FILING_DOCUMENT_UNAVAILABLE"
    assert connection.closed


def test_header_timeout_closes_connection_and_is_retryable(monkeypatch):
    connection, _ = stub_connection(monkeypatch, error=TimeoutError())
    with pytest.raises(ProviderFailure, match="PROVIDER_TIMEOUT") as failure:
        CompaniesHouseDocumentTransport("synthetic-key").content(LINK + "/content", MIME)
    assert failure.value.retryable and connection.closed


def test_dns_ssrf_rejection_occurs_before_credentials_are_sent(monkeypatch):
    def reject(*args):
        raise SourceSecurityError("SOURCE_PRIVATE_ADDRESS_DENIED")

    monkeypatch.setattr(documents, "public_addresses", reject)
    monkeypatch.setattr(documents, "_PinnedHTTPS", lambda *args: pytest.fail("unsafe connection"))
    with pytest.raises(SourceSecurityError, match="PRIVATE_ADDRESS"):
        CompaniesHouseDocumentTransport("synthetic-key").content(LINK + "/content", MIME)


@pytest.mark.parametrize(
    "host",
    [
        "*.example.com",
        "127.0.0.1",
        "localhost",
        "https://example.com",
        "Example.com",
        "example.com:443",
    ],
)
def test_storage_review_requires_exact_dns_hostname(host):
    with pytest.raises(ValueError, match="STORAGE_HOST_INVALID"):
        ReviewedStorageHost(host=host, review_evidence_hash="a" * 64)


@pytest.mark.parametrize("key", ["", "a:b", "a\nb", "a b", "ü", "a" * 513])
def test_credentials_must_be_bounded_valid_basic_usernames(key):
    with pytest.raises(ValueError, match="CREDENTIAL_INVALID"):
        CompaniesHouseDocumentTransport(key)


def reviewed_manifest():
    from money.research.live import LiveManifest

    inference = {
        "provider": "synthetic",
        "model": "synthetic-model",
        "endpoint": "https://example.com",
        "credential_environment_variable": "SYNTHETIC_LLM_KEY",
        "maximum_prompt_bytes": 1000,
        "max_output_tokens": 256,
    }
    spread = EvidenceRecord(
        snapshot_id="review",
        source="synthetic spread",
        provider="eodhd",
        source_id="spread",
        canonical_source_id="spread",
        observation_time=NOW,
        publication_time=NOW,
        retrieval_time=NOW,
        fresh_until=NOW + timedelta(hours=1),
        pit_safe=True,
        payload=FinancialFact(metric="spread_bps", value=10, unit="bps", period_end=NOW),
    )
    instrument = InstrumentMetadata(
        ticker="VOD.L",
        company="Vodafone",
        instrument_type="STOCK",
        quote_currency="GBX",
        isa_available=True,
        currently_available=True,
        business_activities=("telecom",),
        activities_verified=True,
        verified_at=NOW - timedelta(hours=1),
        source="test review",
        provider="synthetic-review",
        source_id="fixture-only",
    )
    return LiveManifest.model_validate(
        {
            "reviewed_by": "synthetic test reviewer",
            "qualification_artifacts": [],
            "instruments": [
                {
                    "metadata": instrument,
                    "identifiers": identifiers(),
                    "eligibility_proof_hash": "d" * 64,
                    "ethical_proof_hash": "d" * 64,
                    "spread_bps": 10,
                    "spread_evidence": spread,
                    "corporate_action_coverage_hash": "d" * 64,
                    "corporate_actions_complete": True,
                    "cost_applicability": {"sdrt": "UNKNOWN", "evidence_source": "test only"},
                    "filing_documents": [proof()],
                }
            ],
            "provider_qualifications": [
                qualification(),
                qualification().model_copy(
                    update={
                        "provider": "eodhd",
                        "datasets": ("ohlcv", "news", "corporate_action", "financial"),
                    }
                ),
            ],
            "filing_document_storage_hosts": [REVIEW],
            "tradingagents": inference,
            "ai_hedge_fund": inference,
            "crewai": inference,
            "native_max_calls": 4,
            "native_egress_policy_verified": True,
            "native_egress_verification_hash": "d" * 64,
            "qlib_registry_id": "test-only-model",
            "qlib_artifact_hash": "d" * 64,
            "lean": {"image": "synthetic@sha256:" + "d" * 64},
            "lean_costs": {
                "version": "test-only",
                "source": "test",
                "effective_from": NOW - timedelta(days=1),
                "effective_to": NOW + timedelta(days=1),
                "round_trip_cost_bps": 10,
                "spread_bps": 10,
                "slippage_bps": 0,
                "applicability_reasons": ["synthetic fixture"],
            },
            "lean_parameters": LeanStudyParameters(scenario_policy=SignalPolicy()),
            "lean_qualification": {
                "parameter_hash": "d" * 64,
                "dataset_hash": "d" * 64,
                "historical_eligibility_hash": "d" * 64,
                "survivorship_audit_hash": "d" * 64,
                "corporate_action_audit_hash": "d" * 64,
                "adjustment_policy": "unadjusted_no_actions",
                "approved_by": "fixture",
                "approved_at": NOW,
            },
            "budgets": {},
        }
    )


@pytest.mark.parametrize("invalid", ["duplicate_id", "duplicate_content", "company", "count"])
def test_live_selection_identity_deduplication_and_bound(invalid):
    from money.research.live import VerifiedInstrument

    raw = reviewed_manifest().instruments[0].model_dump()
    selected = raw["filing_documents"][0]
    if invalid == "company":
        raw["filing_documents"] = [selected | {"company_number": "99999999"}]
    else:
        other = selected | {"filing_id": "other", "document_content_hash": "f" * 64}
        if invalid == "duplicate_id":
            other["filing_id"] = selected["filing_id"]
        if invalid == "duplicate_content":
            other["document_content_hash"] = selected["document_content_hash"]
        raw["filing_documents"] = [selected, other] if invalid != "count" else [selected] * 5
    with pytest.raises(ValueError):
        VerifiedInstrument.model_validate(raw)


def test_live_document_selection_requires_financial_provider_coverage():
    from money.research.live import LiveManifest

    raw = reviewed_manifest().model_dump()
    raw["provider_qualifications"][0]["datasets"] = ["filing"]
    with pytest.raises(ValueError, match="FILING_DOCUMENT_COVERAGE_MISSING"):
        LiveManifest.model_validate(raw)


@pytest.mark.parametrize("missing", [None, "currency", "storage"])
def test_manifest_loader_verifies_selected_currency_and_storage_review_bytes(tmp_path, missing):
    from money.research.live import LiveManifest, load_manifest

    raw = reviewed_manifest().model_dump(mode="json")
    artifacts = {}
    for label in ("common", "currency", "storage"):
        content = ("synthetic reviewed artifact " + label).encode()
        path = tmp_path / (label + ".txt")
        path.write_bytes(content)
        artifacts[label] = (hashlib.sha256(content).hexdigest(), path.name)
    common = artifacts["common"][0]
    item = raw["instruments"][0]
    for name in ("eligibility_proof_hash", "ethical_proof_hash", "corporate_action_coverage_hash"):
        item[name] = common
    item["filing_documents"][0]["evidence_hash"] = artifacts["currency"][0]
    raw["filing_document_storage_hosts"][0]["review_evidence_hash"] = artifacts["storage"][0]
    raw["native_egress_verification_hash"] = common
    for name in (
        "historical_eligibility_hash",
        "survivorship_audit_hash",
        "corporate_action_audit_hash",
    ):
        raw["lean_qualification"][name] = common
    for provider in raw["provider_qualifications"]:
        provider["qualification_report_hash"] = common
    raw["qualification_artifacts"] = [
        value for label, value in artifacts.items() if label != missing
    ]
    manifest = LiveManifest.model_validate(raw)
    content = manifest.model_dump_json().encode()
    path = tmp_path / "manifest.json"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    if missing:
        with pytest.raises(ValueError, match="QUALIFICATION_ARTIFACT_MISSING"):
            load_manifest(path, digest)
    else:
        assert load_manifest(path, digest) == manifest


def prepare_snapshot_builder(monkeypatch, *, failing_document=False):
    from money.research import live

    manifest = reviewed_manifest()
    calls = []

    class Circuit:
        def __init__(self, store):
            pass

        def call(self, provider, operation):
            calls.append(provider)
            return operation()

    class Market:
        def __init__(self, *args, usage_mode=None):
            pass

        def fetch(self, identifiers, dataset, snapshot_id, now):
            if dataset != "ohlcv":
                return ()
            return tuple(
                EvidenceRecord(
                    snapshot_id=snapshot_id,
                    source="synthetic market",
                    provider="eodhd",
                    source_id=str(day),
                    canonical_source_id="synthetic-market:" + str(day),
                    observation_time=NOW - timedelta(days=day),
                    publication_time=NOW,
                    retrieval_time=NOW,
                    fresh_until=NOW + timedelta(hours=1),
                    pit_safe=True,
                    payload=PriceBar(
                        open=100, high=102, low=99, close=101, volume=1000000, currency="GBX"
                    ),
                )
                for day in range(70, 0, -1)
            )

    class Filings:
        def __init__(self, *args):
            pass

        def filings(self, *args):
            return ()

    def document_factory(key, admission, *, storage_hosts, usage_mode=None):
        assert key == "synthetic-key" and storage_hosts == (REVIEW,)
        transport = FixtureTransport()
        if failing_document:
            transport.filing["links"]["document_metadata"] = "https://localhost/private"
        return service(transport, admission=admission, storage_hosts=storage_hosts)

    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "synthetic-key")
    monkeypatch.setattr(live, "utc_now", lambda: NOW)
    monkeypatch.setattr(live, "ProviderCircuit", Circuit)
    monkeypatch.setattr(live, "EODHDProvider", Market)
    monkeypatch.setattr(live, "CompaniesHouseProvider", Filings)
    monkeypatch.setattr(live, "CompaniesHouseFilingDocuments", document_factory)
    return live.LiveSnapshotBuilder(manifest, None), manifest, calls


def test_selected_filing_documents_reach_sealed_snapshot_with_safe_provenance(monkeypatch):
    builder, manifest, calls = prepare_snapshot_builder(monkeypatch)
    original_manifest = content_hash(manifest)
    snapshot = builder(manifest.instruments[0].metadata)
    assert calls[-1] == "companies-house:filing-document"
    assert content_hash(manifest) == original_manifest
    financial = next(
        record
        for record in snapshot.evidence
        if isinstance(record.payload, FinancialFact) and record.payload.metric == "cash"
    )
    sidecar = next(
        record
        for record in snapshot.evidence
        if record.source == "Companies House document retrieval provenance"
    )
    provenance = json.loads(sidecar.payload.excerpt)
    assert provenance["content_hash"] == hashlib.sha256(DOCUMENT).hexdigest()
    assert provenance["metadata_url"] == LINK
    assert provenance["conversion_parser"] == "stream-read-xbrl"
    assert financial.provider == sidecar.provider == "companies-house"
    assert "evidence" not in provenance and "raw_document" not in provenance
    assert "synthetic</body>" not in snapshot.model_dump_json()
    assert "synthetic-key" not in snapshot.model_dump_json()
    assert sidecar.payload.url == LINK
    assert sidecar.canonical_source_id == financial.canonical_source_id
    assert financial.fresh_until == sidecar.fresh_until == NOW + timedelta(hours=1)
    assert sidecar.publication_time == financial.publication_time == NOW
    assert ResearchSnapshot.model_validate_json(snapshot.model_dump_json()).hash == snapshot.hash
    changed = snapshot.model_dump()
    record = next(
        item for item in changed["evidence"] if item["evidence_id"] == sidecar.evidence_id
    )
    record["payload"]["excerpt"] = "changed provenance"
    with pytest.raises(ValueError, match="hash mismatch"):
        ResearchSnapshot.model_validate(changed)


def test_selected_document_failure_does_not_fall_back_to_other_financial_evidence(monkeypatch):
    builder, manifest, calls = prepare_snapshot_builder(monkeypatch, failing_document=True)
    # Existing manually reviewed financial evidence must not mask an explicitly selected document failure.
    item = manifest.instruments[0]
    supplemental = item.spread_evidence.model_dump()
    supplemental["payload"].update(metric="cash", unit="GBP", value="123")
    supplemental["hash"] = ""
    replacement = item.model_copy(
        update={"supplemental_evidence": (EvidenceRecord.model_validate(supplemental),)}
    )
    builder.manifest = manifest.model_copy(update={"instruments": (replacement,)})
    with pytest.raises(SourceSecurityError):
        builder(replacement.metadata)
    assert calls[-1] == "companies-house:filing-document"
