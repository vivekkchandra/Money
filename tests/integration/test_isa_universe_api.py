"""HTTP read-time universe policy with explicitly synthetic review evidence."""

from datetime import timedelta

from fastapi.testclient import TestClient
from test_commercial_accounts import configuration, customer, headers
from test_commercial_product import store as store
from test_instrument_search import reviewed_fixture

from money.api.app import create_app
from money.data.instruments import InstrumentCatalogue
from money.data.qualification import ProviderQualification
from money.schemas.contracts import utc_now


def catalogue_fixture(now):
    manifest = reviewed_fixture(now)
    manifest.provider_qualifications = (ProviderQualification(
        provider="fixture-review", datasets=("instruments",),
        earliest_observation=now - timedelta(days=1),
        publication_times="ORIGINAL_PUBLICATION_VERIFIED", maximum_age_seconds=3600,
        production_qualified=True, qualified_by="Fixture only",
        qualification_report_hash="a" * 64, verified_at=now - timedelta(minutes=1),
        valid_until=now + timedelta(hours=1), attribution="Fixture only",
        source_documentation="https://example.test/fixture",
    ),)
    return InstrumentCatalogue.from_manifest(manifest)


def test_live_universe_is_not_research_history_or_a_complete_broker_directory(store, monkeypatch):
    monkeypatch.setattr("money.api.app.require_manifest_commercial_rights", lambda *_, **__: ())
    now = utc_now()
    with TestClient(create_app(configuration(store), store)) as client:
        alice = customer(client.app.state.accounts, "universe-alice@example.test")
        client.app.state.settings = client.app.state.settings.model_copy(
            update={"money_research_mode": "live"},
        )
        client.app.state.instrument_catalogue = catalogue_fixture(now)
        assert client.get("/research/universe").status_code == 401
        response = client.get("/research/universe?query=fixture&limit=1", headers=headers(alice))
        assert response.status_code == 200, response.json()
        page = response.json()
        assert page["coverage"] == "reviewed_manifest" and page["complete_broker_universe"] is False
        assert page["total"] == 1
        assert page["instruments"][0]["eligibility"] == "VERIFIED_ELIGIBLE"
        assert page["instruments"][0]["currency"] == "GBX"
        assert store.list_jobs() == []  # Browsing never creates paid work.
        assert client.get("/research/universe?offset=1", headers=headers(alice)).json()["instruments"] == []
        for query in ("limit=51", "offset=-1", "query=x&query=y", "allow_usd=true"):
            assert client.get(f"/research/universe?{query}", headers=headers(alice)).status_code == 422
        monkeypatch.setattr("money.api.app.utc_now", lambda: now + timedelta(days=2))
        assert client.get("/research/universe", headers=headers(alice)).status_code == 503


def test_no_qualified_catalogue_returns_unavailable_not_demo(store):
    with TestClient(create_app(configuration(store), store)) as client:
        alice = customer(client.app.state.accounts, "universe-unavailable@example.test")
        client.app.state.settings = client.app.state.settings.model_copy(
            update={"money_research_mode": "live"},
        )
        assert client.get("/research/universe", headers=headers(alice)).status_code == 503
