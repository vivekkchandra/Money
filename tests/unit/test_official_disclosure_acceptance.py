"""Offline synthetic transports exercise production checks, not live acceptance."""

import runpy
from pathlib import Path

import pytest
from test_filing_documents import NOW
from test_official_disclosure_runtime import BYTES, official_manifest

from money.data.security import FetchResult, ProviderFailure
from money.research import live

ROOT = Path(__file__).resolve().parents[2]
SELECTOR = runpy.run_path(str(ROOT / "scripts/check_live_isa.py"))
PRODUCTION = runpy.run_path(str(ROOT / "tests/production/test_data_live.py"))
GLOBALS = PRODUCTION["_verified_official_disclosure_sources"].__globals__


@pytest.fixture
def manifest(monkeypatch):
    monkeypatch.setattr(live, "utc_now", lambda: NOW)
    monkeypatch.setitem(GLOBALS, "utc_now", lambda: NOW)
    return official_manifest()


def fake_fetcher(monkeypatch, *, content=BYTES["document"], failure=None):
    calls = []

    class Transport:
        def __init__(self, hosts, **limits):
            assert hosts == frozenset({"issuer.example"})
            assert limits == {
                "maximum_bytes": 2_000_000,
                "timeout_seconds": 20,
                "maximum_redirects": 0,
            }

        def get(self, url, **kwargs):
            assert "headers" not in kwargs
            assert "application/xhtml+xml" in kwargs["mime_types"]
            calls.append(url)
            if failure is not None:
                raise failure
            return FetchResult(content=content, mime="application/xhtml+xml")

    monkeypatch.setitem(GLOBALS, "SafeFetcher", Transport)
    return calls


def test_official_financial_proofs_are_selectable_without_ch_documents(manifest):
    selected = SELECTOR["select_candidates"](manifest, NOW, require_filing_documents=True)
    assert selected == manifest.instruments
    assert selected[0].official_disclosures
    assert selected[0].filing_documents == ()


def test_missing_official_proof_does_not_satisfy_required_document_selection(manifest):
    invalid = manifest.model_copy(update={
        "instruments": (manifest.instruments[0].model_copy(update={"official_disclosures": ()}),),
    })
    with pytest.raises(SELECTOR["LiveAcceptanceFailure"], match="NO_CURRENT_VERIFIED_STOCK_CANDIDATE"):
        SELECTOR["select_candidates"](invalid, NOW, require_filing_documents=True)


def test_wrong_official_financial_identity_cannot_be_selected(manifest):
    selected = manifest.instruments[0]
    bad_proof = selected.official_disclosures[0].model_copy(update={"eodhd_symbol": "OTHER.LSE"})
    invalid = manifest.model_copy(update={
        "instruments": (selected.model_copy(update={"official_disclosures": (bad_proof,)}),),
    })
    with pytest.raises(SELECTOR["LiveAcceptanceFailure"], match="QUALIFIED_STOCK_UNIVERSE_INVALID"):
        SELECTOR["select_candidates"](invalid, NOW, require_filing_documents=True)


@pytest.mark.parametrize("operation", [
    "test_real_official_filing_source",
    "test_real_reviewed_machine_readable_filing_document",
])
def test_real_acceptance_route_checks_official_bytes_without_ch_or_api_key(
    manifest, monkeypatch, operation,
):
    calls = fake_fetcher(monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("Official route must not require CH credentials, clients or its parser")

    for name in ("credential", "CompaniesHouseProvider", "CompaniesHouseFilingDocuments", "find_spec"):
        monkeypatch.setitem(GLOBALS, name, forbidden)
    selected = manifest.instruments[0]
    if operation == "test_real_official_filing_source":
        PRODUCTION[operation](manifest, selected)
    else:
        def selector(**kwargs):
            assert kwargs == {"require_filing_documents": True}
            return selected
        PRODUCTION[operation](manifest, selector)
    assert calls == [selected.official_disclosures[0].source_url]


def test_official_live_source_cannot_change_behind_reviewed_hash(manifest, monkeypatch):
    fake_fetcher(monkeypatch, content=b"synthetic replacement document differs")
    with pytest.raises(ValueError, match="OFFICIAL_DISCLOSURE_LIVE_CONTENT_MISMATCH"):
        PRODUCTION["_verified_official_disclosure_sources"](manifest, manifest.instruments[0])


def test_empty_official_live_source_is_not_qualified(manifest, monkeypatch):
    fake_fetcher(monkeypatch, content=b"")
    with pytest.raises(AssertionError, match="empty representation"):
        PRODUCTION["_verified_official_disclosure_sources"](manifest, manifest.instruments[0])


@pytest.mark.parametrize("status,retryable", [(401, False), (404, False), (503, True)])
def test_source_failure_is_never_skipped_as_success(manifest, monkeypatch, status, retryable):
    fake_fetcher(monkeypatch, failure=ProviderFailure(
        "PROVIDER_UNAVAILABLE", retryable=retryable, http_status=status,
    ))
    with pytest.raises(ProviderFailure, match="PROVIDER_UNAVAILABLE"):
        PRODUCTION["_verified_official_disclosure_sources"](manifest, manifest.instruments[0])


def test_missing_financial_record_fails_before_network(manifest, monkeypatch):
    calls = fake_fetcher(monkeypatch)
    original = manifest.instruments[0]
    selected = original.model_copy(update={"supplemental_evidence": tuple(
        item for item in original.supplemental_evidence if item.payload.kind == "filing"
    )})
    with pytest.raises(ValueError, match="OFFICIAL_DISCLOSURE_RECORD_MISSING"):
        PRODUCTION["_verified_official_disclosure_sources"](manifest, selected)
    assert calls == []


def test_production_broker_probe_uses_actual_configured_readonly_keys(manifest, monkeypatch):
    requested = []
    selected = manifest.instruments[0]

    def credential(name):
        requested.append(name)
        return "synthetic-test-only-credential"

    class Broker:
        def __init__(self, key, secret):
            assert key == secret == "synthetic-test-only-credential"

        def instruments(self):
            return ({
                "ticker": selected.identifiers.trading212_id,
                "isin": selected.identifiers.isin,
            },)

    monkeypatch.setitem(GLOBALS, "credential", credential)
    monkeypatch.setitem(GLOBALS, "Trading212MetadataProvider", Broker)
    PRODUCTION["test_real_trading212_metadata"](selected)
    assert requested == ["TRADING212_API_KEY", "TRADING212_API_SECRET"]
