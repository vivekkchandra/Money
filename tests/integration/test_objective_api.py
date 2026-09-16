"""Persisted, tenant-scoped projections. Native/provider results are test fixtures."""

import pytest
from fastapi.testclient import TestClient
from test_commercial_accounts import configuration, customer, headers
from test_commercial_product import store as store
from test_signal_persistence import qualified_fixture

from money.api.app import create_app
from money.schemas.contracts import ResearchMandate
from money.worker import run_once


def test_objective_page_is_owned_persisted_and_does_not_promote_watch(store, monkeypatch):
    # Rights are a fixture seam, not a production provider qualification.
    monkeypatch.setattr("money.api.app.require_snapshot_commercial_rights", lambda *_, **__: ())
    with TestClient(create_app(configuration(store), store)) as client:
        alice = customer(client.app.state.accounts, "objective-alice@example.test")
        bob = customer(client.app.state.accounts, "objective-bob@example.test")
        client.app.state.settings = client.app.state.settings.model_copy(
            update={"money_research_mode": "live"},
        )
        scoped = store.for_workspace(alice["workspace"]["id"])
        for index in range(2):
            job = scoped.create_job("DEMO.L", ResearchMandate())
            run_once(scoped, qualified_fixture(rich_prices=True), worker_id=f"objective-{index}", mode="live")
            assert scoped.get_job(job["id"])["status"] == "COMPLETE"
        response = client.get("/research/objective?limit=1", headers=headers(alice))
        assert response.status_code == 200, response.json()
        page = response.json()
        assert page["examined"] == 1 and page["has_more"] is True
        assert page["opportunities"] == []  # WATCH is not a qualified opportunity.
        assert page["objective"]["target_return"] == "5.0"
        second = client.get("/research/objective?limit=1&offset=1", headers=headers(alice)).json()
        assert second["examined"] == 1 and second["has_more"] is False
        assert client.get("/research/objective", headers=headers(bob)).json()["examined"] == 0
        assert client.get("/research/objective", headers={
            **headers(bob), "X-Money-Workspace": alice["workspace"]["id"],
        }).status_code == 403
        assert client.get("/research/objective").status_code == 401


@pytest.mark.parametrize("mode", ["unconfigured", "demo", "live_rnd"])
def test_objective_never_converts_other_modes_to_live(store, mode):
    with TestClient(create_app(configuration(store), store)) as client:
        alice = customer(client.app.state.accounts, "objective-mode@example.test")
        client.app.state.settings = client.app.state.settings.model_copy(
            update={"money_research_mode": mode},
        )
        response = client.get("/research/objective", headers=headers(alice))
        assert response.status_code == 409
        assert response.json()["code"] == "LIVE_RESEARCH_REQUIRED"


def test_objective_page_bounds_and_durable_limit(store):
    with TestClient(create_app(configuration(store), store)) as client:
        alice = customer(client.app.state.accounts, "objective-rate@example.test")
        client.app.state.settings = client.app.state.settings.model_copy(
            update={"money_research_mode": "live"},
        )
        for query in ("limit=51", "limit=0", "offset=-1", "offset=10001", "limit=1&limit=2", "stretch=5000"):
            assert client.get(f"/research/objective?{query}", headers=headers(alice)).status_code == 422
        for _ in range(30):
            assert client.get("/research/objective", headers=headers(alice)).status_code == 200
        assert client.get("/research/objective", headers=headers(alice)).status_code == 429
