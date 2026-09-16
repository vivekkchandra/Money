"""Real auth/admission/rate-limit transactions; catalogue qualification is a test fixture."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from test_commercial_accounts import configuration, customer, headers
from test_commercial_product import store as store

from money.api.app import create_app
from money.data.identifiers import InstrumentIdentifiers
from money.product import models as product
from money.product.service import ProductService
from money.reference import CommercialLicenceError
from money.schemas.contracts import InstrumentMetadata, utc_now
from money.storage.models import jobs


def reviewed_fixture(now, **changes):
    metadata = InstrumentMetadata(
        ticker="FIXTURE.L",
        company="Synthetic Catalogue Fixture",
        instrument_type="STOCK",
        quote_currency="GBX",
        isa_available=True,
        currently_available=True,
        business_activities=("telecommunications",),
        activities_verified=True,
        verified_at=now - timedelta(minutes=1),
        source="Test-only review",
        provider="fixture-review",
        source_id="fixture-1",
    ).model_copy(update=changes)
    identifiers = InstrumentIdentifiers(
        ticker=metadata.ticker,
        company_name=metadata.company,
        trading212_id="FIXTUREl_EQ",
        exchange_ticker="FIXTURE",
        exchange="XLON",
        isin="GB00BH4HKS39",
        quote_currency="GBX",
        verified_at=now - timedelta(minutes=1),
        valid_until=now + timedelta(hours=1),
        source="Test only",
    )
    return SimpleNamespace(
        instruments=(SimpleNamespace(metadata=metadata, identifiers=identifiers),),
        provider_qualifications=(),
    )


def live_configuration(store, monkeypatch, tmp_path, **changes):
    manifest = reviewed_fixture(utc_now(), **changes)
    calls = []

    def load(path, digest):
        calls.append((path, digest))
        return manifest

    # No live provider result is claimed: replace only the trusted release-file boundary.
    monkeypatch.setattr("money.research.live.load_manifest", load)
    monkeypatch.setattr("money.api.app.require_manifest_commercial_rights", lambda *_: ())
    config = configuration(store).model_copy(
        update={
            "money_research_mode": "live",
            "money_live_manifest": tmp_path / "fixture-manifest.json",
            "money_live_manifest_sha256": "a" * 64,
        }
    )
    return config, calls


def test_search_requires_service_session_and_actual_workspace_membership(store):
    with TestClient(create_app(configuration(store), store)) as client:
        alice = customer(client.app.state.accounts, "search-alice@example.test")
        bob = customer(client.app.state.accounts, "search-bob@example.test")
        path = "/research/instruments?query=demo"
        assert client.get(path).status_code == 401
        assert (
            client.get(path, headers={"X-Money-Workspace": alice["workspace"]["id"]}).status_code
            == 401
        )
        forged = {**headers(alice), "X-Money-Workspace": bob["workspace"]["id"]}
        assert client.get(path, headers=forged).status_code == 403
        page = client.get(path, headers=headers(alice))
        assert page.status_code == 200, page.text
        assert page.json()["mode"] == "demo"
        assert page.json()["instruments"][0]["synthetic"] is True
        assert (
            client.get("/research/instruments?query=Barclays", headers=headers(alice)).json()[
                "total"
            ]
            == 0
        )


@pytest.mark.parametrize(
    "query",
    [
        "query=",
        "query=%20",
        "query=x&limit=21",
        "query=x&offset=1001",
        "query=x&unknown=1",
        "query=x&query=y",
        "query=x&limit=1&limit=2",
        "query=" + "x" * 81,
        "query=x%0A",
    ],
)
def test_search_rejects_invalid_query_before_expensive_work(store, query):
    with TestClient(create_app(configuration(store), store)) as client:
        user = customer(client.app.state.accounts, "search-input@example.test")
        response = client.get("/research/instruments?" + query, headers=headers(user))
        assert response.status_code == 422, response.text
        assert "input" not in response.json()
        assert store.list_jobs() == []


def test_unconfigured_catalogue_is_safe_unavailable_not_demo_fallback(store):
    config = configuration(store, money_enable_synthetic_demo=False)
    with TestClient(create_app(config, store)) as client:
        user = customer(client.app.state.accounts, "search-unavailable@example.test")
        response = client.get("/research/instruments?query=Barclays", headers=headers(user))
        assert response.status_code == 503
        assert response.json()["code"] == "INSTRUMENT_SEARCH_UNAVAILABLE"
        assert "instruments" not in response.json()


@pytest.mark.parametrize("environment,mode", [("production", "unconfigured"), ("test", "live")])
def test_mutated_configuration_cannot_expose_synthetic_catalogue(store, environment, mode):
    config = configuration(store)
    with TestClient(create_app(config, store)) as client:
        user = customer(client.app.state.accounts, "search-demo-boundary@example.test")
        config.money_env = environment
        config.money_research_mode = mode
        assert (
            client.get("/research/instruments?query=demo", headers=headers(user)).status_code == 503
        )


@pytest.mark.parametrize(
    "changes,ticker",
    [
        ({}, "UNKNOWN.L"),
        ({}, "DEMO.L"),
        ({"isa_available": None}, "FIXTURE.L"),
        ({"isa_available": False}, "FIXTURE.L"),
        ({"quote_currency": "USD"}, "FIXTURE.L"),
        ({"activities_verified": False}, "FIXTURE.L"),
        ({"business_activities": ("weapons",)}, "FIXTURE.L"),
        ({"verified_at": utc_now() - timedelta(days=2)}, "FIXTURE.L"),
        ({"verified_at": utc_now() + timedelta(days=2)}, "FIXTURE.L"),
    ],
)
def test_live_admission_rejects_before_job_or_usage_insert(
    store, monkeypatch, tmp_path, changes, ticker
):
    config, loads = live_configuration(store, monkeypatch, tmp_path, **changes)
    with TestClient(create_app(config, store)) as client:
        user = customer(client.app.state.accounts, "search-rejected@example.test")

        def forbidden(*args, **kwargs):
            pytest.fail("Rejected instrument must not reach paid quota admission")

        monkeypatch.setattr(ProductService, "reserve_research", forbidden)
        response = client.post("/research/jobs", headers=headers(user), json={"ticker": ticker})
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "INSTRUMENT_NOT_RESEARCHABLE"
        assert len(loads) == 1  # The validated startup manifest, no remote fetch per keystroke.
        with store.engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(jobs)) == 0
            assert connection.scalar(select(func.count()).select_from(product.usage_records)) == 0


def test_selected_live_instrument_is_canonical_idempotent_and_workspace_scoped(
    store, monkeypatch, tmp_path
):
    config, loads = live_configuration(store, monkeypatch, tmp_path)
    with TestClient(create_app(config, store)) as client:
        user = customer(client.app.state.accounts, "search-selected@example.test")
        outsider = customer(client.app.state.accounts, "search-outsider@example.test")
        auth = {**headers(user), "Idempotency-Key": "selected-company"}
        result = client.get("/research/instruments?query=catalogue", headers=auth)
        assert result.status_code == 200, result.text
        selection = result.json()["instruments"][0]
        assert selection["research_allowed"] and not selection["synthetic"]
        actor = client.app.state.accounts.principal(user["session_token"], user["workspace"]["id"])
        ProductService(store).summary(actor)
        # A fixture paid entitlement; this is not a Stripe lifecycle qualification.
        with store.engine.begin() as connection:
            connection.execute(
                update(product.subscriptions)
                .where(product.subscriptions.c.workspace_id == actor.workspace_id)
                .values(plan="PRO")
            )
        created = client.post(
            "/research/jobs", headers=auth, json={"ticker": selection["instrument_id"]}
        )
        assert created.status_code == 202, created.text
        duplicate = client.post(
            "/research/jobs", headers=auth, json={"ticker": selection["instrument_id"]}
        )
        assert duplicate.json()["id"] == created.json()["id"]
        assert ProductService(store).summary(actor)["used"] == 1
        assert (
            client.get(
                f"/research/jobs/{created.json()['id']}", headers=headers(outsider)
            ).status_code
            == 404
        )
        assert len(loads) == 1
    with TestClient(create_app(config, store)) as client:
        restored = client.get(f"/research/jobs/{created.json()['id']}", headers=headers(user))
        assert restored.status_code == 200 and restored.json()["ticker"] == "FIXTURE.L"


def test_commercial_rights_are_rechecked_before_search_and_admission(store, monkeypatch, tmp_path):
    config, _ = live_configuration(store, monkeypatch, tmp_path)
    with TestClient(create_app(config, store)) as client:
        user = customer(client.app.state.accounts, "search-rights@example.test")

        def revoked(*args):
            raise CommercialLicenceError("MISSING_LICENCE")

        monkeypatch.setattr("money.api.app.require_manifest_commercial_rights", revoked)
        response = client.get("/research/instruments?query=fixture", headers=headers(user))
        assert response.status_code == 503 and response.json()["code"] == "MISSING_LICENCE"
        assert "Fixture" not in response.text
        response = client.post(
            "/research/jobs", headers=headers(user), json={"ticker": "FIXTURE.L"}
        )
        assert response.status_code == 503 and store.list_jobs() == []


def test_search_limit_is_durable_and_cannot_be_bypassed_by_switching_workspaces(store):
    config = configuration(store)
    with TestClient(create_app(config, store)) as client:
        service = client.app.state.accounts
        user = customer(service, "search-limit@example.test")
        principal = service.principal(user["session_token"], user["workspace"]["id"])
        other = service.create_workspace(
            service.authenticated_user(user["session_token"]), "Second"
        )
        limiter = store.for_workspace("instrument-search-users")
        for _ in range(60):
            assert limiter.consume_rate_limit(principal.user_id, 60, 60)["allowed"]
    with TestClient(create_app(config, store)) as client:
        for workspace in (user["workspace"]["id"], other["id"]):
            response = client.get(
                "/research/instruments?query=demo",
                headers={
                    **headers(user),
                    "X-Money-Workspace": workspace,
                },
            )
            assert response.status_code == 429
            assert int(response.headers["Retry-After"]) > 0
