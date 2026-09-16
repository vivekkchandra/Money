"""Opt-in live provider checks. Ordinary CI has no secrets or paid calls."""

import os
from pathlib import Path

import pytest

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
