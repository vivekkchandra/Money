"""Real transactions; provider transports alone are fixtures, never live billing claims."""

import hashlib
import hmac
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DatabaseError

from money.accounts import models as accounts
from money.product import models as db
from money.product.billing import BillingUnavailable, StripeGateway, authenticate_event
from money.product.service import EntitlementDenied, ProductService
from money.product.settings import ProductSettings
from money.product.worker import reconcile_once
from money.research.budgets import BudgetLimits, TokenBudgetManager
from money.schemas.contracts import ResearchMandate, utc_now
from money.storage import ResearchStore
from money.storage.models import jobs


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path, monkeypatch):
    monkeypatch.setenv("MONEY_ENV", "test")
    schema = None
    admin = None
    if request.param == "postgres":
        raw = os.environ.get("TEST_DATABASE_URL")
        if not raw:
            pytest.skip("TEST_DATABASE_URL not configured")
        raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
        admin = create_engine(raw)
        schema = "money_commercial_" + uuid4().hex
        with admin.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        url = (
            make_url(raw)
            .update_query_dict({"options": f"-csearch_path={schema}"})
            .render_as_string(hide_password=False)
        )
    else:
        url = f"sqlite:///{tmp_path / 'product.db'}"
    repository = ResearchStore(url, allow_sqlite=request.param == "sqlite")
    try:
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        yield repository
    finally:
        repository.engine.dispose()
        if admin is not None and schema:
            with admin.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            admin.dispose()


def actor(workspace="customer-a", role="OWNER"):
    return SimpleNamespace(user_id="test-user", workspace_id=workspace, role=role)


def ensure_actor(store, who):
    """Persist test authority; commercial service never trusts a fixture's role alone."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    now = utc_now()
    with store.transaction() as connection:
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        for table, values in (
            (
                accounts.users,
                dict(
                    id=who.user_id,
                    email=f"{who.user_id}@example.test",
                    display_name="Fixture",
                    password_hash="disabled",
                    email_verified_at=now,
                    created_at=now,
                ),
            ),
            (
                accounts.organisations,
                dict(
                    id=who.workspace_id,
                    name="Fixture workspace",
                    created_by=who.user_id,
                    created_at=now,
                ),
            ),
            (
                accounts.members,
                dict(
                    workspace_id=who.workspace_id,
                    user_id=who.user_id,
                    role=who.role,
                    created_at=now,
                ),
            ),
        ):
            connection.execute(insert(table).values(**values).on_conflict_do_nothing())


def submit(store, who, key=None, ticker="DEMO.L"):
    ensure_actor(store, who)
    product = ProductService(store)
    return store.for_workspace(who.workspace_id).create_job(
        ticker,
        ResearchMandate(),
        idempotency_key=key,
        admission=lambda connection, job_id: product.reserve_research(
            connection, job_id, who, ticker
        ),
    )


def billing_settings():
    return ProductSettings(
        money_billing_enabled=True,
        money_stripe_secret_key="sk_test_fixture",
        money_stripe_webhook_secret="whsec_fixture_only",
        money_stripe_prices={"PRO": "price_pro"},
    )


def signed_event(settings, event=None, timestamp=None):
    event = event or {"id": "evt_test", "livemode": False}
    timestamp = str(timestamp if timestamp is not None else int(time.time()))
    raw = json.dumps(event).encode()
    signature = hmac.new(
        settings.money_stripe_webhook_secret.get_secret_value().encode(),
        timestamp.encode() + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    return raw, f"t={timestamp},v1={signature}"


def test_quota_and_idempotency_are_atomic_across_replicas(store):
    who = actor()
    with ThreadPoolExecutor(max_workers=8) as executor:
        duplicates = list(executor.map(lambda _: submit(store, who, "same-key"), range(8)))
    assert len({row["id"] for row in duplicates}) == 1
    assert ProductService(store).summary(who)["used"] == 1
    submit(store, who, "second")
    submit(store, who, "third")
    with pytest.raises(EntitlementDenied, match="EXHAUSTED"):
        submit(store, who, "fourth")
    assert len(store.for_workspace(who.workspace_id).list_jobs()) == 3
    assert ProductService(store).summary(actor("customer-b"))["used"] == 0
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(db.usage_records)) == 3


@pytest.mark.parametrize(
    "role,ticker,code", [("VIEWER", "DEMO.L", "ROLE_FORBIDDEN"), ("OWNER", "REAL.L", "PLAN_LIVE")]
)
def test_quota_rollback_no_orphan_job(store, role, ticker, code):
    with pytest.raises(EntitlementDenied, match=code):
        submit(store, actor(role=role), ticker=ticker)
    assert store.list_jobs() == []


def test_history_search_is_bounded_and_tenant_scoped(store):
    first = submit(store, actor(), "a")
    submit(store, actor(), "b")
    other = submit(store, actor("other"), "a")
    product = ProductService(store)
    page = product.history(actor(), limit=1)
    assert len(page["jobs"]) == 1 and page["next_offset"] == 1
    assert product.history(actor(), query=other["id"])["jobs"] == []
    assert product.history(actor(), query=first["id"])["jobs"][0]["id"] == first["id"]
    assert product.history(actor(), query="%_'")["jobs"] == []
    assert "lease_token" not in page["jobs"][0]


def test_watchlist_tenant_and_viewer_boundaries(store):
    product = ProductService(store)
    product.watch(actor(), "DEMO.L")
    product.watch(actor(), "DEMO.L")
    assert len(product.list_watch(actor())) == 1
    assert product.list_watch(actor("other")) == []
    with pytest.raises(EntitlementDenied):
        product.watch(actor(role="VIEWER"), "DEMO.L", remove=True)
    product.watch(actor("other"), "DEMO.L", remove=True)
    assert len(product.list_watch(actor())) == 1


def test_account_ceiling_is_atomic_across_workspace_admissions(store):
    product = ProductService(store, ProductSettings(money_user_jobs_per_month=2))

    def attempt(workspace):
        who = actor(workspace)
        ensure_actor(store, who)
        try:
            store.for_workspace(workspace).create_job(
                "DEMO.L",
                ResearchMandate(),
                idempotency_key="unique-per-workspace",
                admission=lambda connection, job_id: product.reserve_research(
                    connection, job_id, who, "DEMO.L"
                ),
            )
            return "admitted"
        except EntitlementDenied as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(attempt, [f"workspace-{index}" for index in range(5)]))
    assert results.count("admitted") == 2
    assert results.count("ACCOUNT_RESEARCH_ALLOWANCE_EXHAUSTED") == 3
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(db.usage_records)) == 2
        assert connection.scalar(select(func.count()).select_from(jobs)) == 2
    # Another account has an independent operational ceiling, even on this host.
    peer = SimpleNamespace(user_id="another-user", workspace_id="peer", role="OWNER")
    submit(store, peer)
    assert product.summary(peer)["account_monthly_used"] == 1


def test_commercial_healthcheck_is_scoped_and_never_reconciles(store, monkeypatch):
    from money.product import worker

    monkeypatch.setenv("DATABASE_URL", store.engine.url.render_as_string(hide_password=False))
    monkeypatch.setenv("RESEARCH_API_TOKEN", "healthcheck-test-service-token-at-least-32-chars")
    monkeypatch.setattr("sys.argv", ["commercial-worker", "--healthcheck"])
    monkeypatch.setattr(
        worker, "reconcile_once", lambda *a, **k: pytest.fail("health mutated billing")
    )
    store.heartbeat("email-worker", "transactional-email")
    with pytest.raises(SystemExit) as unavailable:
        worker.main()
    assert unavailable.value.code == 1
    store.heartbeat("commercial-worker", "commercial-services")
    with pytest.raises(SystemExit) as healthy:
        worker.main()
    assert healthy.value.code == 0
    store.heartbeat("commercial-worker", "commercial-services", healthy=False)
    with pytest.raises(SystemExit) as stopped:
        worker.main()
    assert stopped.value.code == 1


def test_history_date_window_sort_pagination_and_isolation(store):
    first = submit(store, actor(), "first-date")
    second = submit(store, actor(), "second-date")
    third = submit(store, actor(), "third-date")
    other = submit(store, actor("other"), "other-date")
    with store.transaction() as connection:
        for job, timestamp in (
            (first, datetime(2026, 9, 14, tzinfo=UTC)),
            (second, datetime(2026, 9, 15, 23, 59, 59, 999999, tzinfo=UTC)),
            (third, datetime(2026, 9, 16, tzinfo=UTC)),
            (other, datetime(2026, 9, 15, tzinfo=UTC)),
        ):
            connection.execute(
                jobs.update().where(jobs.c.id == job["id"]).values(created_at=timestamp)
            )
    product = ProductService(store)
    window = {"created_from": date(2026, 9, 14), "created_to": date(2026, 9, 15)}
    newest = product.history(actor(), **window)
    assert [job["id"] for job in newest["jobs"]] == [second["id"], first["id"]]
    oldest = product.history(actor(), **window, sort="oldest", limit=1)
    assert [job["id"] for job in oldest["jobs"]] == [first["id"]]
    assert oldest["next_offset"] == 1
    last = product.history(actor(), **window, sort="oldest", limit=1, offset=1)
    assert [job["id"] for job in last["jobs"]] == [second["id"]]
    assert last["next_offset"] is None
    assert product.history(actor(), query=other["id"], **window)["jobs"] == []
    assert product.history(actor(), created_from=date(2026, 9, 17))["jobs"] == []
    for parameters in (
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"offset": 100001},
        {"sort": "arbitrary-sql"},
        {"created_from": date(2026, 9, 16), "created_to": date(2026, 9, 15)},
    ):
        with pytest.raises(ValueError, match="INVALID_HISTORY_RANGE"):
            product.history(actor(), **parameters)


def test_usage_and_product_audit_are_immutable(store):
    submit(store, actor())
    for table in (db.usage_records, db.product_events):
        with pytest.raises(DatabaseError):
            with store.engine.begin() as connection:
                connection.execute(table.delete())


def test_webhook_signatures_expiry_mode_and_tampering():
    settings = billing_settings()
    raw, signature = signed_event(settings)
    assert authenticate_event(raw, signature, settings)["id"] == "evt_test"
    with pytest.raises(ValueError):
        authenticate_event(raw + b" ", signature, settings)
    raw, signature = signed_event(settings, timestamp=int(time.time()) - 301)
    with pytest.raises(ValueError, match="EXPIRED"):
        authenticate_event(raw, signature, settings)
    raw, signature = signed_event(settings, {"id": "evt_x", "livemode": True})
    with pytest.raises(ValueError, match="MODE"):
        authenticate_event(raw, signature, settings)
    with pytest.raises(ValueError):
        authenticate_event(raw, "t=1,t=2,v1=x", settings)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://checkout.stripe.com.evil/a",
        "https://evil@checkout.stripe.com/a",
        "http://checkout.stripe.com/a",
        "https://checkout.stripe.com:444/a",
    ],
)
def test_hosted_billing_redirect_allowlist(url):
    with pytest.raises(BillingUnavailable):
        StripeGateway.hosted_url({"url": url}, "checkout.stripe.com")


def test_billing_disabled_and_owner_only(store):
    with pytest.raises(BillingUnavailable):
        ProductService(store).checkout(actor(), "PRO", "idempotencykeyhere")
    with pytest.raises(EntitlementDenied):
        ProductService(store, billing_settings()).checkout(actor(role="ADMIN"), "PRO", "key")


def seed_subscription(store, status="active"):
    product = ProductService(store, billing_settings())
    product.summary(actor())
    with store.engine.begin() as connection:
        connection.execute(
            db.subscriptions.update().values(customer_id="cus_one", plan="PRO", status=status)
        )
    return product


def test_payment_failure_blocks_jobs_without_destroying_history(store):
    product = seed_subscription(store)
    submit(store, actor(), ticker="REAL.L")
    with store.engine.begin() as connection:
        connection.execute(db.subscriptions.update().values(status="past_due"))
    with pytest.raises(EntitlementDenied, match="SUBSCRIPTION_INACTIVE"):
        submit(store, actor(), ticker="REAL.L")
    assert len(product.history(actor())["jobs"]) == 1


def test_saas_token_limits_override_larger_research_defaults(store):
    seed_subscription(store)
    job = submit(store, actor(), ticker="REAL.L")
    budget = TokenBudgetManager(store, BudgetLimits())
    with pytest.raises(ValueError, match="TOKEN_BUDGET_EXCEEDED"):
        budget.reserve(
            reservation_id="too-large",
            job_id=job["id"],
            stage="FIRST_PASS_RESEARCH",
            agent="firm",
            provider="fixture",
            model="fixture",
            maximum_tokens=10001,
            prompt_version="v1",
        )
    demo = submit(store, actor())
    with pytest.raises(ValueError, match="TOKEN_BUDGET_EXCEEDED"):
        budget.reserve(
            reservation_id="demo-paid",
            job_id=demo["id"],
            stage="FIRST_PASS_RESEARCH",
            agent="firm",
            provider="fixture",
            model="fixture",
            maximum_tokens=1,
            prompt_version="v1",
        )


def test_reconciliation_uses_current_provider_state_and_is_idempotent(store):
    product = seed_subscription(store)
    now = utc_now()
    with store.engine.begin() as connection:
        connection.execute(
            db.billing_events.insert().values(
                id="evt_old",
                kind="customer.subscription.updated",
                subscription_id="sub_one",
                customer_id="cus_one",
                content_hash="a" * 64,
                state="PENDING",
                attempts=0,
                available_at=now,
                created_at=now,
            )
        )

    class Gateway:
        def request(self, method, path):
            assert path == "/subscriptions/sub_one"
            return {
                "id": "sub_one",
                "customer": "cus_one",
                "livemode": False,
                "metadata": {"workspace_id": "customer-a"},
                "status": "past_due",
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

    assert reconcile_once(product, Gateway())
    assert not reconcile_once(product, Gateway())
    assert product.summary(actor())["status"] == "past_due"


def test_failed_billing_events_bound_retries(store):
    product = seed_subscription(store)
    with store.engine.begin() as connection:
        connection.execute(
            db.billing_events.insert().values(
                id="evt_failed",
                kind="customer.subscription.updated",
                subscription_id="sub_one",
                customer_id="cus_one",
                content_hash="a" * 64,
                state="PENDING",
                attempts=4,
                available_at=utc_now(),
                created_at=utc_now(),
            )
        )

    class Gateway:
        def request(self, method, path):
            raise BillingUnavailable("provider-secret-must-not-escape")

    assert reconcile_once(product, Gateway())
    with store.engine.connect() as connection:
        assert connection.scalar(select(db.billing_events.c.state)) == "FAILED"


def test_stale_subscription_cannot_admit_paid_jobs(store):
    seed_subscription(store)
    with store.engine.begin() as connection:
        connection.execute(
            db.subscriptions.update().values(period_end=utc_now() - timedelta(seconds=1))
        )
    with pytest.raises(EntitlementDenied, match="INACTIVE"):
        submit(store, actor(), ticker="REAL.L")
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(jobs)) == 0
