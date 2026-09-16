"""Mutable customer state belongs in PostgreSQL, not Git data assets."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    true,
)

from money.storage.models import document, metadata

users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("email", String(254), nullable=False, unique=True),
    Column("display_name", String(100), nullable=False),
    Column("password_hash", String(256), nullable=False),
    Column("email_verified_at", DateTime(timezone=True)),
    Column("deletion_requested_at", DateTime(timezone=True)),
    Column("email_notifications", Boolean, nullable=False, server_default=true()),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
organisations = Table(
    "organisations",
    metadata,
    Column("id", String(80), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("created_by", String(36), ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
members = Table(
    "organisation_members",
    metadata,
    Column("workspace_id", String(80), ForeignKey("organisations.id"), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), primary_key=True),
    Column("role", String(12), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("role IN ('OWNER', 'ADMIN', 'MEMBER', 'VIEWER')", name="ck_member_role"),
)
Index("ix_members_user", members.c.user_id, members.c.workspace_id)
sessions = Table(
    "account_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
)
Index("ix_account_sessions_user", sessions.c.user_id, sessions.c.expires_at)
tokens = Table(
    "account_action_tokens",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("purpose", String(24), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True)),
)
Index("ix_action_tokens_user", tokens.c.user_id, tokens.c.purpose)
audit = Table(
    "account_audit_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id")),
    Column("workspace_id", String(80), ForeignKey("organisations.id")),
    Column("event", String(64), nullable=False),
    Column("payload", document, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("ix_account_audit_workspace_time", audit.c.workspace_id, audit.c.created_at)
email_outbox = Table(
    "transactional_email_outbox",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("encrypted_payload", Text, nullable=False),
    Column("recipient_hash", String(64)),
    Column("state", String(16), nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("lease_token", String(36)),
    Column("lease_until", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("delivered_at", DateTime(timezone=True)),
    Column("failure_code", String(64)),
)
Index("ix_transactional_email_delivery", email_outbox.c.state, email_outbox.c.available_at)
Index("ix_transactional_email_recipient", email_outbox.c.recipient_hash)

invitations = Table(
    "organisation_invitations",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("workspace_id", String(80), ForeignKey("organisations.id"), nullable=False),
    Column("invited_by", String(36), ForeignKey("users.id"), nullable=False),
    Column("email", String(254), nullable=False),
    Column("role", String(12), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True)),
    CheckConstraint("role IN ('ADMIN', 'MEMBER', 'VIEWER')", name="ck_invitation_role"),
)
Index("ix_invitations_workspace", invitations.c.workspace_id, invitations.c.email)

deletion_requests = Table(
    "account_deletion_requests",
    metadata,
    Column("user_id", String(36), ForeignKey("users.id"), primary_key=True),
    Column("state", String(20), nullable=False),
    Column("requested_at", DateTime(timezone=True), nullable=False),
    Column("recipient_hash", String(64), nullable=False),
    Column("policy_version", String(80)),
    Column("policy_hash", String(64)),
    Column("reviewed_by", String(128)),
    Column("reviewed_at", DateTime(timezone=True)),
    Column("erasure_after", DateTime(timezone=True)),
    Column("erased_at", DateTime(timezone=True)),
    CheckConstraint("state IN ('REQUESTED', 'REVIEWED', 'ERASED')", name="ck_deletion_state"),
)
Index("ix_deletion_fulfilment", deletion_requests.c.state, deletion_requests.c.erasure_after)
Index("ix_deletion_recipient", deletion_requests.c.recipient_hash)

workspace_closures = Table(
    "workspace_closures",
    metadata,
    Column("workspace_id", String(80), ForeignKey("organisations.id"), primary_key=True),
    Column("requested_by", String(36), ForeignKey("users.id"), nullable=False),
    Column("requested_at", DateTime(timezone=True), nullable=False),
    Column("state", String(20), nullable=False),
    Column("customer_id", String(128)),
    Column("subscription_id", String(128)),
    Column("cancellation_key", String(64), nullable=False, unique=True),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("failure_code", String(64)),
    Column("billing_reviewed_at", DateTime(timezone=True)),
    Column("billing_reviewed_by", String(128)),
    CheckConstraint(
        "state IN ('PENDING', 'SCHEDULED', 'CLOSED', 'FAILED')", name="ck_workspace_closure_state"
    ),
)
Index("ix_workspace_closure_queue", workspace_closures.c.state, workspace_closures.c.available_at)

TABLES = (
    users,
    organisations,
    members,
    sessions,
    tokens,
    audit,
    email_outbox,
    invitations,
    deletion_requests,
    workspace_closures,
)
