"""Real persisted customer identity; synthetic evidence remains explicitly synthetic."""

import json
import os
import subprocess
import sys
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import DatabaseError

from money.accounts import models as db
from money.accounts.email_worker import EmailSettings, EmailWorker
from money.accounts.security import password_hash, password_valid, token_digest
from money.accounts.service import AccountError, AccountService
from money.api.app import create_app
from money.api.settings import Settings
from money.schemas.contracts import ResearchMandate
from money.storage import ResearchStore
from money.storage.store import now_utc

PASSWORD = "test account passphrase only"
SERVICE_TOKEN = "test-service-token-for-account-integration-32"


def configuration(store: ResearchStore, **overrides: Any) -> Settings:
    return Settings.model_validate(
        {
            "money_env": "test",
            "money_auth_mode": "saas",
            "money_research_mode": "unconfigured",
            "money_enable_synthetic_demo": True,
            "database_url": store.database_url,
            "research_api_token": SERVICE_TOKEN,
            "money_email_encryption_key": Fernet.generate_key().decode(),
            **overrides,
        }
    )


def email_token(service: AccountService, email: str, kind: str = "verify") -> str:
    # Test-only delivery sink decrypts the same durable payload that SMTP receives.
    with service.store.engine.connect() as connection:
        encrypted = connection.scalar(
            select(db.email_outbox.c.encrypted_payload)
            .join(db.users)
            .where(db.users.c.email == email, db.email_outbox.c.kind == kind)
            .order_by(db.email_outbox.c.created_at.desc())
            .limit(1)
        )
    message = json.loads(service.cipher.decrypt(encrypted.encode()))
    return message["body"].split("#token=", 1)[1].split()[0]


def customer(service: AccountService, email: str) -> dict[str, Any]:
    service.signup(email, PASSWORD, "Test Customer")
    service.verify_email(email_token(service, email))
    result = service.login(email, PASSWORD)
    workspace = service.create_workspace(
        service.authenticated_user(result["session_token"]), "Research"
    )
    return {**result, "workspace": workspace}


def headers(customer: dict[str, Any]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Money-Session": customer["session_token"],
        "X-Money-Workspace": customer["workspace"]["id"],
    }


def test_signup_verification_login_workspace_and_separate_worker_survive_restart(
    store: ResearchStore,
):
    config = configuration(store)
    auth = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    with TestClient(create_app(config, store)) as client:
        response = client.post(
            "/v1/account/signup",
            headers=auth,
            json={
                "email": "journey@example.test",
                "password": PASSWORD,
                "display_name": "Customer",
            },
        )
        assert response.status_code == 202 and "token" not in response.text
        service = client.app.state.accounts
        token = email_token(service, "journey@example.test")
        verified = client.post("/v1/account/verify-email", headers=auth, json={"token": token})
        assert verified.json() == {"verified": True}
        logged = client.post(
            "/v1/account/login",
            headers=auth,
            json={"email": "journey@example.test", "password": PASSWORD},
        ).json()
        session_headers = {**auth, "X-Money-Session": logged["session_token"]}
        workspace = client.post(
            "/v1/workspaces", headers=session_headers, json={"name": "Customer Research"}
        ).json()["workspace"]
        session_headers["X-Money-Workspace"] = workspace["id"]
        queued = client.post(
            "/research/jobs",
            headers={**session_headers, "Idempotency-Key": "first-demo"},
            json={"ticker": "DEMO.L"},
        )
        assert queued.status_code == 202, queued.text
        job_id = queued.json()["id"]
        duplicate = client.post(
            "/research/jobs",
            headers={**session_headers, "Idempotency-Key": "first-demo"},
            json={"ticker": "DEMO.L"},
        )
        assert duplicate.json()["id"] == job_id
    process = subprocess.run(
        [sys.executable, "-m", "money.worker", "--once"],
        env={
            **os.environ,
            "MONEY_ENV": "test",
            "MONEY_AUTH_MODE": "saas",
            "MONEY_RESEARCH_MODE": "unconfigured",
            "MONEY_ENABLE_SYNTHETIC_DEMO": "true",
            "MONEY_EMAIL_ENCRYPTION_KEY": config.money_email_encryption_key.get_secret_value(),
            "DATABASE_URL": store.database_url,
            "RESEARCH_API_TOKEN": SERVICE_TOKEN,
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    with TestClient(create_app(config, store)) as client:
        result = client.get(f"/research/jobs/{job_id}", headers=session_headers)
        assert result.status_code == 200
        assert result.json()["status"] == "COMPLETE", result.text
        assert result.json()["packet"]["runtime"] == "demo"
        assert result.json()["packet"]["signal"] is None
        assert client.get(f"/research/jobs/{job_id}/reports", headers=session_headers).json()[
            "locked"
        ]
        assert client.get(f"/research/jobs/{job_id}/evidence", headers=session_headers).json()[
            "evidence"
        ]
        assert (
            client.post("/v1/account/logout", headers=session_headers, json={}).status_code == 200
        )
        assert client.get(f"/research/jobs/{job_id}", headers=session_headers).status_code == 401
        again = client.post(
            "/v1/account/login",
            headers=auth,
            json={"email": "journey@example.test", "password": PASSWORD},
        ).json()
        session_headers["X-Money-Session"] = again["session_token"]
        assert (
            client.get(f"/research/jobs/{job_id}", headers=session_headers).json()["id"] == job_id
        )


def test_cross_tenant_uuid_and_forged_workspace_headers_fail_closed(store: ResearchStore):
    config = configuration(store)
    with TestClient(create_app(config, store)) as client:
        service = client.app.state.accounts
        alice, bob = customer(service, "alice@example.test"), customer(service, "bob@example.test")
        job = store.for_workspace(alice["workspace"]["id"]).create_job("DEMO.L", ResearchMandate())
        for suffix in ("", "/reports", "/evidence"):
            assert (
                client.get(f"/research/jobs/{job['id']}{suffix}", headers=headers(bob)).status_code
                == 404
            )
        forged = {**headers(bob), "X-Money-Workspace": alice["workspace"]["id"]}
        assert client.get("/research/jobs", headers=forged).status_code == 403
        assert (
            client.post("/research/jobs", headers=forged, json={"ticker": "DEMO.L"}).status_code
            == 403
        )
        assert (
            client.get(
                "/research/jobs", headers={"Authorization": f"Bearer {SERVICE_TOKEN}"}
            ).status_code
            == 401
        )


def test_viewer_cannot_enqueue_or_administer_workspace(store: ResearchStore):
    config = configuration(store)
    with TestClient(create_app(config, store)) as client:
        account = customer(client.app.state.accounts, "viewer@example.test")
        with store.transaction() as connection:
            connection.execute(
                db.members.update()
                .where(db.members.c.user_id == account["user"]["id"])
                .values(role="VIEWER")
            )
        assert client.get("/research/jobs", headers=headers(account)).status_code == 200
        assert (
            client.post(
                "/research/jobs", headers=headers(account), json={"ticker": "DEMO.L"}
            ).status_code
            == 403
        )
        assert client.get("/research/system", headers=headers(account)).status_code == 403


def test_verification_tokens_hashed_encrypted_expiring_one_use(store: ResearchStore):
    service = AccountService(store, configuration(store))
    service.signup("verify@example.test", PASSWORD, "Customer")
    raw = email_token(service, "verify@example.test")
    with store.engine.connect() as connection:
        persisted = connection.execute(select(db.tokens)).mappings().one()
        email = connection.execute(select(db.email_outbox)).mappings().one()
        assert persisted["token_hash"] == token_digest(raw)
        assert raw not in str(persisted) and raw not in str(email)
    with pytest.raises(AccountError, match="EMAIL_VERIFICATION_REQUIRED"):
        service.login("verify@example.test", PASSWORD)
    service.verify_email(raw)
    with pytest.raises(AccountError, match="TOKEN_INVALID"):
        service.verify_email(raw)


def test_expired_action_token_and_session_are_rejected(store: ResearchStore):
    service = AccountService(store, configuration(store))
    service.signup("expired@example.test", PASSWORD, "Customer")
    raw = email_token(service, "expired@example.test")
    with store.transaction() as connection:
        connection.execute(db.tokens.update().values(expires_at=now_utc() - timedelta(seconds=1)))
    with pytest.raises(AccountError, match="TOKEN_INVALID"):
        service.verify_email(raw)
    account = customer(service, "session@example.test")
    with store.transaction() as connection:
        connection.execute(db.sessions.update().values(expires_at=now_utc() - timedelta(seconds=1)))
    with pytest.raises(AccountError, match="SESSION_EXPIRED"):
        service.authenticated_user(account["session_token"])


def test_password_reset_revokes_existing_sessions_and_does_not_log_in(store: ResearchStore):
    service = AccountService(store, configuration(store))
    account = customer(service, "reset@example.test")
    service.forgot_password("reset@example.test")
    raw = email_token(service, "reset@example.test", "reset")
    service.reset_password(raw, "a different safe test passphrase")
    with pytest.raises(AccountError, match="SESSION_EXPIRED"):
        service.authenticated_user(account["session_token"])
    with pytest.raises(AccountError, match="TOKEN_INVALID"):
        service.reset_password(raw, PASSWORD)
    with pytest.raises(AccountError, match="LOGIN_INVALID"):
        service.login("reset@example.test", PASSWORD)
    assert service.login("reset@example.test", "a different safe test passphrase")["session_token"]


def test_session_rotation_and_logout_are_durable(store: ResearchStore):
    service = AccountService(store, configuration(store))
    account = customer(service, "rotation@example.test")
    rotated = service.rotate(account["session_token"])
    assert rotated["session_token"] != account["session_token"]
    with pytest.raises(AccountError, match="SESSION_EXPIRED"):
        service.authenticated_user(account["session_token"])
    service.logout(rotated["session_token"])
    with pytest.raises(AccountError, match="SESSION_EXPIRED"):
        service.authenticated_user(rotated["session_token"])


def test_account_deletion_revokes_access_preserves_audit_and_research(store: ResearchStore):
    service = AccountService(store, configuration(store))
    account = customer(service, "deletion@example.test")
    job = store.for_workspace(account["workspace"]["id"]).create_job("DEMO.L", ResearchMandate())
    user = service.authenticated_user(account["session_token"])
    exported = service.export(user)
    assert "password_hash" not in json.dumps(exported, default=str)
    service.request_deletion(user, PASSWORD)
    with pytest.raises(AccountError, match="SESSION_EXPIRED"):
        service.authenticated_user(account["session_token"])
    assert store.get_job(job["id"]) is not None
    with pytest.raises(DatabaseError):
        with store.transaction() as connection:
            connection.execute(db.audit.delete())


def test_rate_limit_and_strict_service_authenticated_contract(store: ResearchStore):
    config = configuration(store)
    with TestClient(create_app(config, store)) as client:
        body = {"email": "unknown@example.test", "password": PASSWORD}
        assert client.post("/v1/account/login", json=body).status_code == 401
        auth = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
        assert (
            client.post(
                "/v1/account/login", headers=auth, json={**body, "role": "OWNER"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/internal/auth/sessions/validate",
                headers=auth,
                json={"token_hash": "a" * 64, "credential_version": "b" * 64},
            ).status_code
            == 403
        )
        for _ in range(5):
            assert client.post("/v1/account/login", headers=auth, json=body).status_code == 401
        assert client.post("/v1/account/login", headers=auth, json=body).status_code == 429


def test_scrypt_hashes_are_salted_bounded_and_constant_time_compared():
    one, two = password_hash(PASSWORD), password_hash(PASSWORD)
    assert one != two and PASSWORD not in one
    assert password_valid(PASSWORD, one)
    assert not password_valid("wrong", one)
    assert not password_valid(PASSWORD, "scrypt-v999$bad$bad")


def test_email_worker_delivery_fencing_and_encrypted_payload_cleanup(
    store: ResearchStore, monkeypatch: pytest.MonkeyPatch
):
    config = configuration(store)
    service = AccountService(store, config)
    service.signup("email@example.test", PASSWORD, "Customer")
    worker = EmailWorker(
        store,
        EmailSettings.model_validate(
            {
                "money_email_encryption_key": config.money_email_encryption_key,
                "money_smtp_host": "smtp.example.test",
                "money_smtp_username": "test",
                "money_smtp_password": "test-only",
                "money_email_from": "money@example.test",
            }
        ),
    )
    sent: list[str] = []
    monkeypatch.setattr(worker, "deliver", lambda row: sent.append(row["id"]))
    assert worker.once() and not worker.once()
    with store.engine.connect() as connection:
        row = connection.execute(select(db.email_outbox)).mappings().one()
        assert row["state"] == "DELIVERED" and row["encrypted_payload"] == ""
    assert len(sent) == 1


def paid_workspace(service: AccountService, account: dict[str, Any]) -> Any:
    from money.product.models import subscriptions
    from money.product.service import ProductService

    actor = service.principal(account["session_token"], account["workspace"]["id"])
    with service.store.transaction() as connection:
        ProductService(service.store).subscription(connection, actor.workspace_id)
        connection.execute(
            subscriptions.update()
            .where(subscriptions.c.workspace_id == actor.workspace_id)
            .values(plan="TEAM")
        )
    return actor


def test_owner_invitation_acceptance_role_changes_and_removal(store: ResearchStore):
    service = AccountService(store, configuration(store))
    owner = customer(service, "team-owner@example.test")
    teammate = customer(service, "team-member@example.test")
    actor = paid_workspace(service, owner)
    service.invite(actor, teammate["user"]["email"], "VIEWER")
    token = email_token(service, owner["user"]["email"], "invitation")
    member = service.authenticated_user(teammate["session_token"])
    accepted = service.accept_invitation(member, token)
    assert accepted["id"] == actor.workspace_id and accepted["role"] == "VIEWER"
    assert len(service.workspace_members(actor)["members"]) == 2
    with pytest.raises(AccountError, match="TOKEN_INVALID"):
        service.accept_invitation(member, token)
    service.change_member(actor, member["id"], "MEMBER")
    assert service.principal(teammate["session_token"], actor.workspace_id).role == "MEMBER"
    service.change_member(actor, member["id"], None)
    with pytest.raises(AccountError, match="WORKSPACE_FORBIDDEN"):
        service.principal(teammate["session_token"], actor.workspace_id)
    with pytest.raises(AccountError, match="OWNER_PROTECTED"):
        service.change_member(actor, owner["user"]["id"], None)


def test_invitation_email_binding_owner_role_and_seat_allowance(store: ResearchStore):
    service = AccountService(store, configuration(store))
    owner = customer(service, "invite-owner@example.test")
    other = customer(service, "wrong-email@example.test")
    actor = service.principal(owner["session_token"], owner["workspace"]["id"])
    with pytest.raises(AccountError, match="SEAT_ALLOWANCE_EXHAUSTED"):
        service.invite(actor, "invited@example.test", "MEMBER")
    actor = paid_workspace(service, owner)
    with pytest.raises(AccountError, match="ROLE_FORBIDDEN"):
        service.invite(actor, "invited@example.test", "OWNER")
    service.invite(actor, "invited@example.test", "MEMBER")
    token = email_token(service, owner["user"]["email"], "invitation")
    with pytest.raises(AccountError, match="TOKEN_INVALID"):
        service.accept_invitation(service.authenticated_user(other["session_token"]), token)


def test_pending_invitation_rechecks_downgraded_subscription(store: ResearchStore):
    from money.product.models import subscriptions

    service = AccountService(store, configuration(store))
    owner = customer(service, "downgrade-owner@example.test")
    teammate = customer(service, "downgrade-member@example.test")
    actor = paid_workspace(service, owner)
    service.invite(actor, teammate["user"]["email"], "MEMBER")
    token = email_token(service, owner["user"]["email"], "invitation")
    with store.transaction() as connection:
        connection.execute(
            subscriptions.update()
            .where(subscriptions.c.workspace_id == actor.workspace_id)
            .values(plan="FREE")
        )
    with pytest.raises(AccountError, match="SEAT_ALLOWANCE_EXHAUSTED"):
        service.accept_invitation(service.authenticated_user(teammate["session_token"]), token)


def test_resend_verification_revokes_earlier_link_and_is_enumeration_safe(store: ResearchStore):
    service = AccountService(store, configuration(store))
    service.signup("resend@example.test", PASSWORD, "Customer")
    old = email_token(service, "resend@example.test")
    service.resend_verification("resend@example.test")
    new = email_token(service, "resend@example.test")
    assert old != new
    with pytest.raises(AccountError, match="TOKEN_INVALID"):
        service.verify_email(old)
    service.verify_email(new)
    assert service.resend_verification("not-an-account@example.test") is None


def test_session_inventory_never_exposes_session_bearers_and_cannot_revoke_peer(
    store: ResearchStore,
):
    service = AccountService(store, configuration(store))
    alice, bob = (
        customer(service, "sessions-a@example.test"),
        customer(service, "sessions-b@example.test"),
    )
    user = service.authenticated_user(alice["session_token"])
    inventory = service.list_sessions(user, alice["session_token"])
    assert len(inventory["sessions"]) == 1 and inventory["sessions"][0]["current"]
    assert alice["session_token"] not in json.dumps(inventory)
    assert token_digest(alice["session_token"]) not in json.dumps(inventory)
    with pytest.raises(AccountError, match="SESSION_NOT_FOUND"):
        service.revoke_session(
            service.authenticated_user(bob["session_token"]), inventory["sessions"][0]["id"]
        )
    service.revoke_session(user, inventory["sessions"][0]["id"])
    with pytest.raises(AccountError, match="SESSION_EXPIRED"):
        service.authenticated_user(alice["session_token"])


@pytest.mark.parametrize(
    "workspace_opt_in,user_opt_in,deleted,expected",
    [
        (True, True, False, 1),
        (False, True, False, 0),
        (True, False, False, 0),
        (True, True, True, 0),
    ],
)
def test_research_email_is_durable_idempotent_and_respects_preferences(
    store: ResearchStore, workspace_opt_in: bool, user_opt_in: bool, deleted: bool, expected: int
):
    from money.flows.research import build_runtime
    from money.product.models import preferences
    from money.product.notifications import notify_research_once
    from money.product.service import ProductService
    from money.worker import run_once

    config = configuration(store)
    service = AccountService(store, config)
    account = customer(service, "notified@example.test")
    actor = service.principal(account["session_token"], account["workspace"]["id"])
    scoped = store.for_workspace(actor.workspace_id)
    scoped.create_job(
        "DEMO.L",
        ResearchMandate(),
        admission=lambda connection, job_id: ProductService(store).reserve_research(
            connection, job_id, actor, "DEMO.L"
        ),
    )
    with store.transaction() as connection:
        connection.execute(
            preferences.insert().values(
                workspace_id=actor.workspace_id,
                payload={"research_email": workspace_opt_in},
                updated_at=now_utc(),
            )
        )
        connection.execute(
            db.users.update()
            .where(db.users.c.id == actor.user_id)
            .values(
                email_notifications=user_opt_in,
                deletion_requested_at=now_utc() if deleted else None,
            )
        )
    run_once(store, build_runtime("demo"), worker_id="notification-test", mode="demo")
    assert notify_research_once(store, config)
    assert not notify_research_once(store, config)
    with store.engine.connect() as connection:
        rows = (
            connection.execute(
                select(db.email_outbox).where(db.email_outbox.c.kind == "research_status")
            )
            .mappings()
            .all()
        )
    assert len(rows) == expected
    if rows:
        message = json.loads(service.cipher.decrypt(rows[0]["encrypted_payload"].encode()))
        assert message["to"] == "notified@example.test"
        assert "DEMO.L" not in message["body"]  # notifications contain no research payload


def test_stale_email_worker_cannot_acknowledge_a_newer_delivery(
    store: ResearchStore, monkeypatch: pytest.MonkeyPatch
):
    config = configuration(store)
    service = AccountService(store, config)
    service.signup("fence@example.test", PASSWORD, "Customer")
    worker = EmailWorker(
        store,
        EmailSettings.model_validate(
            {
                "money_email_encryption_key": config.money_email_encryption_key,
                "money_smtp_host": "smtp.example.test",
                "money_smtp_username": "test",
                "money_smtp_password": "test-only",
                "money_email_from": "money@example.test",
            }
        ),
    )
    replacement = str(uuid4())

    def replace_lease(row: dict[str, Any]) -> None:
        with store.transaction() as connection:
            connection.execute(
                db.email_outbox.update()
                .where(db.email_outbox.c.id == row["id"])
                .values(lease_token=replacement)
            )

    monkeypatch.setattr(worker, "deliver", replace_lease)
    assert worker.once()
    with store.engine.connect() as connection:
        row = connection.execute(select(db.email_outbox)).mappings().one()
        assert row["state"] == "SENDING" and row["lease_token"] == replacement
        assert row["encrypted_payload"]


@pytest.mark.parametrize(
    "overrides", [{"money_enable_synthetic_demo": False}, {"money_env": "production"}]
)
def test_synthetic_result_access_is_revoked_when_server_demo_flag_is_disabled(
    store: ResearchStore, overrides
):
    from money.flows.research import build_runtime
    from money.worker import run_once

    config = configuration(store)
    service = AccountService(store, config)
    account = customer(service, "licence@example.test")
    job = store.for_workspace(account["workspace"]["id"]).create_job("DEMO.L", ResearchMandate())
    run_once(store, build_runtime("demo"), worker_id="licence-test", mode="demo")
    config = config.model_copy(update=overrides)
    with TestClient(create_app(config, store)) as client:
        for path in (
            f"/research/jobs/{job['id']}",
            f"/research/jobs/{job['id']}/reports",
            f"/research/jobs/{job['id']}/evidence",
            "/research/universe",
            "/research/discovery",
        ):
            response = client.get(path, headers=headers(account))
            assert response.status_code == 503, response.text
            assert response.json()["code"] == "MISSING_LICENCE"
            assert "snapshot" not in response.text


def test_production_demo_defence_rejects_enqueue_even_after_invalid_settings_mutation(store):
    config = configuration(store)
    service = AccountService(store, config)
    account = customer(service, "production-demo@example.test")
    with TestClient(create_app(config, store)) as client:
        client.app.state.settings = config.model_copy(update={"money_env": "production"})
        response = client.post(
            "/research/jobs", headers=headers(account), json={"ticker": "DEMO.L"}
        )
        assert response.status_code == 503
        assert response.json()["code"] == "DEMO_UNAVAILABLE"
    assert store.for_workspace(account["workspace"]["id"]).list_jobs() == []


def test_production_worker_refuses_previously_queued_synthetic_job(store, monkeypatch):
    from money.worker import _compute_process

    config = configuration(store).model_copy(update={"money_env": "production"})
    job = store.create_job("DEMO.L", ResearchMandate())
    claim = store.claim_job("production-boundary")
    assert claim is not None
    monkeypatch.setattr("money.worker.os.setsid", lambda: None)
    monkeypatch.setattr("money.worker.ResearchStore", lambda *args, **kwargs: store)

    def forbidden(*args, **kwargs):
        pytest.fail("No runtime may be built for synthetic production work")

    monkeypatch.setattr("money.flows.research.build_runtime", forbidden)
    _compute_process(config, claim)
    row = store.get_job(job["id"])
    assert row["status"] == "FAILED"
    assert row["error_code"] == "LIVE_CONFIGURATION_UNAVAILABLE"


def test_early_api_failures_have_safe_correlation_headers_and_webhook_body_limit(
    store: ResearchStore,
):
    config = configuration(store, money_request_max_bytes=1024)
    with TestClient(create_app(config, store)) as client:
        unauthenticated = client.post("/v1/account/login", json={})
        assert unauthenticated.status_code == 401
        oversized = client.post(
            "/v1/account/login",
            content=b"x" * 1025,
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        )
        assert oversized.status_code == 413
        webhook = client.post("/v1/product/billing/webhook", content=b"x" * 2048)
        assert webhook.status_code != 413  # Separate bounded envelope; signature still required.
        webhook_oversized = client.post("/v1/product/billing/webhook", content=b"x" * 262145)
        assert webhook_oversized.status_code == 413
        for response in (unauthenticated, oversized, webhook, webhook_oversized):
            assert len(response.headers["x-request-id"]) == 32
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
        ready = client.get("/health/ready").json()
        assert "workspace_id" not in ready and "queue_depth" not in ready and "jobs" not in ready


def test_subscription_notifications_are_private_verified_owner_only(store: ResearchStore):
    from money.product.notifications import notify_subscription

    service = AccountService(store, configuration(store))
    owner = customer(service, "billing-owner@example.test")
    unrelated = customer(service, "billing-other@example.test")
    viewer = customer(service, "billing-viewer@example.test")
    with store.transaction() as connection:
        connection.execute(
            db.members.insert().values(
                workspace_id=owner["workspace"]["id"],
                user_id=viewer["user"]["id"],
                role="VIEWER",
                created_at=now_utc(),
            )
        )
        notify_subscription(connection, service, owner["workspace"]["id"], "past_due")
    with store.engine.connect() as connection:
        rows = (
            connection.execute(
                select(db.email_outbox).where(db.email_outbox.c.kind == "subscription")
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1 and rows[0]["user_id"] == owner["user"]["id"]
    assert unrelated["user"]["id"] != rows[0]["user_id"]
    message = json.loads(service.cipher.decrypt(rows[0]["encrypted_payload"].encode()))
    assert message["to"] == "billing-owner@example.test"
    assert "Action needed" in message["subject"]
    assert not any(
        word in message["body"] for word in ("DEMO.L", "snapshot", "report", "price_", "cus_")
    )


def test_subscription_notification_preferences_and_transaction_rollback(store: ResearchStore):
    from money.product.notifications import notify_subscription

    service = AccountService(store, configuration(store))
    owner = customer(service, "billing-preferences@example.test")
    for update in (
        {"email_notifications": False},
        {"email_verified_at": None},
        {"deletion_requested_at": now_utc()},
    ):
        with store.transaction() as connection:
            connection.execute(
                db.users.update()
                .where(db.users.c.id == owner["user"]["id"])
                .values(
                    email_notifications=True,
                    email_verified_at=now_utc(),
                    deletion_requested_at=None,
                )
            )
            connection.execute(
                db.users.update().where(db.users.c.id == owner["user"]["id"]).values(**update)
            )
            notify_subscription(connection, service, owner["workspace"]["id"], "active")
    with store.engine.connect() as connection:
        assert not connection.execute(
            select(db.email_outbox).where(db.email_outbox.c.kind == "subscription")
        ).all()
    with pytest.raises(RuntimeError, match="rollback test"):
        with store.transaction() as connection:
            connection.execute(
                db.users.update()
                .where(db.users.c.id == owner["user"]["id"])
                .values(
                    email_notifications=True,
                    email_verified_at=now_utc(),
                    deletion_requested_at=None,
                )
            )
            notify_subscription(connection, service, owner["workspace"]["id"], "active")
            raise RuntimeError("rollback test")
    with store.engine.connect() as connection:
        assert not connection.execute(
            select(db.email_outbox).where(db.email_outbox.c.kind == "subscription")
        ).all()


def test_subscription_reconciliation_atomically_deduplicates_notification_and_audit(
    store: ResearchStore,
):
    from money.product import models as product_db
    from money.product.service import ProductService
    from money.product.settings import ProductSettings
    from money.product.worker import reconcile_once

    service = AccountService(store, configuration(store))
    owner = customer(service, "billing-reconcile@example.test")
    actor = service.principal(owner["session_token"], owner["workspace"]["id"])
    product = ProductService(store, ProductSettings(money_stripe_prices={"PRO": "price_fixture"}))
    now = now_utc()
    with store.transaction() as connection:
        product.subscription(connection, actor.workspace_id)
        connection.execute(
            product_db.subscriptions.update()
            .where(product_db.subscriptions.c.workspace_id == actor.workspace_id)
            .values(customer_id="cus_fixture")
        )
        connection.execute(
            product_db.billing_events.insert().values(
                id="evt_fixture",
                kind="customer.subscription.updated",
                subscription_id="sub_fixture",
                customer_id="cus_fixture",
                content_hash="a" * 64,
                state="PENDING",
                attempts=0,
                available_at=now,
                created_at=now,
            )
        )

    class Gateway:
        def request(self, method: str, path: str) -> dict[str, Any]:
            assert method == "GET" and path == "/subscriptions/sub_fixture"
            return {
                "id": "sub_fixture",
                "customer": "cus_fixture",
                "livemode": False,
                "metadata": {"workspace_id": actor.workspace_id},
                "status": "past_due",
                "items": {
                    "data": [
                        {
                            "quantity": 1,
                            "price": {"id": "price_fixture"},
                            "current_period_start": int(now.timestamp()),
                            "current_period_end": int((now + timedelta(days=30)).timestamp()),
                        }
                    ]
                },
            }

    assert reconcile_once(product, Gateway(), service)
    assert not reconcile_once(product, Gateway(), service)
    with store.engine.connect() as connection:
        assert (
            len(
                connection.execute(
                    select(db.email_outbox).where(db.email_outbox.c.kind == "subscription")
                ).all()
            )
            == 1
        )
        assert (
            len(
                connection.execute(
                    select(product_db.product_events).where(
                        product_db.product_events.c.event == "subscription_reconciled"
                    )
                ).all()
            )
            == 1
        )
        assert connection.scalar(select(product_db.billing_events.c.state)) == "PROCESSED"


def test_account_deletion_cannot_use_credentials_captured_before_password_reset(
    store: ResearchStore,
):
    service = AccountService(store, configuration(store))
    account = customer(service, "deletion-race@example.test")
    before_reset = service.authenticated_user(account["session_token"])
    service.forgot_password("deletion-race@example.test")
    service.reset_password(
        email_token(service, "deletion-race@example.test", "reset"), "replacement long passphrase"
    )
    with pytest.raises(AccountError, match="SESSION_EXPIRED"):
        service.request_deletion(before_reset, PASSWORD)
    with store.engine.connect() as connection:
        assert (
            connection.scalar(
                select(db.users.c.deletion_requested_at).where(db.users.c.id == before_reset["id"])
            )
            is None
        )


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not configured"
)
def test_real_postgres_tenant_isolation_and_one_use_action_token(monkeypatch: pytest.MonkeyPatch):
    from concurrent.futures import ThreadPoolExecutor

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    url = os.environ["TEST_DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
    schema = f"money_accounts_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    scoped = (
        make_url(url)
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )
    store = ResearchStore(scoped)
    try:
        monkeypatch.setenv("MONEY_ENV", "test")
        config = Config("alembic.ini")
        config.attributes["database_url"] = scoped
        command.upgrade(config, "head")
        test_cross_tenant_uuid_and_forged_workspace_headers_fail_closed(store)
        service = AccountService(store, configuration(store))
        service.signup("pg@example.test", PASSWORD, "PG")
        raw = email_token(service, "pg@example.test")

        def consume(_: int) -> bool:
            try:
                service.verify_email(raw)
                return True
            except AccountError:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sum(executor.map(consume, range(2))) == 1
        # A real PostgreSQL non-key user update models reset's locking order.
        # Rotation must block before creating ANY replacement session.
        import threading

        account = customer(service, "pg-rotation@example.test")
        authenticated, session_created = threading.Event(), threading.Event()
        authenticate = service.authenticated_user
        create_session = service._session

        def observe_authentication(token):
            result = authenticate(token)
            authenticated.set()
            return result

        def observe_creation(connection, user_id):
            session_created.set()
            return create_session(connection, user_id)

        monkeypatch.setattr(service, "authenticated_user", observe_authentication)
        monkeypatch.setattr(service, "_session", observe_creation)
        replacement_hash = password_hash("replacement PostgreSQL passphrase")
        with ThreadPoolExecutor(max_workers=1) as executor:
            with store.transaction() as connection:
                connection.execute(
                    db.users.update()
                    .where(db.users.c.id == account["user"]["id"])
                    .values(password_hash=replacement_hash)
                )
                future = executor.submit(service.rotate, account["session_token"])
                assert authenticated.wait(timeout=3)
                assert not session_created.wait(timeout=0.25)
                connection.execute(
                    db.sessions.update()
                    .where(db.sessions.c.user_id == account["user"]["id"])
                    .values(revoked_at=now_utc())
                )
            with pytest.raises(AccountError, match="SESSION_EXPIRED"):
                future.result(timeout=5)
        assert not session_created.is_set()
    finally:
        store.engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()
