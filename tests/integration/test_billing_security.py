"""Billing admission and replay regressions using real local database transactions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Lock

import pytest
from sqlalchemy import func, select
from test_commercial_product import actor, billing_settings, ensure_actor, submit
from test_commercial_product import store as store

from money.product import models as db
from money.product.billing import BillingUnavailable, StripeGateway, authenticate_event
from money.product.service import EntitlementDenied, ProductService
from money.product.worker import reconcile_once
from money.schemas.contracts import utc_now
from money.storage.models import jobs


def existing_customer(store):
    ensure_actor(store, actor())
    product = ProductService(store, billing_settings())
    product.summary(actor())
    with store.engine.begin() as connection:
        connection.execute(
            db.subscriptions.update()
            .where(db.subscriptions.c.workspace_id == actor().workspace_id)
            .values(customer_id="cus_one")
        )
    return product


def test_unsigned_oversized_timestamp_is_a_safe_rejection():
    with pytest.raises(ValueError):
        authenticate_event(b"{}", "t=" + "9" * 2000 + ",v1=wrong", billing_settings())


def test_lost_checkout_response_reuses_durable_identical_parameters(store, monkeypatch):
    product = existing_customer(store)
    calls = []

    class Gateway:
        hosted_url = staticmethod(StripeGateway.hosted_url)

        def __init__(self, settings):
            pass

        def request(self, method, path, data=None, key=None):
            assert path == "/checkout/sessions"
            calls.append((key, dict(data)))
            if len(calls) == 1:
                raise BillingUnavailable("simulated response loss after provider accepted request")
            return {"url": "https://checkout.stripe.com/c/pay/fixture"}

    monkeypatch.setattr("money.product.service.StripeGateway", Gateway)
    with pytest.raises(BillingUnavailable):
        product.checkout(actor(), "PRO", "original-idempotency-key")
    with store.engine.connect() as connection:
        persisted = connection.execute(select(db.subscriptions)).mappings().one()
        assert persisted["checkout_payload"] == calls[0][1]
        assert persisted["checkout_key"] == "original-idempotency-key"
        assert persisted["checkout_url"] is None
    # A release/config change while retrying must not rewrite Stripe idempotency input.
    product.settings.money_stripe_prices = {"PRO": "price_changed_between_requests"}
    assert product.checkout(actor(), "PRO", "original-idempotency-key") == (
        "https://checkout.stripe.com/c/pay/fixture"
    )
    assert calls[0] == calls[1]


def test_concurrent_double_click_uses_one_checkout_identity(store, monkeypatch):
    product = existing_customer(store)
    calls = []
    mutex = Lock()

    class Gateway:
        hosted_url = staticmethod(StripeGateway.hosted_url)

        def __init__(self, settings):
            pass

        def request(self, method, path, data=None, key=None):
            assert path == "/checkout/sessions"
            with mutex:
                calls.append((key, dict(data)))
            return {"url": "https://checkout.stripe.com/c/pay/one-session"}

    monkeypatch.setattr("money.product.service.StripeGateway", Gateway)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(product.checkout, actor(), "PRO", "same-double-click-key")
            for _ in range(2)
        ]
        urls = [future.result(timeout=10) for future in futures]
    assert len(set(urls)) == 1
    assert calls and all(call == calls[0] for call in calls)


def test_stripe_outage_does_not_hold_global_research_admission_lock(store, monkeypatch):
    product = existing_customer(store)
    started, release = Event(), Event()

    class Gateway:
        hosted_url = staticmethod(StripeGateway.hosted_url)

        def __init__(self, settings):
            pass

        def request(self, method, path, data=None, key=None):
            assert path == "/checkout/sessions"
            started.set()
            if not release.wait(8):
                raise AssertionError("test provider wait timed out")
            return {"url": "https://checkout.stripe.com/c/pay/fixture"}

    monkeypatch.setattr("money.product.service.StripeGateway", Gateway)
    with ThreadPoolExecutor(max_workers=2) as executor:
        checkout = executor.submit(product.checkout, actor(), "PRO", "slow-provider-key")
        try:
            assert started.wait(3)
            research = executor.submit(submit, store, actor("unrelated-workspace"), "research-key")
            assert research.result(timeout=3)["ticker"] == "DEMO.L"
        finally:
            release.set()
        assert checkout.result(timeout=3).startswith("https://checkout.stripe.com/")


def pending_event(store, event_id="evt_identity", created_at=None):
    with store.engine.begin() as connection:
        connection.execute(
            db.billing_events.insert().values(
                id=event_id,
                kind="customer.subscription.updated",
                subscription_id="sub_one",
                customer_id="cus_one",
                content_hash="f" * 64,
                state="PENDING",
                attempts=0,
                created_at=created_at or utc_now(),
                available_at=utc_now(),
            )
        )


def valid_subscription():
    now = utc_now()
    return {
        "id": "sub_one",
        "customer": "cus_one",
        "livemode": False,
        "metadata": {"workspace_id": actor().workspace_id},
        "status": "active",
        "items": {
            "data": [
                {
                    "quantity": 1,
                    "price": {"id": "price_pro"},
                    "current_period_start": int(now.timestamp()),
                    "current_period_end": int((now + timedelta(days=30)).timestamp()),
                }
            ]
        },
    }


@pytest.mark.parametrize(
    "mismatch",
    [
        "subscription",
        "customer",
        "workspace",
        "mode",
        "price",
        "quantity",
        "period",
    ],
)
def test_reconciliation_never_grants_paid_access_for_mismatched_identity(store, mismatch):
    product = existing_customer(store)
    pending_event(store)
    subscription = valid_subscription()
    if mismatch == "subscription":
        subscription["id"] = "sub_other"
    elif mismatch == "customer":
        subscription["customer"] = "cus_other"
    elif mismatch == "workspace":
        subscription["metadata"]["workspace_id"] = "other-tenant"
    elif mismatch == "mode":
        subscription["livemode"] = True
    elif mismatch == "price":
        subscription["items"]["data"][0]["price"]["id"] = "price_not_in_server_config"
    elif mismatch == "quantity":
        subscription["items"]["data"][0]["quantity"] = -1
    else:
        subscription["items"]["data"][0]["current_period_end"] = 0

    class Gateway:
        def request(self, method, path):
            return subscription

    assert reconcile_once(product, Gateway())
    assert product.summary(actor())["plan"] == "FREE"
    with store.engine.connect() as connection:
        row = connection.execute(select(db.billing_events)).mappings().one()
        assert row["state"] == "PENDING" and row["attempts"] == 1


def test_postgres_reconciliations_observe_provider_after_workspace_lock(store):
    if store.engine.dialect.name != "postgresql":
        pytest.skip("Requires real PostgreSQL row-lock semantics")
    product = existing_customer(store)
    pending_event(store, "evt_first", utc_now() - timedelta(seconds=1))
    pending_event(store, "evt_second")
    entered, release, second_fetch = Event(), Event(), Event()
    mutex = Lock()
    calls = 0

    class Gateway:
        def request(self, method, path):
            nonlocal calls
            with mutex:
                calls += 1
                call = calls
            value = valid_subscription()
            if call == 1:
                entered.set()
                assert release.wait(8)
                value["status"] = "past_due"
            else:
                second_fetch.set()
            return value

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(reconcile_once, product, Gateway())
        try:
            assert entered.wait(3)
            second = executor.submit(reconcile_once, product, Gateway())
            assert not second_fetch.wait(0.2)
        finally:
            release.set()
        assert first.result(timeout=3) and second.result(timeout=3)
    assert second_fetch.is_set()
    assert product.summary(actor())["status"] == "active"


def paid_customer(store):
    product = existing_customer(store)
    with store.engine.begin() as connection:
        connection.execute(
            db.subscriptions.update()
            .where(db.subscriptions.c.workspace_id == actor().workspace_id)
            .values(plan="PRO", subscription_id="sub_one", status="active")
        )
    assert product.summary(actor())["entitlements"]["live_research"] is True
    return product


@pytest.mark.parametrize("billing_state", ["PENDING", "FAILED"])
def test_unresolved_matching_billing_event_blocks_paid_jobs_without_usage_charge(
    store, billing_state
):
    product = paid_customer(store)
    pending_event(store)
    with store.engine.begin() as connection:
        connection.execute(
            db.billing_events.update()
            .where(db.billing_events.c.id == "evt_identity")
            .values(state=billing_state)
        )

    with pytest.raises(EntitlementDenied, match="^BILLING_RECONCILIATION_REQUIRED$"):
        submit(store, actor(), key="must-not-create-job-or-charge", ticker="REAL.L")

    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(jobs)) == 0
        assert connection.scalar(select(func.count()).select_from(db.usage_records)) == 0
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.product_events)
                .where(db.product_events.c.event == "research_requested")
            )
            == 0
        )
        assert connection.scalar(select(db.billing_events.c.state)) == billing_state
    assert product.summary(actor())["used"] == 0


@pytest.mark.parametrize("billing_state", ["PENDING", "FAILED"])
def test_other_customers_unresolved_billing_event_does_not_block_paid_workspace(
    store, billing_state
):
    product = paid_customer(store)
    other_actor = actor("other-billing-customer")
    product.summary(other_actor)
    pending_event(store)
    with store.engine.begin() as connection:
        connection.execute(
            db.subscriptions.update()
            .where(db.subscriptions.c.workspace_id == other_actor.workspace_id)
            .values(customer_id="cus_other", subscription_id="sub_other", plan="PRO")
        )
        connection.execute(
            db.billing_events.update()
            .where(db.billing_events.c.id == "evt_identity")
            .values(customer_id="cus_other", subscription_id="sub_other", state=billing_state)
        )

    job = submit(store, actor(), key="unrelated-billing-cannot-block", ticker="REAL.L")

    assert job["ticker"] == "REAL.L"
    assert product.summary(actor())["used"] == 1
    assert product.summary(other_actor)["used"] == 0
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(jobs)) == 1
        usage = connection.execute(select(db.usage_records)).mappings().one()
        assert usage["workspace_id"] == actor().workspace_id
        assert usage["job_id"] == job["id"]
        assert usage["plan"] == "PRO"
