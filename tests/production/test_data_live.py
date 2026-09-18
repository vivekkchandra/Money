"""Opt-in live provider checks. Ordinary CI has no secrets or paid calls."""

import hashlib
import os
from importlib.util import find_spec
from urllib.parse import urlsplit

import pytest

from money.data.official_disclosures import require_official_disclosures
from money.data.security import ProviderFailure, SafeFetcher
from money.data.source_policy import IssuerSourcePolicy
from money.data.uk.filing_documents import CompaniesHouseFilingDocuments
from money.data.uk.live import CompaniesHouseProvider, EODHDProvider, Trading212MetadataProvider
from money.schemas.contracts import utc_now


def credential(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"SKIPPED_MISSING_CREDENTIAL: {name}")
    return value


def test_real_trading212_metadata(live_instrument):
    provider = Trading212MetadataProvider(
        credential("TRADING212_API_KEY"), credential("TRADING212_API_SECRET")
    )
    records = provider.instruments()
    expected = live_instrument.identifiers
    assert any(
        row["ticker"] == expected.trading212_id and row["isin"] == expected.isin for row in records
    )
    # Metadata success does not independently certify ISA eligibility.


@pytest.mark.parametrize("dataset", ["ohlcv", "news", "corporate_action"])
def test_real_market_news_actions(dataset, live_manifest, live_instrument):
    specification = live_manifest
    qualification = next(q for q in specification.provider_qualifications if q.provider == "eodhd")
    provider = EODHDProvider(
        credential(specification.market_credential_environment_variable), qualification
    )
    records = provider.fetch(live_instrument.identifiers, dataset, "live-qualification", utc_now())
    assert all(record.provider == "eodhd" and record.hash for record in records)
    if dataset == "ohlcv":
        assert len(records) >= 60, "Real configured market coverage is insufficient"


def test_real_official_filing_source(live_manifest, live_instrument):
    specification = live_manifest
    if specification.issuer_source_policy == IssuerSourcePolicy.OFFICIAL_DISCLOSURES:
        records = _verified_official_disclosure_sources(specification, live_instrument)
        assert any(record.payload.kind == "filing" for record in records)
        return
    provider = CompaniesHouseProvider(
        credential(specification.filings_credential_environment_variable)
    )
    records = provider.filings(live_instrument.identifiers, "live-qualification", utc_now())
    assert records, "Real configured company filing coverage is empty"


def test_real_reviewed_machine_readable_filing_document(live_manifest, live_selector):
    specification = live_manifest
    selected = live_selector(require_filing_documents=True)
    if specification.issuer_source_policy == IssuerSourcePolicy.OFFICIAL_DISCLOSURES:
        records = _verified_official_disclosure_sources(specification, selected)
        assert any(
            record.payload.kind == "financial" and record.payload.metric != "spread_bps"
            for record in records
        ), "Qualified official representation yielded no usable financial evidence"
        return
    key = credential(specification.filings_credential_environment_variable)
    if find_spec("stream_read_xbrl") is None:
        pytest.skip("BLOCKED_EXTERNAL_INFRA: pinned stream-read-xbrl runtime")
    admission = next(
        item for item in specification.provider_qualifications if item.provider == "companies-house"
    )
    provider = CompaniesHouseFilingDocuments(
        key, admission, storage_hosts=specification.filing_document_storage_hosts
    )
    proof = selected.filing_documents[0]
    try:
        bundle = provider.fetch(
            selected.identifiers, proof.filing_id, "live-document-qualification", proof
        )
    except ProviderFailure as error:
        if error.retryable:
            pytest.skip("BLOCKED_EXTERNAL_INFRA: Companies House document transport")
        raise
    except ValueError as error:
        if str(error) == "XBRL_PARSER_UNAVAILABLE":
            pytest.skip("BLOCKED_EXTERNAL_INFRA: pinned XBRL parser dependencies")
        raise
    assert bundle.evidence, "Reviewed live representation yielded no usable financial evidence"
    assert bundle.content_hash == proof.document_content_hash
    assert bundle.company_number == selected.identifiers.companies_house_number
    assert bundle.availability_time == bundle.retrieval_time
    assert bundle.original_publication_time is None
    assert bundle.conversion_source_attestation == "PINNED_SOURCE_VERIFIED"
    assert bundle.conversion_source_hash
    assert "raw_document" not in bundle.model_dump()
    assert all(
        record.provider == "companies-house"
        and record.snapshot_id == "live-document-qualification"
        and record.publication_time == bundle.retrieval_time
        and record.fresh_until <= min(admission.valid_until, selected.identifiers.valid_until)
        for record in bundle.evidence
    )


def _verified_official_disclosure_sources(specification, selected):
    """Exercise actual approved issuer URLs, not merely manifest declarations.

    The manifest already pins reviewed technical conversion and source bytes.
    Current retrieval must yield exactly that representation; replacing a report
    cannot inherit old financial extraction, publication or currency review.
    No credentials, redirects or alternative hosts are accepted by this probe.
    """
    qualifications = {item.provider: item for item in specification.provider_qualifications}

    def validate():
        require_official_disclosures(
            selected.official_disclosures, selected.identifiers, selected.supplemental_evidence,
            qualifications, utc_now(), jurisdiction=selected.issuer_jurisdiction,
            jurisdiction_proof_hash=selected.issuer_jurisdiction_proof_hash,
            issuer_company_number=selected.issuer_company_number,
        )

    validate()
    for proof in selected.official_disclosures:
        host = urlsplit(proof.source_url).hostname
        assert host, "Official disclosure source hostname missing"
        fetcher = SafeFetcher(
            frozenset({host}), maximum_bytes=2_000_000,
            timeout_seconds=20, maximum_redirects=0,
        )
        response = fetcher.get(
            proof.source_url,
            mime_types=(
                "application/pdf", "text/html", "text/plain", "application/xhtml+xml",
                "application/xml", "application/zip", "application/json",
            ),
        )
        assert response.content, "Official disclosure source returned an empty representation"
        if hashlib.sha256(response.content).hexdigest() != proof.document_sha256:
            raise ValueError("OFFICIAL_DISCLOSURE_LIVE_CONTENT_MISMATCH")
    # Do not count an observation that expired during the real network requests.
    validate()
    return selected.supplemental_evidence
