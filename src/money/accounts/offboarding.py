"""Reviewed offboarding without destructive deletion of immutable research.

No retention duration is a legal default. Operators must configure an approved
policy and attest to billing, exports, backups and retained-record obligations
for each request. The fulfilment action only erases mutable local identity data.
"""

import argparse
import hashlib
import json
import re
from datetime import timedelta
from typing import Any, Self
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Connection, or_, select

from money.accounts import models as db
from money.accounts.security import token_digest
from money.accounts.service import AccountError, audit_event
from money.api.settings import OperatorSettings
from money.product import models as product_db
from money.product.billing import BillingUnavailable, StripeGateway
from money.product.settings import ProductSettings
from money.storage import models as research_db
from money.storage.store import ResearchStore, aware, now_utc

_REFERENCE = r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}"


class OffboardingSettings(OperatorSettings):
    money_email_encryption_key: SecretStr | None = None


class RetentionPolicy(BaseSettings):
    """Explicit approved policy, not an invented jurisdictional retention rule."""

    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)
    money_retention_approved: bool = False
    money_retention_policy_version: str | None = Field(default=None, max_length=80)
    money_retention_approval_reference: str | None = Field(default=None, pattern=_REFERENCE)
    money_pii_retention_days: int | None = Field(default=None, ge=0, le=36500)

    @model_validator(mode="after")
    def approval_requires_policy(self) -> Self:
        if self.money_retention_approved and (
            not self.money_retention_policy_version
            or not self.money_retention_approval_reference
            or self.money_pii_retention_days is None
        ):
            raise ValueError(
                "Approved retention requires a version, reference and explicit duration"
            )
        return self

    def digest(self) -> str:
        if not self.money_retention_approved:
            raise AccountError(
                "RETENTION_POLICY_UNAPPROVED", "Retention policy approval required", 409
            )
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def assert_workspace_open(connection: Connection, workspace_id: str) -> None:
    """Call after the subscription row lock to serialize with account closure."""
    if connection.scalar(
        select(db.workspace_closures.c.workspace_id).where(
            db.workspace_closures.c.workspace_id == workspace_id
        )
    ):
        raise AccountError("WORKSPACE_CLOSING", "This workspace is closing", 409)


def begin_deletion(connection: Connection, user: Any, store: ResearchStore) -> None:
    """User row must already be locked and password authentication rechecked."""
    from money.product.service import ProductService

    now, user_id = now_utc(), user["id"]
    product = ProductService(store)
    memberships = (
        connection.execute(
            select(db.members)
            .where(db.members.c.user_id == user_id)
            .order_by(db.members.c.workspace_id)
            .limit(1001)
        )
        .mappings()
        .all()
    )
    if len(memberships) > 1000:
        raise AccountError(
            "OFFBOARDING_REVIEW_REQUIRED", "Contact support to close this account", 409
        )
    for membership in memberships:
        workspace_id = membership["workspace_id"]
        # Match invitation and billing lock ordering: subscription, workspace.
        subscription = product.subscription(connection, workspace_id)
        connection.execute(
            select(db.organisations.c.id)
            .where(db.organisations.c.id == workspace_id)
            .with_for_update()
        ).one()
        current_role = connection.scalar(
            select(db.members.c.role).where(
                db.members.c.workspace_id == workspace_id, db.members.c.user_id == user_id
            )
        )
        if current_role != "OWNER":
            continue
        others = (
            connection.execute(
                select(db.members.c.role)
                .join(db.users)
                .where(
                    db.members.c.workspace_id == workspace_id,
                    db.members.c.user_id != user_id,
                    db.users.c.deletion_requested_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        if "OWNER" in others:
            continue
        if others:
            raise AccountError(
                "OWNERSHIP_TRANSFER_REQUIRED",
                "Transfer workspace ownership before closing your account",
                409,
            )
        if (
            subscription
            and subscription["checkout_until"]
            and aware(subscription["checkout_until"]) > now
        ):
            raise AccountError(
                "BILLING_CHECKOUT_PENDING",
                "Finish or expire the pending checkout before account closure",
                409,
            )
        connection.execute(
            db.workspace_closures.insert().values(
                workspace_id=workspace_id,
                requested_by=user_id,
                requested_at=now,
                state="PENDING",
                customer_id=subscription["customer_id"] if subscription else None,
                subscription_id=subscription["subscription_id"] if subscription else None,
                cancellation_key=f"close-{uuid4()}",
                attempts=0,
                available_at=now,
            )
        )
        connection.execute(
            db.invitations.update()
            .where(
                db.invitations.c.workspace_id == workspace_id, db.invitations.c.used_at.is_(None)
            )
            .values(used_at=now)
        )
        audit_event(connection, "workspace_closure_requested", user_id, workspace_id)
    connection.execute(
        db.users.update()
        .where(db.users.c.id == user_id)
        .values(deletion_requested_at=now, email_notifications=False)
    )
    connection.execute(
        db.sessions.update().where(db.sessions.c.user_id == user_id).values(revoked_at=now)
    )
    connection.execute(db.tokens.update().where(db.tokens.c.user_id == user_id).values(used_at=now))
    connection.execute(
        db.invitations.update()
        .where(
            or_(db.invitations.c.invited_by == user_id, db.invitations.c.email == user["email"]),
            db.invitations.c.used_at.is_(None),
        )
        .values(used_at=now)
    )
    connection.execute(
        db.email_outbox.update()
        .where(
            or_(
                db.email_outbox.c.user_id == user_id,
                db.email_outbox.c.recipient_hash == token_digest(user["email"]),
            ),
            db.email_outbox.c.state == "QUEUED",
        )
        .values(state="CANCELLED", encrypted_payload="", failure_code="ACCOUNT_CLOSING")
    )
    connection.execute(db.members.delete().where(db.members.c.user_id == user_id))
    connection.execute(
        db.deletion_requests.insert().values(
            user_id=user_id,
            state="REQUESTED",
            requested_at=now,
            recipient_hash=token_digest(user["email"]),
        )
    )
    audit_event(
        connection,
        "account_deletion_requested",
        user_id,
        payload={
            "retention_status": "pending_review",
            "automatic_evidence_deletion": False,
        },
    )


def _locked_request(connection: Connection, user_id: str) -> tuple[Any, Any, list[Any]]:
    user = (
        connection.execute(select(db.users).where(db.users.c.id == user_id).with_for_update())
        .mappings()
        .one_or_none()
    )
    request = (
        connection.execute(
            select(db.deletion_requests)
            .where(db.deletion_requests.c.user_id == user_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if user is None or request is None:
        raise AccountError("DELETION_NOT_FOUND", "Deletion request not found", 404)
    workspaces = (
        connection.execute(
            select(db.workspace_closures.c.workspace_id)
            .where(db.workspace_closures.c.requested_by == user_id)
            .order_by(db.workspace_closures.c.workspace_id)
        )
        .scalars()
        .all()
    )
    if workspaces:
        connection.execute(
            select(product_db.subscriptions.c.workspace_id)
            .where(product_db.subscriptions.c.workspace_id.in_(workspaces))
            .order_by(product_db.subscriptions.c.workspace_id)
            .with_for_update()
        ).all()
    closures = (
        connection.execute(
            select(db.workspace_closures)
            .where(db.workspace_closures.c.requested_by == user_id)
            .order_by(db.workspace_closures.c.workspace_id)
            .with_for_update()
        )
        .mappings()
        .all()
    )
    return user, request, list(closures)


def close_billing_once(store: ResearchStore, gateway: StripeGateway) -> bool:
    """One bounded attempt; serialize with reconciliation on subscription row.

    Cancellation stops renewal, not proof of settled invoices. Stripe customer
    records and financial records are not erased by this method.
    """
    with store.transaction() as connection:
        candidate_row = connection.execute(
            select(db.workspace_closures.c.workspace_id, db.workspace_closures.c.requested_by)
            .where(
                db.workspace_closures.c.state.in_(["PENDING", "SCHEDULED"]),
                db.workspace_closures.c.available_at <= now_utc(),
            )
            .order_by(db.workspace_closures.c.available_at)
            .limit(1)
        ).one_or_none()
        if candidate_row is None:
            return False
        candidate = candidate_row.workspace_id
        # Account audit FKs also touch the user row. Take it first, as deletion
        # and fulfilment do, rather than creating a subscription→user lock cycle.
        if (
            connection.execute(
                select(db.users.c.id)
                .where(db.users.c.id == candidate_row.requested_by)
                .with_for_update(skip_locked=True)
            ).one_or_none()
            is None
        ):
            return False
        subscription = (
            connection.execute(
                select(product_db.subscriptions)
                .where(product_db.subscriptions.c.workspace_id == candidate)
                .with_for_update(skip_locked=True)
            )
            .mappings()
            .one_or_none()
        )
        if subscription is None and connection.scalar(
            select(product_db.subscriptions.c.workspace_id).where(
                product_db.subscriptions.c.workspace_id == candidate
            )
        ):
            return False  # A concurrent reconciler owns this subscription lock.
        closure = (
            connection.execute(
                select(db.workspace_closures)
                .where(
                    db.workspace_closures.c.workspace_id == candidate,
                    db.workspace_closures.c.state.in_(["PENDING", "SCHEDULED"]),
                    db.workspace_closures.c.available_at <= now_utc(),
                )
                .with_for_update(skip_locked=True)
            )
            .mappings()
            .one_or_none()
        )
        if closure is None:
            return False
        now = now_utc()
        failure: str | None = None
        state = "CLOSED"
        try:
            customer_id, subscription_id = closure["customer_id"], closure["subscription_id"]
            if customer_id and not subscription_id:
                raise BillingUnavailable("BILLING_IDENTITY_UNRESOLVED")
            if subscription_id:
                if (
                    not subscription
                    or subscription["subscription_id"] != subscription_id
                    or subscription["customer_id"] != customer_id
                ):
                    raise BillingUnavailable("BILLING_IDENTITY_MISMATCH")
                path = "/subscriptions/" + subscription_id
                current = gateway.request("GET", path)
                _check_stripe_identity(current, closure, gateway.settings)
                if current.get("status") not in {"canceled", "incomplete_expired"}:
                    if current.get("cancel_at_period_end") is not True:
                        updated = gateway.request(
                            "POST",
                            path,
                            {"cancel_at_period_end": "true"},
                            closure["cancellation_key"],
                        )
                        _check_stripe_identity(updated, closure, gateway.settings)
                    current = gateway.request("GET", path)
                    _check_stripe_identity(current, closure, gateway.settings)
                    if (
                        current.get("cancel_at_period_end") is not True
                        and current.get("status") != "canceled"
                    ):
                        raise BillingUnavailable("BILLING_CLOSURE_UNCONFIRMED")
                    state = "CLOSED" if current.get("status") == "canceled" else "SCHEDULED"
                if state == "CLOSED":
                    connection.execute(
                        product_db.subscriptions.update()
                        .where(product_db.subscriptions.c.workspace_id == candidate)
                        .values(status=current["status"], updated_at=now)
                    )
        except BillingUnavailable as error:
            failure = (
                str(error)
                if re.fullmatch(r"[A-Z_]{1,64}", str(error))
                else "BILLING_CLOSURE_FAILED"
            )
        attempts = closure["attempts"] + 1 if failure else 0
        if failure:
            state = "FAILED" if attempts >= 5 else "PENDING"
        connection.execute(
            db.workspace_closures.update()
            .where(db.workspace_closures.c.workspace_id == candidate)
            .values(
                state=state,
                attempts=attempts,
                failure_code=failure,
                available_at=now
                + timedelta(seconds=min(3600, 30 * 2**attempts) if failure else 3600),
            )
        )
        audit_event(
            connection,
            "workspace_billing_closure_checked",
            closure["requested_by"],
            candidate,
            {"state": state, "failure_code": failure},
        )
        return True


def _check_stripe_identity(
    current: dict[str, Any], closure: Any, settings: ProductSettings
) -> None:
    if (
        current.get("id") != closure["subscription_id"]
        or current.get("customer") != closure["customer_id"]
        or current.get("livemode") is not settings.money_stripe_live
        or not isinstance(current.get("metadata"), dict)
        or current["metadata"].get("workspace_id") != closure["workspace_id"]
        or current.get("status")
        not in {
            "active",
            "trialing",
            "past_due",
            "unpaid",
            "incomplete",
            "paused",
            "canceled",
            "incomplete_expired",
        }
    ):
        raise BillingUnavailable("BILLING_IDENTITY_MISMATCH")


def review_billing_closure(
    store: ResearchStore,
    workspace_id: str,
    reviewer: str,
    *,
    provider_review_reference: str | None = None,
    retry: bool = False,
) -> None:
    """Audited provider-review recovery; never infer invoice settlement.

    A customer created by abandoned checkout may have no subscription ID. Only
    explicit reviewed no-subscription evidence can resolve that case. Known
    subscriptions still require provider cancellation and identity verification.
    """
    if not re.fullmatch(_REFERENCE, reviewer):
        raise AccountError("BILLING_REVIEW_REQUIRED", "Operator review reference required", 409)
    with store.transaction() as connection:
        requested_by = connection.scalar(
            select(db.workspace_closures.c.requested_by).where(
                db.workspace_closures.c.workspace_id == workspace_id
            )
        )
        if requested_by is None:
            raise AccountError("CLOSURE_NOT_FOUND", "Workspace closure not found", 404)
        connection.execute(
            select(db.users.c.id).where(db.users.c.id == requested_by).with_for_update()
        ).one()
        subscription = (
            connection.execute(
                select(product_db.subscriptions)
                .where(product_db.subscriptions.c.workspace_id == workspace_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        closure = (
            connection.execute(
                select(db.workspace_closures)
                .where(db.workspace_closures.c.workspace_id == workspace_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if closure is None:
            raise AccountError("CLOSURE_NOT_FOUND", "Workspace closure not found", 404)
        if retry:
            if closure["state"] != "FAILED":
                raise AccountError(
                    "CLOSURE_NOT_FAILED", "Only a failed closure may be retried", 409
                )
            connection.execute(
                db.workspace_closures.update()
                .where(db.workspace_closures.c.workspace_id == workspace_id)
                .values(state="PENDING", attempts=0, available_at=now_utc(), failure_code=None)
            )
            audit_event(
                connection,
                "billing_closure_retry_approved",
                closure["requested_by"],
                workspace_id,
                {"reviewer": reviewer},
            )
            return
        if not provider_review_reference or not re.fullmatch(_REFERENCE, provider_review_reference):
            raise AccountError(
                "BILLING_REVIEW_REQUIRED",
                "Provider no-subscription and invoice review required",
                409,
            )
        if closure["subscription_id"] or (subscription and subscription["subscription_id"]):
            raise AccountError(
                "BILLING_PROVIDER_CLOSURE_REQUIRED",
                "Known subscription must close through provider verification",
                409,
            )
        if (
            subscription
            and subscription["checkout_until"]
            and aware(subscription["checkout_until"]) > now_utc()
        ):
            raise AccountError(
                "BILLING_CHECKOUT_PENDING", "Pending checkout must expire first", 409
            )
        if closure["customer_id"] and connection.scalar(
            select(product_db.billing_events.c.id)
            .where(
                product_db.billing_events.c.customer_id == closure["customer_id"],
                product_db.billing_events.c.state.in_(["PENDING", "FAILED"]),
            )
            .limit(1)
        ):
            raise AccountError(
                "BILLING_RECONCILIATION_REQUIRED",
                "Resolve billing events before operator closure",
                409,
            )
        connection.execute(
            db.workspace_closures.update()
            .where(db.workspace_closures.c.workspace_id == workspace_id)
            .values(
                state="CLOSED",
                failure_code=None,
                billing_reviewed_at=now_utc(),
                billing_reviewed_by=reviewer,
            )
        )
        audit_event(
            connection,
            "unlinked_billing_closure_reviewed",
            closure["requested_by"],
            workspace_id,
            {
                "reviewer": reviewer,
                "provider_review_reference": provider_review_reference,
                "no_subscription_and_invoice_resolution_attested": True,
            },
        )


def review_request(
    store: ResearchStore,
    user_id: str,
    policy: RetentionPolicy,
    reviewer: str,
    *,
    billing_resolved: bool,
    retained_records_reviewed: bool,
) -> None:
    """Operator attests external invoices, backups, exports and retention holds."""
    digest = policy.digest()
    if (
        not re.fullmatch(_REFERENCE, reviewer)
        or not billing_resolved
        or not retained_records_reviewed
    ):
        raise AccountError(
            "RETENTION_REVIEW_REQUIRED", "Explicit billing and retained-record review required", 409
        )
    with store.transaction() as connection:
        _, request, closures = _locked_request(connection, user_id)
        if request["state"] == "ERASED":
            return
        _require_closed_billing(connection, closures)
        assert policy.money_pii_retention_days is not None
        connection.execute(
            db.deletion_requests.update()
            .where(db.deletion_requests.c.user_id == user_id)
            .values(
                state="REVIEWED",
                policy_version=policy.money_retention_policy_version,
                policy_hash=digest,
                reviewed_by=reviewer,
                reviewed_at=now_utc(),
                erasure_after=aware(request["requested_at"])
                + timedelta(days=policy.money_pii_retention_days),
            )
        )
        connection.execute(
            db.workspace_closures.update()
            .where(db.workspace_closures.c.requested_by == user_id)
            .values(
                billing_reviewed_at=now_utc(),
                billing_reviewed_by=reviewer,
            )
        )
        audit_event(
            connection,
            "account_erasure_reviewed",
            user_id,
            payload={
                "policy_hash": digest,
                "reviewer": reviewer,
                "billing_resolved": True,
                "retained_records_reviewed": True,
            },
        )


def _require_closed_billing(connection: Connection, closures: list[Any]) -> None:
    for closure in closures:
        if closure["state"] != "CLOSED":
            raise AccountError(
                "BILLING_CLOSURE_PENDING", "Billing closure must be confirmed first", 409
            )
        subscription = (
            connection.execute(
                select(product_db.subscriptions).where(
                    product_db.subscriptions.c.workspace_id == closure["workspace_id"]
                )
            )
            .mappings()
            .one_or_none()
        )
        if subscription and (
            subscription["customer_id"] != closure["customer_id"]
            or subscription["subscription_id"] != closure["subscription_id"]
        ):
            raise AccountError(
                "BILLING_IDENTITY_MISMATCH", "Reconcile changed billing identity first", 409
            )
        if closure["subscription_id"] and (
            not subscription or subscription["status"] not in {"canceled", "incomplete_expired"}
        ):
            raise AccountError("BILLING_CLOSURE_PENDING", "Reconcile provider closure first", 409)
        if closure["customer_id"] and connection.scalar(
            select(product_db.billing_events.c.id)
            .where(
                product_db.billing_events.c.customer_id == closure["customer_id"],
                product_db.billing_events.c.state.in_(["PENDING", "FAILED"]),
            )
            .limit(1)
        ):
            raise AccountError(
                "BILLING_RECONCILIATION_REQUIRED", "Resolve outstanding billing events first", 409
            )


def fulfil_request(store: ResearchStore, user_id: str, policy: RetentionPolicy) -> bool:
    """Idempotently erase eligible mutable local PII; never research/audit rows."""
    digest = policy.digest()
    with store.transaction() as connection:
        user, request, closures = _locked_request(connection, user_id)
        if request["state"] == "ERASED":
            return False
        if (
            request["state"] != "REVIEWED"
            or request["policy_hash"] != digest
            or request["erasure_after"] is None
        ):
            raise AccountError("RETENTION_REVIEW_REQUIRED", "Current policy review required", 409)
        if aware(request["erasure_after"]) > now_utc():
            raise AccountError("RETENTION_NOT_ELAPSED", "Retention period has not elapsed", 409)
        _require_closed_billing(connection, closures)
        workspace_ids = [row["workspace_id"] for row in closures]
        if workspace_ids and connection.scalar(
            select(research_db.jobs.c.id)
            .where(
                research_db.jobs.c.workspace_id.in_(workspace_ids),
                research_db.jobs.c.status.not_in(["COMPLETE", "FAILED", "REJECTED"]),
            )
            .limit(1)
        ):
            raise AccountError("RESEARCH_STILL_ACTIVE", "Wait for durable research to finish", 409)
        email_scope = or_(
            db.email_outbox.c.user_id == user_id,
            db.email_outbox.c.recipient_hash == token_digest(user["email"]),
        )
        if connection.scalar(
            select(db.email_outbox.c.id)
            .where(
                email_scope,
                db.email_outbox.c.state == "SENDING",
                db.email_outbox.c.lease_until > now_utc(),
            )
            .limit(1)
        ):
            raise AccountError("EMAIL_STILL_ACTIVE", "Wait for the active email lease to end", 409)
        # Legacy ciphertext lacks recipient correlation. Backfill before claiming
        # erasure, otherwise a pending invitation could retain this address.
        if connection.scalar(
            select(db.email_outbox.c.id)
            .where(
                db.email_outbox.c.recipient_hash.is_(None),
                db.email_outbox.c.encrypted_payload != "",
            )
            .limit(1)
        ):
            raise AccountError(
                "LEGACY_EMAIL_REVIEW_REQUIRED", "Index legacy email recipients before erasure", 409
            )
        connection.execute(db.email_outbox.delete().where(email_scope))
        connection.execute(db.tokens.delete().where(db.tokens.c.user_id == user_id))
        connection.execute(db.sessions.delete().where(db.sessions.c.user_id == user_id))
        connection.execute(
            db.invitations.delete().where(
                or_(db.invitations.c.invited_by == user_id, db.invitations.c.email == user["email"])
            )
        )
        connection.execute(db.members.delete().where(db.members.c.user_id == user_id))
        connection.execute(
            product_db.notification_reads.delete().where(
                product_db.notification_reads.c.user_id == user_id
            )
        )
        if workspace_ids:
            connection.execute(
                product_db.watchlist.delete().where(
                    product_db.watchlist.c.workspace_id.in_(workspace_ids)
                )
            )
            connection.execute(
                product_db.preferences.delete().where(
                    product_db.preferences.c.workspace_id.in_(workspace_ids)
                )
            )
            connection.execute(
                db.organisations.update()
                .where(db.organisations.c.id.in_(workspace_ids))
                .values(name="Closed workspace")
            )
        connection.execute(
            db.users.update()
            .where(db.users.c.id == user_id)
            .values(
                email=f"erased+{user_id}@deleted.invalid",
                display_name="Deleted account",
                password_hash="disabled",
                email_notifications=False,
                email_verified_at=None,
            )
        )
        connection.execute(
            db.deletion_requests.update()
            .where(db.deletion_requests.c.user_id == user_id)
            .values(state="ERASED", erased_at=now_utc())
        )
        audit_event(
            connection,
            "account_local_pii_erased",
            user_id,
            payload={"policy_hash": digest, "immutable_records_preserved": True},
        )
        return True


def backfill_email_recipients(store: ResearchStore, cipher: Fernet, limit: int = 100) -> int:
    """Bounded safe migration of legacy ciphertext correlation; prints no PII."""
    if not 1 <= limit <= 1000:
        raise ValueError("EMAIL_INDEX_LIMIT_INVALID")
    with store.transaction() as connection:
        rows = (
            connection.execute(
                select(db.email_outbox)
                .where(
                    db.email_outbox.c.recipient_hash.is_(None),
                    db.email_outbox.c.encrypted_payload != "",
                )
                .order_by(db.email_outbox.c.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            .mappings()
            .all()
        )
        for row in rows:
            payload = json.loads(cipher.decrypt(row["encrypted_payload"].encode()))
            recipient = payload.get("to") if isinstance(payload, dict) else None
            if not isinstance(recipient, str) or not 3 <= len(recipient) <= 254:
                raise AccountError("EMAIL_INDEX_INVALID", "Legacy email review required", 409)
            connection.execute(
                db.email_outbox.update()
                .where(db.email_outbox.c.id == row["id"])
                .values(recipient_hash=token_digest(recipient.strip().lower()))
            )
        return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reviewed Money account offboarding; no research or audit deletion"
    )
    parser.add_argument(
        "action",
        choices=[
            "inspect",
            "review",
            "fulfil",
            "close-billing",
            "backfill-email-index",
            "retry-billing",
            "resolve-unlinked-billing",
        ],
    )
    parser.add_argument("--user-id", type=UUID)
    parser.add_argument("--workspace-id", type=UUID)
    parser.add_argument("--provider-review-reference")
    parser.add_argument("--reviewer")
    parser.add_argument("--billing-resolved", action="store_true")
    parser.add_argument("--retained-records-reviewed", action="store_true")
    args = parser.parse_args()
    settings = OffboardingSettings()  # type: ignore[call-arg]
    store = ResearchStore(
        settings.database_url.get_secret_value(),
        allow_sqlite=settings.money_env in {"development", "test"},
    )
    try:
        if args.action == "close-billing":
            result: Any = {"processed": close_billing_once(store, StripeGateway(ProductSettings()))}
        elif args.action in {"retry-billing", "resolve-unlinked-billing"}:
            if args.workspace_id is None:
                parser.error("--workspace-id is required")
            review_billing_closure(
                store,
                str(args.workspace_id),
                args.reviewer or "",
                provider_review_reference=args.provider_review_reference,
                retry=args.action == "retry-billing",
            )
            result = {"reviewed": True}
        elif args.action == "backfill-email-index":
            if not settings.money_email_encryption_key:
                raise AccountError("EMAIL_KEY_REQUIRED", "Email encryption key required", 409)
            result = {
                "indexed": backfill_email_recipients(
                    store, Fernet(settings.money_email_encryption_key.get_secret_value().encode())
                )
            }
        else:
            if args.user_id is None:
                parser.error("--user-id is required")
            user_id = str(args.user_id)
            if args.action == "review":
                review_request(
                    store,
                    user_id,
                    RetentionPolicy(),
                    args.reviewer or "",
                    billing_resolved=args.billing_resolved,
                    retained_records_reviewed=args.retained_records_reviewed,
                )
                result = {"reviewed": True}
            elif args.action == "fulfil":
                result = {"erased": fulfil_request(store, user_id, RetentionPolicy())}
            else:
                with store.engine.connect() as connection:
                    request = (
                        connection.execute(
                            select(db.deletion_requests).where(
                                db.deletion_requests.c.user_id == user_id
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    result = {"request": dict(request) if request else None}
        print(json.dumps(result, default=str))
    except (AccountError, BillingUnavailable) as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(1) from None
    finally:
        store.engine.dispose()


if __name__ == "__main__":
    main()
