"""Offboarding transactions against SQLite and optional isolated PostgreSQL.

Provider responses are fixtures here, not evidence of live Stripe qualification.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import DatabaseError
from test_commercial_accounts import PASSWORD, configuration, customer, email_token, headers
from test_commercial_product import billing_settings
from test_commercial_product import store as store

from money.accounts import models as db
from money.accounts.offboarding import (
    RetentionPolicy,
    backfill_email_recipients,
    close_billing_once,
    fulfil_request,
    review_billing_closure,
    review_request,
)
from money.accounts.security import token_digest
from money.accounts.service import AccountError, AccountService
from money.api.app import create_app
from money.product import models as product_db
from money.product.billing import BillingUnavailable
from money.product.service import EntitlementDenied, ProductService
from money.schemas.contracts import ResearchMandate
from money.storage import models as research_db
from money.storage.store import now_utc


def policy(days=0, **overrides):
    return RetentionPolicy(
        money_retention_approved=True,
        money_retention_policy_version="test-policy-1",
        money_retention_approval_reference="test-review-123",
        money_pii_retention_days=days,
        **overrides,
    )


class Gateway:
    def __init__(self, workspace=None, *, lost_response=False, status="active", mismatch=False):
        self.settings = billing_settings()
        self.workspace = workspace
        self.lost_response = lost_response
        self.status = status
        self.mismatch = mismatch
        self.calls = []
        self.cancelled = False

    def request(self, method, path, data=None, key=None):
        self.calls.append((method, path, data, key))
        if method == "POST":
            assert data == {"cancel_at_period_end": "true"}
            if self.lost_response:
                self.lost_response = False
                raise BillingUnavailable("BILLING_PROVIDER_UNAVAILABLE")
            self.cancelled = True
        return {
            "id": "sub_fixture",
            "customer": "cus_wrong" if self.mismatch else "cus_fixture",
            "metadata": {"workspace_id": self.workspace},
            "livemode": False,
            "status": self.status,
            "cancel_at_period_end": self.cancelled,
        }


def setup(store, email="offboarding@example.test"):
    service = AccountService(store, configuration(store))
    account = customer(service, email)
    user = service.authenticated_user(account["session_token"])
    return service, account, user


def join(store, account, user_id, role="MEMBER"):
    with store.transaction() as connection:
        connection.execute(
            db.members.insert().values(
                workspace_id=account["workspace"]["id"],
                user_id=user_id,
                role=role,
                created_at=now_utc(),
            )
        )


def paid(store, workspace):
    product = ProductService(store)
    with store.transaction() as connection:
        product.subscription(connection, workspace)
        connection.execute(
            product_db.subscriptions.update()
            .where(product_db.subscriptions.c.workspace_id == workspace)
            .values(plan="PRO", customer_id="cus_fixture", subscription_id="sub_fixture")
        )


def make_due(store):
    with store.transaction() as connection:
        connection.execute(
            db.workspace_closures.update().values(available_at=now_utc() - timedelta(seconds=1))
        )


def review(store, user, retention=None):
    review_request(
        store,
        user["id"],
        retention or policy(),
        "operator-test-123",
        billing_resolved=True,
        retained_records_reviewed=True,
    )


def test_last_owner_cannot_strand_members_and_failed_request_keeps_access(store):
    service, account, user = setup(store)
    other = customer(service, "member@example.test")
    join(store, account, other["user"]["id"])
    with pytest.raises(AccountError, match="OWNERSHIP_TRANSFER_REQUIRED"):
        service.request_deletion(user, PASSWORD)
    assert service.authenticated_user(account["session_token"])["id"] == user["id"]
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(db.deletion_requests)) == 0
        assert connection.scalar(select(func.count()).select_from(db.workspace_closures)) == 0


def test_password_confirmed_transfer_allows_successor_and_preserves_their_workspace(store):
    service, account, user = setup(store)
    successor = customer(service, "successor@example.test")
    join(store, account, successor["user"]["id"])
    actor = service.principal(account["session_token"], account["workspace"]["id"])
    with pytest.raises(AccountError, match="PASSWORD_INVALID"):
        service.transfer_ownership(actor, "not the correct password", successor["user"]["id"])
    service.transfer_ownership(actor, PASSWORD, successor["user"]["id"])
    service.request_deletion(user, PASSWORD)
    assert service.principal(successor["session_token"], account["workspace"]["id"]).role == "OWNER"
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(db.workspace_closures)) == 0
    review(store, user)
    assert fulfil_request(store, user["id"], policy())
    assert service.principal(successor["session_token"], account["workspace"]["id"]).role == "OWNER"


def test_ownership_api_cannot_promote_foreign_customer_or_non_owner(store):
    service, account, _ = setup(store)
    foreign = customer(service, "foreign@example.test")
    with TestClient(create_app(service.settings, store)) as client:
        response = client.post(
            "/v1/workspaces/transfer-ownership",
            headers=headers(account),
            json={"user_id": foreign["user"]["id"], "password": PASSWORD},
        )
        assert response.status_code == 409 and response.json()["code"] == "SUCCESSOR_INVALID"
        join(store, account, foreign["user"]["id"])
        foreign_headers = {**headers(foreign), "X-Money-Workspace": account["workspace"]["id"]}
        response = client.post(
            "/v1/workspaces/transfer-ownership",
            headers=foreign_headers,
            json={"user_id": account["user"]["id"], "password": PASSWORD},
        )
        assert response.status_code == 403


def test_closure_revokes_tokens_invitations_and_queued_mail(store):
    service, account, user = setup(store)
    workspace = account["workspace"]["id"]
    with store.transaction() as connection:
        connection.execute(
            db.invitations.insert().values(
                token_hash=token_digest("fixture-invitation"),
                workspace_id=workspace,
                invited_by=user["id"],
                email="invitee@example.test",
                role="MEMBER",
                created_at=now_utc(),
                expires_at=now_utc() + timedelta(days=1),
            )
        )
    service.request_deletion(user, PASSWORD)
    with pytest.raises(AccountError, match="SESSION_EXPIRED"):
        service.authenticated_user(account["session_token"])
    with store.engine.connect() as connection:
        assert connection.scalar(select(db.invitations.c.used_at)) is not None
        assert (
            connection.scalar(
                select(func.count()).select_from(db.tokens).where(db.tokens.c.used_at.is_(None))
            )
            == 0
        )
        row = connection.execute(select(db.email_outbox)).mappings().one()
        assert row["state"] == "CANCELLED" and row["encrypted_payload"] == ""
        assert connection.scalar(select(db.deletion_requests.c.state)) == "REQUESTED"
    with pytest.raises(AccountError, match="SESSION_EXPIRED"):
        service.create_workspace(user, "Captured stale request")


def test_closing_workspace_cannot_checkout_or_enqueue_even_with_captured_principal(store):
    service, account, user = setup(store)
    actor = service.principal(account["session_token"], account["workspace"]["id"])
    product = ProductService(store, billing_settings())
    service.request_deletion(user, PASSWORD)
    with pytest.raises(AccountError, match="WORKSPACE_CLOSING"):
        product.checkout(actor, "PRO", "offboard-checkout")
    with pytest.raises(AccountError, match="WORKSPACE_CLOSING"):
        store.for_workspace(actor.workspace_id).create_job(
            "DEMO.L",
            ResearchMandate(),
            admission=lambda conn, job_id: product.reserve_research(conn, job_id, actor, "DEMO.L"),
        )
    with store.engine.connect() as connection:
        for table in (research_db.jobs, research_db.mandates, product_db.usage_records):
            assert connection.scalar(select(func.count()).select_from(table)) == 0


def test_pending_checkout_blocks_account_closure_without_revoking_session(store):
    service, account, user = setup(store)
    product = ProductService(store)
    with store.transaction() as connection:
        product.subscription(connection, account["workspace"]["id"])
        connection.execute(
            product_db.subscriptions.update().values(checkout_until=now_utc() + timedelta(hours=1))
        )
    with pytest.raises(AccountError, match="BILLING_CHECKOUT_PENDING"):
        service.request_deletion(user, PASSWORD)
    assert service.authenticated_user(account["session_token"])


def test_billing_lost_response_reuses_stable_intent_and_cancellation_is_not_settlement(store):
    service, account, user = setup(store)
    workspace = account["workspace"]["id"]
    paid(store, workspace)
    service.request_deletion(user, PASSWORD)
    gateway = Gateway(workspace, lost_response=True)
    assert close_billing_once(store, gateway)
    make_due(store)
    assert close_billing_once(store, gateway)
    posts = [call for call in gateway.calls if call[0] == "POST"]
    assert len(posts) == 2 and posts[0] == posts[1]
    with pytest.raises(AccountError, match="BILLING_CLOSURE_PENDING"):
        review(store, user)
    gateway.status = "canceled"
    make_due(store)
    assert close_billing_once(store, gateway)
    with pytest.raises(AccountError, match="RETENTION_REVIEW_REQUIRED"):
        review_request(
            store,
            user["id"],
            policy(),
            "operator-test",
            billing_resolved=False,
            retained_records_reviewed=True,
        )
    review(store, user)
    assert fulfil_request(store, user["id"], policy())


def test_stripe_identity_mismatch_never_cancels_wrong_subscription_and_retries_bounded(store):
    service, account, user = setup(store)
    workspace = account["workspace"]["id"]
    paid(store, workspace)
    service.request_deletion(user, PASSWORD)
    gateway = Gateway(workspace, mismatch=True)
    for _ in range(5):
        make_due(store)
        assert close_billing_once(store, gateway)
    assert all(call[0] == "GET" for call in gateway.calls)
    with store.engine.connect() as connection:
        row = connection.execute(select(db.workspace_closures)).mappings().one()
        assert row["state"] == "FAILED" and row["attempts"] == 5
    make_due(store)
    assert not close_billing_once(store, gateway)
    with pytest.raises(AccountError, match="BILLING_CLOSURE_PENDING"):
        review(store, user)


def test_retention_policy_unapproved_and_unsupported_review_cannot_erase(store):
    service, _, user = setup(store)
    service.request_deletion(user, PASSWORD)
    assert close_billing_once(store, Gateway())
    with pytest.raises(ValidationError):
        RetentionPolicy(money_retention_approved=True)
    with pytest.raises(AccountError, match="RETENTION_POLICY_UNAPPROVED"):
        review_request(
            store,
            user["id"],
            RetentionPolicy(),
            "operator-test",
            billing_resolved=True,
            retained_records_reviewed=True,
        )
    with pytest.raises(AccountError, match="RETENTION_REVIEW_REQUIRED"):
        fulfil_request(store, user["id"], policy())
    review(store, user, policy(30))
    with pytest.raises(AccountError, match="RETENTION_NOT_ELAPSED"):
        fulfil_request(store, user["id"], policy(30))
    with pytest.raises(AccountError, match="RETENTION_REVIEW_REQUIRED"):
        fulfil_request(store, user["id"], policy(0))


def test_fulfilment_preserves_reports_audits_and_tenant_owner_references(store):
    service, account, user = setup(store)
    workspace = account["workspace"]["id"]
    job = store.for_workspace(workspace).create_job("DEMO.L", ResearchMandate())
    report = {"fixture": "immutable report must survive identity erasure"}
    with store.transaction() as connection:
        connection.execute(
            research_db.firm_reports.insert().values(
                id=str(uuid4()),
                job_id=job["id"],
                firm="qlib",
                payload=report,
                content_hash="a" * 64,
                created_at=now_utc(),
            )
        )
    service.request_deletion(user, PASSWORD)
    assert close_billing_once(store, Gateway())
    review(store, user)
    with pytest.raises(AccountError, match="RESEARCH_STILL_ACTIVE"):
        fulfil_request(store, user["id"], policy())
    claim = store.claim_job("test-offboarding-worker")
    store.for_claim(claim).fail_job(job["id"], "TEST_COMPLETE", "Fixture terminal state")
    assert fulfil_request(store, user["id"], policy())
    assert not fulfil_request(store, user["id"], policy())
    with store.engine.connect() as connection:
        erased = (
            connection.execute(select(db.users).where(db.users.c.id == user["id"])).mappings().one()
        )
        assert erased["email"] == f"erased+{user['id']}@deleted.invalid"
        assert erased["password_hash"] == "disabled"
        assert connection.scalar(select(research_db.firm_reports.c.payload)) == report
        assert connection.scalar(select(db.organisations.c.created_by)) == user["id"]
        assert connection.scalar(select(research_db.jobs.c.workspace_id)) == workspace
        assert connection.scalar(select(func.count()).select_from(db.audit)) > 0
        assert connection.scalar(select(func.count()).select_from(db.sessions)) == 0
    with pytest.raises(DatabaseError):
        with store.transaction() as connection:
            connection.execute(research_db.firm_reports.delete())


def test_active_email_and_legacy_recipient_index_block_erasure(store):
    service, _, user = setup(store)
    with store.transaction() as connection:
        connection.execute(
            db.email_outbox.update().values(
                state="SENDING", lease_until=now_utc() + timedelta(minutes=2), recipient_hash=None
            )
        )
    service.request_deletion(user, PASSWORD)
    close_billing_once(store, Gateway())
    review(store, user)
    with pytest.raises(AccountError, match="EMAIL_STILL_ACTIVE"):
        fulfil_request(store, user["id"], policy())
    with store.transaction() as connection:
        connection.execute(
            db.email_outbox.update().values(lease_until=now_utc() - timedelta(seconds=1))
        )
    with pytest.raises(AccountError, match="LEGACY_EMAIL_REVIEW_REQUIRED"):
        fulfil_request(store, user["id"], policy())
    assert backfill_email_recipients(store, service.cipher) == 1
    assert fulfil_request(store, user["id"], policy())


def test_billing_webhook_pending_after_review_blocks_erasure(store):
    service, account, user = setup(store)
    paid(store, account["workspace"]["id"])
    service.request_deletion(user, PASSWORD)
    close_billing_once(store, Gateway(account["workspace"]["id"], status="canceled"))
    review(store, user)
    with store.transaction() as connection:
        connection.execute(
            product_db.billing_events.insert().values(
                id="evt_late",
                kind="invoice.payment_failed",
                customer_id="cus_fixture",
                subscription_id="sub_fixture",
                content_hash="f" * 64,
                state="PENDING",
                attempts=0,
                available_at=now_utc(),
                created_at=now_utc(),
            )
        )
    with pytest.raises(AccountError, match="BILLING_RECONCILIATION_REQUIRED"):
        fulfil_request(store, user["id"], policy())


def test_concurrent_erasures_publish_one_completion_audit(store):
    service, _, user = setup(store)
    service.request_deletion(user, PASSWORD)
    close_billing_once(store, Gateway())
    review(store, user)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _: fulfil_request(store, user["id"], policy()), range(2))
        )
    assert sorted(results) == [False, True]
    with store.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.audit)
                .where(db.audit.c.event == "account_local_pii_erased")
            )
            == 1
        )


def test_stale_owner_and_removed_member_cannot_bill_or_admit_paid_work(store):
    service, account, _ = setup(store)
    successor = customer(service, "new-owner@example.test")
    join(store, account, successor["user"]["id"])
    actor = service.principal(account["session_token"], account["workspace"]["id"])
    product = ProductService(store, billing_settings())
    service.transfer_ownership(actor, PASSWORD, successor["user"]["id"])
    for plan, portal in (("PRO", False), ("FREE", True)):
        with pytest.raises((AccountError, EntitlementDenied), match="ROLE_FORBIDDEN"):
            product.checkout(actor, plan, "captured-owner-key", portal=portal)
    owner = service.principal(successor["session_token"], account["workspace"]["id"])
    removed = service.principal(account["session_token"], account["workspace"]["id"])
    service.change_member(owner, removed.user_id, None)
    with pytest.raises((AccountError, EntitlementDenied), match="ROLE_FORBIDDEN"):
        store.for_workspace(actor.workspace_id).create_job(
            "DEMO.L",
            ResearchMandate(),
            admission=lambda conn, job_id: product.reserve_research(
                conn, job_id, removed, "DEMO.L"
            ),
        )
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(research_db.jobs)) == 0
        assert connection.scalar(select(func.count()).select_from(product_db.usage_records)) == 0


def test_erased_address_is_not_reintroduced_by_stale_notifications_or_invitations(store):
    service, _, user = setup(store)
    sender = customer(service, "invitation-sender@example.test")
    service.request_deletion(user, PASSWORD)
    close_billing_once(store, Gateway())
    review(store, user)
    fulfil_request(store, user["id"], policy())
    with store.transaction() as connection:
        service._email(connection, user["id"], user["email"], "subscription", "Fixture", "Fixture")
    with pytest.raises(AccountError, match="INVITATION_UNAVAILABLE"):
        with store.transaction() as connection:
            service._email(
                connection, sender["user"]["id"], user["email"], "invitation", "Fixture", "Fixture"
            )
    actor = service.principal(sender["session_token"], sender["workspace"]["id"])
    with store.transaction() as connection:
        ProductService(store).subscription(connection, actor.workspace_id)
        connection.execute(
            product_db.subscriptions.update()
            .where(product_db.subscriptions.c.workspace_id == actor.workspace_id)
            .values(plan="TEAM")
        )
    with pytest.raises(AccountError, match="INVITATION_UNAVAILABLE"):
        service.invite(actor, user["email"], "MEMBER")
    with store.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.email_outbox)
                .where(db.email_outbox.c.recipient_hash == token_digest(user["email"]))
            )
            == 0
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.invitations)
                .where(db.invitations.c.email == user["email"])
            )
            == 0
        )


def test_postgres_email_enqueue_contending_with_closure_is_retryable_not_a_deadlock(store):
    if store.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock regression")
    service, _, user = setup(store)

    def notification():
        with store.transaction() as connection:
            service._email(
                connection, user["id"], user["email"], "subscription", "Fixture", "Fixture"
            )

    with store.engine.begin() as connection:
        connection.execute(
            select(db.users.c.id).where(db.users.c.id == user["id"]).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(notification)
            with pytest.raises(AccountError, match="ACCOUNT_BUSY"):
                future.result(timeout=3)


def test_concurrent_reset_and_deletion_follow_user_before_token_lock_order(store):
    service, account, user = setup(store)
    service.forgot_password(user["email"])
    token = email_token(service, user["email"], "reset")

    def reset():
        try:
            service.reset_password(token, "new long passphrase for test")
            return "reset"
        except AccountError as error:
            return error.code

    def delete():
        try:
            service.request_deletion(user, PASSWORD)
            return "deleted"
        except AccountError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [executor.submit(reset), executor.submit(delete)]
        observed = {future.result(timeout=10) for future in results}
    assert observed in ({"reset", "SESSION_EXPIRED"}, {"deleted", "TOKEN_INVALID"})
    with pytest.raises(AccountError, match="SESSION_EXPIRED"):
        service.authenticated_user(account["session_token"])


def test_abandoned_checkout_requires_explicit_provider_review_not_silent_assumption(store):
    service, account, user = setup(store)
    workspace = account["workspace"]["id"]
    with store.transaction() as connection:
        ProductService(store).subscription(connection, workspace)
        connection.execute(product_db.subscriptions.update().values(customer_id="cus_fixture"))
    service.request_deletion(user, PASSWORD)
    gateway = Gateway()
    close_billing_once(store, gateway)
    assert gateway.calls == []
    with pytest.raises(AccountError, match="BILLING_REVIEW_REQUIRED"):
        review_billing_closure(store, workspace, "operator-test")
    review_billing_closure(
        store, workspace, "operator-test", provider_review_reference="stripe-case-fixture-321"
    )
    review(store, user)
    assert fulfil_request(store, user["id"], policy())


def test_failed_billing_closure_retry_is_audited_and_preserves_idempotency_key(store):
    service, account, user = setup(store)
    workspace = account["workspace"]["id"]
    paid(store, workspace)
    service.request_deletion(user, PASSWORD)
    gateway = Gateway(workspace, mismatch=True)
    for _ in range(5):
        make_due(store)
        close_billing_once(store, gateway)
    with store.engine.connect() as connection:
        key = connection.scalar(select(db.workspace_closures.c.cancellation_key))
    with pytest.raises(AccountError, match="BILLING_PROVIDER_CLOSURE_REQUIRED"):
        review_billing_closure(
            store, workspace, "operator-test", provider_review_reference="stripe-case-fixture-321"
        )
    review_billing_closure(store, workspace, "operator-test", retry=True)
    gateway.mismatch = False
    close_billing_once(store, gateway)
    with store.engine.connect() as connection:
        closure = connection.execute(select(db.workspace_closures)).mappings().one()
        assert closure["cancellation_key"] == key and closure["state"] == "SCHEDULED"
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.audit)
                .where(db.audit.c.event == "billing_closure_retry_approved")
            )
            == 1
        )


def test_commercial_worker_processes_closure_but_healthcheck_is_read_only(store, monkeypatch):
    from money.accounts import offboarding
    from money.product import worker

    service, _, user = setup(store)
    service.request_deletion(user, PASSWORD)
    monkeypatch.setenv("DATABASE_URL", store.database_url)
    monkeypatch.setenv("RESEARCH_API_TOKEN", "offboarding-worker-service-test-32-characters")
    monkeypatch.setenv("MONEY_AUTH_MODE", "saas")
    monkeypatch.setenv(
        "MONEY_EMAIL_ENCRYPTION_KEY", service.settings.money_email_encryption_key.get_secret_value()
    )
    monkeypatch.setattr("sys.argv", ["commercial-worker", "--healthcheck"])
    original = offboarding.close_billing_once
    monkeypatch.setattr(
        offboarding,
        "close_billing_once",
        lambda *args, **kwargs: pytest.fail("healthcheck mutated billing closure"),
    )
    store.heartbeat("fixture-commercial-worker", "commercial-services")
    with pytest.raises(SystemExit) as healthy:
        worker.main()
    assert healthy.value.code == 0
    with store.engine.connect() as connection:
        assert connection.scalar(select(db.workspace_closures.c.state)) == "PENDING"
    monkeypatch.setattr(offboarding, "close_billing_once", original)
    monkeypatch.setattr("sys.argv", ["commercial-worker", "--once"])
    worker.main()
    with store.engine.connect() as connection:
        assert connection.scalar(select(db.workspace_closures.c.state)) == "CLOSED"
        assert connection.scalar(select(db.deletion_requests.c.state)) == "REQUESTED"
