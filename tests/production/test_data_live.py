"""Opt-in live provider checks. Ordinary CI has no secrets or paid calls."""

import os
from importlib.util import find_spec
from pathlib import Path

import pytest

from money.data.security import ProviderFailure
from money.data.uk.filing_documents import CompaniesHouseFilingDocuments
from money.data.uk.live import CompaniesHouseProvider, EODHDProvider, Trading212MetadataProvider
from money.research.live import load_manifest
from money.schemas.contracts import utc_now


def manifest():
    if os.environ.get("MONEY_RUN_PRODUCTION_INTEGRATION") != "1":
        pytest.skip("SKIPPED_MISSING_CREDENTIAL: production integration opt-in absent")
    path, digest = (
        os.environ.get("MONEY_LIVE_MANIFEST"),
        os.environ.get("MONEY_LIVE_MANIFEST_SHA256"),
    )
    if not path or not digest:
        pytest.skip("SKIPPED_MISSING_CREDENTIAL: qualification manifest and hash")
    if not Path(path).is_file():
        pytest.skip("SKIPPED_EXTERNAL_UNAVAILABLE: qualification manifest file")
    return load_manifest(Path(path), digest)


def credential(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"SKIPPED_MISSING_CREDENTIAL: {name}")
    return value


def test_real_trading212_metadata():
    specification = manifest()
    provider = Trading212MetadataProvider(
        credential("TRADING212_METADATA_API_KEY"), credential("TRADING212_METADATA_API_SECRET")
    )
    records = provider.instruments()
    expected = specification.instruments[0].identifiers
    assert any(
        row["ticker"] == expected.trading212_id and row["isin"] == expected.isin for row in records
    )
    # Metadata success does not independently certify ISA eligibility.


@pytest.mark.parametrize("dataset", ["ohlcv", "news", "corporate_action"])
def test_real_market_news_actions(dataset):
    specification = manifest()
    qualification = next(q for q in specification.provider_qualifications if q.provider == "eodhd")
    provider = EODHDProvider(
        credential(specification.market_credential_environment_variable), qualification
    )
    records = provider.fetch(
        specification.instruments[0].identifiers, dataset, "live-qualification", utc_now()
    )
    assert all(record.provider == "eodhd" and record.hash for record in records)
    if dataset == "ohlcv":
        assert len(records) >= 60, "Real configured market coverage is insufficient"


def test_real_official_filing_source():
    specification = manifest()
    provider = CompaniesHouseProvider(
        credential(specification.filings_credential_environment_variable)
    )
    records = provider.filings(
        specification.instruments[0].identifiers, "live-qualification", utc_now()
    )
    assert records, "Real configured company filing coverage is empty"


def test_real_reviewed_machine_readable_filing_document():
    specification = manifest()
    selected = next((item for item in specification.instruments if item.filing_documents), None)
    if selected is None:
        pytest.skip("SKIPPED_MISSING_CREDENTIAL: reviewed filing selection configuration")
    key = credential(specification.filings_credential_environment_variable)
    if find_spec("stream_read_xbrl") is None:
        pytest.skip("SKIPPED_EXTERNAL_UNAVAILABLE: pinned stream-read-xbrl runtime")
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
            pytest.skip("SKIPPED_EXTERNAL_UNAVAILABLE: Companies House document transport")
        raise
    except ValueError as error:
        if str(error) == "XBRL_PARSER_UNAVAILABLE":
            pytest.skip("SKIPPED_EXTERNAL_UNAVAILABLE: pinned XBRL parser dependencies")
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
