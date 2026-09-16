"""Transactional identity and tenant capabilities, with no browser-trusted identity."""

import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet
from sqlalchemy import Connection, func, or_, select
from sqlalchemy.exc import IntegrityError, OperationalError

from money.accounts import models as db
from money.accounts.security import dummy_password_hash, password_hash, password_valid, token_digest
from money.api.settings import Settings
from money.storage.store import ResearchStore, aware, now_utc


class AccountError(Exception):
    def __init__(self, code: str, detail: str, status: int = 400) -> None:
        self.code, self.detail, self.status = code, detail, status
        super().__init__(code)


@dataclass(frozen=True)
class Principal:
    user_id: str
    workspace_id: str
    role: str
    email: str

    def require_write(self) -> None:
        if self.role not in {"OWNER", "ADMIN", "MEMBER"}:
            raise AccountError("ROLE_FORBIDDEN", "This workspace role is read-only", 403)

    def require_admin(self) -> None:
        if self.role not in {"OWNER", "ADMIN"}:
            raise AccountError(
                "ROLE_FORBIDDEN", "Workspace administration permission required", 403
            )


def audit_event(
    connection: Connection,
    event: str,
    user_id: str | None = None,
    workspace_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        db.audit.insert().values(
            id=str(uuid4()),
            event=event,
            user_id=user_id,
            workspace_id=workspace_id,
            payload=payload or {},
            created_at=now_utc(),
        )
    )


def public_user(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "email_verified": row["email_verified_at"] is not None,
        "email_notifications": row["email_notifications"],
    }


class AccountService:
    def __init__(self, store: ResearchStore, settings: Settings) -> None:
        self.store, self.settings = store, settings
        assert settings.money_email_encryption_key is not None
        self.cipher = Fernet(settings.money_email_encryption_key.get_secret_value().encode())
        # Same expensive verification path for unknown and existing users.
        self.dummy_hash = dummy_password_hash()

    def limit(self, operation: str, identifier: str, *, limit: int = 5) -> None:
        # Database buckets work across serverless/API instances; email is never in keys/logs.
        for key, allowance in (
            (f"account:{operation}:global", self.settings.money_login_global_per_minute),
            (f"account:{operation}:{token_digest(identifier)}", limit),
        ):
            if not self.store.consume_rate_limit(key, allowance, 60)["allowed"]:
                raise AccountError("RATE_LIMITED", "Too many attempts. Please try again later", 429)

    def _email(
        self, connection: Connection, user_id: str, email: str, kind: str, subject: str, body: str
    ) -> None:
        # Billing may hold a subscription lock, while closure starts at user.
        # NOWAIT aborts/retries contention instead of creating a lock cycle.
        # Never enqueue a captured address after account erasure commits.
        try:
            identities = (
                connection.execute(
                    select(db.users)
                    .where(or_(db.users.c.id == user_id, db.users.c.email == email))
                    .order_by(db.users.c.id)
                    .with_for_update(nowait=True)
                )
                .mappings()
                .all()
            )
        except OperationalError as error:
            raise AccountError(
                "ACCOUNT_BUSY", "Account update in progress. Please retry", 503
            ) from error
        source = next((row for row in identities if row["id"] == user_id), None)
        recipient = next((row for row in identities if row["email"] == email), None)
        suppressed = (
            source is None
            or bool(source["deletion_requested_at"])
            or (kind != "invitation" and source["email"] != email)
            or (recipient is not None and bool(recipient["deletion_requested_at"]))
        )
        if not suppressed and recipient is None:
            suppressed = (
                connection.scalar(
                    select(db.deletion_requests.c.user_id)
                    .where(
                        db.deletion_requests.c.recipient_hash == token_digest(email.strip().lower())
                    )
                    .limit(1)
                )
                is not None
            )
        if suppressed:
            if kind == "invitation":
                # Roll back the invitation row too; suppressing only ciphertext
                # would reintroduce its plaintext address after erasure.
                raise AccountError("INVITATION_UNAVAILABLE", "This invitation cannot be sent", 409)
            return
        payload = self.cipher.encrypt(
            json.dumps({"to": email, "subject": subject, "body": body}).encode()
        ).decode()
        connection.execute(
            db.email_outbox.insert().values(
                id=str(uuid4()),
                user_id=user_id,
                kind=kind,
                encrypted_payload=payload,
                recipient_hash=token_digest(email.strip().lower()),
                state="QUEUED",
                attempts=0,
                available_at=now_utc(),
                created_at=now_utc(),
            )
        )

    def _action(self, connection: Connection, user_id: str, email: str, purpose: str) -> None:
        now = now_utc()
        connection.execute(
            db.tokens.update()
            .where(
                db.tokens.c.user_id == user_id,
                db.tokens.c.purpose == purpose,
                db.tokens.c.used_at.is_(None),
            )
            .values(used_at=now)
        )
        raw = secrets.token_urlsafe(32)
        expiry = now + timedelta(minutes=30 if purpose == "reset" else 1440)
        connection.execute(
            db.tokens.insert().values(
                token_hash=token_digest(raw),
                user_id=user_id,
                purpose=purpose,
                created_at=now,
                expires_at=expiry,
            )
        )
        path = "reset-password" if purpose == "reset" else "verify-email"
        # Fragment avoids putting the bearer token in HTTP access logs/referrers.
        link = f"{self.settings.money_public_web_url.rstrip('/')}/{path}#token={raw}"
        title = "Reset your Money password" if purpose == "reset" else "Verify your Money email"
        self._email(
            connection,
            user_id,
            email,
            purpose,
            title,
            f"{title}: {link}\nThis link expires at {expiry.isoformat()}. "
            "If you did not request this, ignore this email.",
        )

    def signup(self, email: str, password: str, display_name: str) -> None:
        self.limit("signup", email)
        hashed = password_hash(password)
        try:
            with self.store.transaction() as connection:
                existing = connection.execute(select(db.users).where(db.users.c.email == email))
                user = existing.mappings().one_or_none()
                if user is not None:
                    # Never modify an existing unverified account's password using signup.
                    return
                user_id = str(uuid4())
                connection.execute(
                    db.users.insert().values(
                        id=user_id,
                        email=email,
                        display_name=display_name,
                        password_hash=hashed,
                        email_notifications=True,
                        created_at=now_utc(),
                    )
                )
                self._action(connection, user_id, email, "verify")
                audit_event(connection, "signup_completed", user_id)
        except IntegrityError:
            # Concurrent duplicate signup is indistinguishable from an existing account.
            return

    def verify_email(self, raw: str) -> None:
        self.limit("verify", token_digest(raw), limit=10)
        with self.store.transaction() as connection:
            token = self._consume(connection, raw, "verify")
            connection.execute(
                db.users.update()
                .where(db.users.c.id == token["user_id"])
                .values(email_verified_at=now_utc())
            )
            audit_event(connection, "email_verified", token["user_id"])

    def _consume(self, connection: Connection, raw: str, purpose: str) -> Any:
        initial = connection.execute(
            select(db.tokens.c.user_id).where(
                db.tokens.c.token_hash == token_digest(raw), db.tokens.c.purpose == purpose
            )
        ).one_or_none()
        if initial is None:
            raise AccountError("TOKEN_INVALID", "This link is invalid or has expired")
        user = connection.execute(
            select(db.users.c.deletion_requested_at)
            .where(db.users.c.id == initial.user_id)
            .with_for_update()
        ).one_or_none()
        if user is None or user.deletion_requested_at is not None:
            raise AccountError("TOKEN_INVALID", "This link is invalid or has expired")
        token = (
            connection.execute(
                select(db.tokens)
                .where(db.tokens.c.token_hash == token_digest(raw), db.tokens.c.purpose == purpose)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if token is None or token["used_at"] is not None or aware(token["expires_at"]) <= now_utc():
            raise AccountError("TOKEN_INVALID", "This link is invalid or has expired")
        connection.execute(
            db.tokens.update()
            .where(db.tokens.c.token_hash == token["token_hash"])
            .values(used_at=now_utc())
        )
        return token

    def login(self, email: str, password: str) -> dict[str, Any]:
        self.limit("login", email)
        with self.store.engine.connect() as connection:
            user = (
                connection.execute(select(db.users).where(db.users.c.email == email))
                .mappings()
                .one_or_none()
            )
        valid = password_valid(password, user["password_hash"] if user else self.dummy_hash)
        if not valid or user is None or user["deletion_requested_at"] is not None:
            with self.store.transaction() as connection:
                audit_event(connection, "login_failed", user["id"] if user else None)
            raise AccountError("LOGIN_INVALID", "Email or password is incorrect", 401)
        if user["email_verified_at"] is None:
            raise AccountError(
                "EMAIL_VERIFICATION_REQUIRED", "Verify your email before signing in", 403
            )
        with self.store.transaction() as connection:
            # Lock and recheck after hashing: a concurrent reset/deletion invalidates this login.
            current = (
                connection.execute(
                    select(db.users).where(db.users.c.id == user["id"]).with_for_update()
                )
                .mappings()
                .one()
            )
            if (
                current["password_hash"] != user["password_hash"]
                or current["deletion_requested_at"]
            ):
                raise AccountError("LOGIN_INVALID", "Email or password is incorrect", 401)
            raw, expiry = self._session(connection, user["id"])
            audit_event(connection, "login", user["id"])
        return {
            "session_token": raw,
            "expires_at": expiry.isoformat(),
            "user": public_user(user),
            "workspaces": self.workspaces(user["id"]),
        }

    def _session(self, connection: Connection, user_id: str) -> tuple[str, Any]:
        raw = secrets.token_urlsafe(32)
        expiry = now_utc() + timedelta(hours=self.settings.money_account_session_hours)
        connection.execute(
            db.sessions.insert().values(
                token_hash=token_digest(raw),
                user_id=user_id,
                created_at=now_utc(),
                expires_at=expiry,
            )
        )
        return raw, expiry

    def authenticated_user(self, token: str | None) -> dict[str, Any]:
        if not token or not 32 <= len(token) <= 128:
            raise AccountError("AUTH_REQUIRED", "Please sign in to continue", 401)
        with self.store.engine.connect() as connection:
            user = (
                connection.execute(
                    select(db.users)
                    .join(db.sessions, db.users.c.id == db.sessions.c.user_id)
                    .where(
                        db.sessions.c.token_hash == token_digest(token),
                        db.sessions.c.revoked_at.is_(None),
                        db.sessions.c.expires_at > now_utc(),
                        db.users.c.email_verified_at.is_not(None),
                        db.users.c.deletion_requested_at.is_(None),
                    )
                )
                .mappings()
                .one_or_none()
            )
        if user is None:
            raise AccountError("SESSION_EXPIRED", "Your session has expired. Please sign in", 401)
        return dict(user)

    def workspaces(self, user_id: str) -> list[dict[str, Any]]:
        with self.store.engine.connect() as connection:
            rows = connection.execute(
                select(db.organisations.c.id, db.organisations.c.name, db.members.c.role)
                .join(db.members)
                .where(db.members.c.user_id == user_id)
                .order_by(db.organisations.c.created_at, db.organisations.c.id)
                .limit(100)
            ).mappings()
            return [dict(row) for row in rows]

    def principal(self, session_token: str | None, workspace_id: str | None) -> Principal:
        user = self.authenticated_user(session_token)
        statement = (
            select(db.organisations.c.id, db.members.c.role)
            .join(db.members)
            .where(db.members.c.user_id == user["id"])
        )
        if workspace_id:
            statement = statement.where(db.organisations.c.id == workspace_id)
        with self.store.engine.connect() as connection:
            workspace = (
                connection.execute(
                    statement.order_by(db.organisations.c.created_at, db.organisations.c.id).limit(
                        1
                    )
                )
                .mappings()
                .one_or_none()
            )
        if workspace is None:
            raise AccountError(
                "WORKSPACE_FORBIDDEN" if workspace_id else "WORKSPACE_REQUIRED",
                "Select an authorised workspace",
                403,
            )
        return Principal(user["id"], workspace["id"], workspace["role"], user["email"])

    def create_workspace(self, user: dict[str, Any], name: str) -> dict[str, str]:
        self.limit("workspace", user["id"], limit=3)
        workspace_id = str(uuid4())
        with self.store.transaction() as connection:
            # Lock user to serialise workspace-creation caps across API instances.
            active = connection.execute(
                select(db.users.c.deletion_requested_at)
                .where(db.users.c.id == user["id"])
                .with_for_update()
            ).one()
            if active.deletion_requested_at is not None:
                raise AccountError("SESSION_EXPIRED", "Please sign in", 401)
            owned = connection.execute(
                select(db.organisations.c.id)
                .where(db.organisations.c.created_by == user["id"])
                .limit(5)
            ).all()
            if len(owned) >= 5:
                raise AccountError("WORKSPACE_LIMIT", "Workspace creation limit reached", 409)
            connection.execute(
                db.organisations.insert().values(
                    id=workspace_id, name=name, created_by=user["id"], created_at=now_utc()
                )
            )
            connection.execute(
                db.members.insert().values(
                    workspace_id=workspace_id,
                    user_id=user["id"],
                    role="OWNER",
                    created_at=now_utc(),
                )
            )
            audit_event(connection, "workspace_created", user["id"], workspace_id)
        return {"id": workspace_id, "name": name, "role": "OWNER"}

    def forgot_password(self, email: str) -> None:
        self.limit("reset", email)
        with self.store.transaction() as connection:
            user = (
                connection.execute(
                    select(db.users)
                    .where(db.users.c.email == email, db.users.c.deletion_requested_at.is_(None))
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if user:
                self._action(connection, user["id"], email, "reset")

    def resend_verification(self, email: str) -> None:
        self.limit("resend-verification", email)
        with self.store.transaction() as connection:
            user = (
                connection.execute(
                    select(db.users)
                    .where(
                        db.users.c.email == email,
                        db.users.c.email_verified_at.is_(None),
                        db.users.c.deletion_requested_at.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if user:
                self._action(connection, user["id"], email, "verify")

    def reset_password(self, raw: str, password: str) -> None:
        self.limit("reset-confirm", token_digest(raw))
        hashed = password_hash(password)
        with self.store.transaction() as connection:
            token = self._consume(connection, raw, "reset")
            connection.execute(
                db.users.update()
                .where(db.users.c.id == token["user_id"])
                .values(password_hash=hashed)
            )
            connection.execute(
                db.sessions.update()
                .where(
                    db.sessions.c.user_id == token["user_id"], db.sessions.c.revoked_at.is_(None)
                )
                .values(revoked_at=now_utc())
            )
            audit_event(connection, "password_reset", token["user_id"])

    def logout(self, raw: str | None) -> None:
        if not raw:
            return
        with self.store.transaction() as connection:
            initial = connection.scalar(
                select(db.sessions.c.user_id).where(db.sessions.c.token_hash == token_digest(raw))
            )
            if initial is None:
                return
            connection.execute(
                select(db.users.c.id).where(db.users.c.id == initial).with_for_update()
            ).one()
            row = (
                connection.execute(
                    select(db.sessions)
                    .where(db.sessions.c.token_hash == token_digest(raw))
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row and row["revoked_at"] is None:
                connection.execute(
                    db.sessions.update()
                    .where(db.sessions.c.token_hash == token_digest(raw))
                    .values(revoked_at=now_utc())
                )
                audit_event(connection, "logout", row["user_id"])

    def rotate(self, raw: str | None) -> dict[str, str]:
        user = self.authenticated_user(raw)
        assert raw is not None
        with self.store.transaction() as connection:
            # All session creators lock the user first. Otherwise a reset's
            # UPDATE sessions snapshot could miss a concurrently inserted session.
            current = (
                connection.execute(
                    select(db.users).where(db.users.c.id == user["id"]).with_for_update()
                )
                .mappings()
                .one()
            )
            if (
                current["deletion_requested_at"] is not None
                or current["password_hash"] != user["password_hash"]
            ):
                raise AccountError("SESSION_EXPIRED", "Please sign in", 401)
            old = (
                connection.execute(
                    select(db.sessions)
                    .where(db.sessions.c.token_hash == token_digest(raw))
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if old["revoked_at"] is not None or aware(old["expires_at"]) <= now_utc():
                raise AccountError("SESSION_EXPIRED", "Please sign in", 401)
            connection.execute(
                db.sessions.update()
                .where(db.sessions.c.token_hash == token_digest(raw))
                .values(revoked_at=now_utc())
            )
            token, expiry = self._session(connection, user["id"])
            audit_event(connection, "session_rotated", user["id"])
        return {"session_token": token, "expires_at": expiry.isoformat()}

    def list_sessions(self, user: dict[str, Any], current: str | None) -> dict[str, Any]:
        with self.store.engine.connect() as connection:
            rows = connection.execute(
                select(db.sessions)
                .where(
                    db.sessions.c.user_id == user["id"],
                    db.sessions.c.revoked_at.is_(None),
                    db.sessions.c.expires_at > now_utc(),
                )
                .order_by(db.sessions.c.created_at.desc())
                .limit(100)
            ).mappings()
            return {
                "sessions": [
                    {
                        "id": token_digest("session-reference:" + row["token_hash"]),
                        "created_at": aware(row["created_at"]).isoformat(),
                        "expires_at": aware(row["expires_at"]).isoformat(),
                        "current": current is not None
                        and token_digest(current) == row["token_hash"],
                    }
                    for row in rows
                ]
            }

    def revoke_session(self, user: dict[str, Any], reference: str) -> None:
        with self.store.transaction() as connection:
            current = connection.execute(
                select(db.users.c.deletion_requested_at)
                .where(db.users.c.id == user["id"])
                .with_for_update()
            ).one()
            if current.deletion_requested_at is not None:
                raise AccountError("SESSION_EXPIRED", "Please sign in", 401)
            rows = connection.execute(
                select(db.sessions)
                .where(
                    db.sessions.c.user_id == user["id"],
                    db.sessions.c.revoked_at.is_(None),
                    db.sessions.c.expires_at > now_utc(),
                )
                .limit(100)
                .with_for_update()
            ).mappings()
            token_hash = next(
                (
                    row["token_hash"]
                    for row in rows
                    if secrets.compare_digest(
                        token_digest("session-reference:" + row["token_hash"]), reference
                    )
                ),
                None,
            )
            if token_hash is None:
                raise AccountError("SESSION_NOT_FOUND", "Session not found", 404)
            connection.execute(
                db.sessions.update()
                .where(db.sessions.c.token_hash == token_hash)
                .values(revoked_at=now_utc())
            )
            audit_event(connection, "session_revoked", user["id"])

    def request_deletion(self, user: dict[str, Any], password: str) -> None:
        from money.accounts.offboarding import begin_deletion

        self.limit("deletion", user["id"])
        if not password_valid(password, user["password_hash"]):
            raise AccountError("PASSWORD_INVALID", "Password is incorrect", 401)
        with self.store.transaction() as connection:
            current = (
                connection.execute(
                    select(db.users).where(db.users.c.id == user["id"]).with_for_update()
                )
                .mappings()
                .one()
            )
            if (
                current["password_hash"] != user["password_hash"]
                or current["deletion_requested_at"]
            ):
                raise AccountError("SESSION_EXPIRED", "Please sign in", 401)
            begin_deletion(connection, current, self.store)

    def transfer_ownership(self, actor: Principal, password: str, successor_id: str) -> None:
        """Explicit password-confirmed succession to an existing verified member."""
        from money.accounts.offboarding import assert_workspace_open

        self.limit("ownership-transfer", actor.user_id)
        if successor_id == actor.user_id:
            raise AccountError("SUCCESSOR_INVALID", "Choose another existing workspace member", 409)
        with self.store.transaction() as connection:
            # Sorted identity locks prevent two overlapping transfers deadlocking
            # and serialize against reset/deletion of either participant.
            identities = (
                connection.execute(
                    select(db.users)
                    .where(db.users.c.id.in_([actor.user_id, successor_id]))
                    .order_by(db.users.c.id)
                    .with_for_update()
                )
                .mappings()
                .all()
            )
            users_by_id = {row["id"]: row for row in identities}
            current = users_by_id.get(actor.user_id)
            successor = users_by_id.get(successor_id)
            if current is None or current["deletion_requested_at"]:
                raise AccountError("SESSION_EXPIRED", "Please sign in", 401)
            if not password_valid(password, current["password_hash"]):
                raise AccountError("PASSWORD_INVALID", "Password is incorrect", 401)
            if (
                successor is None
                or successor["deletion_requested_at"]
                or not successor["email_verified_at"]
            ):
                raise AccountError(
                    "SUCCESSOR_INVALID", "Choose an active verified workspace member", 409
                )
            connection.execute(
                select(db.organisations.c.id)
                .where(db.organisations.c.id == actor.workspace_id)
                .with_for_update()
            ).one()
            assert_workspace_open(connection, actor.workspace_id)
            self._owner(connection, actor)
            if (
                connection.scalar(
                    select(db.members.c.role).where(
                        db.members.c.workspace_id == actor.workspace_id,
                        db.members.c.user_id == successor_id,
                    )
                )
                is None
            ):
                raise AccountError("SUCCESSOR_INVALID", "Choose an existing workspace member", 409)
            connection.execute(
                db.members.update()
                .where(
                    db.members.c.workspace_id == actor.workspace_id,
                    db.members.c.user_id == successor_id,
                )
                .values(role="OWNER")
            )
            connection.execute(
                db.members.update()
                .where(
                    db.members.c.workspace_id == actor.workspace_id,
                    db.members.c.user_id == actor.user_id,
                )
                .values(role="ADMIN")
            )
            audit_event(
                connection,
                "workspace_ownership_transferred",
                actor.user_id,
                actor.workspace_id,
                {"successor_user_id": successor_id},
            )

    def export(self, user: dict[str, Any]) -> dict[str, Any]:
        with self.store.transaction() as connection:
            events = connection.execute(
                select(db.audit.c.event, db.audit.c.created_at)
                .where(db.audit.c.user_id == user["id"])
                .order_by(db.audit.c.created_at.desc())
                .limit(1000)
            ).mappings()
            result = {
                "user": public_user(user),
                "workspaces": self.workspaces(user["id"]),
                "account_events": [dict(row) for row in events],
                "research_export": "Use authorised workspace history and record endpoints",
                "account_event_limit": 1000,
            }
            audit_event(connection, "account_exported", user["id"])
            return result

    def _seat_limit(self, connection: Connection, workspace_id: str) -> int:
        from money.product.service import ProductService

        product = ProductService(self.store)
        sub = product.subscription(connection, workspace_id)
        # Stable lock ordering: billing/queue admission before the workspace lock.
        connection.execute(
            select(db.organisations.c.id)
            .where(db.organisations.c.id == workspace_id)
            .with_for_update()
        ).one()
        if sub["status"] not in {"active", "trialing"} or aware(sub["period_end"]) <= now_utc():
            raise AccountError("SUBSCRIPTION_INACTIVE", "An active subscription is required", 402)
        return product.catalog.plan(sub["plan"]).seats

    @staticmethod
    def _owner(connection: Connection, actor: Principal) -> None:
        try:
            current = connection.execute(
                select(db.users.c.deletion_requested_at)
                .where(db.users.c.id == actor.user_id)
                .with_for_update(nowait=True)
            ).one_or_none()
        except OperationalError as error:
            raise AccountError(
                "ACCOUNT_BUSY", "Account update in progress. Please retry", 503
            ) from error
        if current is None or current.deletion_requested_at is not None:
            raise AccountError("ROLE_FORBIDDEN", "Workspace ownership is required", 403)
        role = connection.scalar(
            select(db.members.c.role).where(
                db.members.c.workspace_id == actor.workspace_id,
                db.members.c.user_id == actor.user_id,
            )
        )
        if role != "OWNER":
            raise AccountError(
                "ROLE_FORBIDDEN", "Only the workspace owner can manage membership", 403
            )

    def invite(self, actor: Principal, email: str, role: str) -> None:
        if role not in {"ADMIN", "MEMBER", "VIEWER"}:
            raise AccountError("ROLE_FORBIDDEN", "This role cannot be invited", 403)
        self.limit("invitation", actor.user_id, limit=10)
        with self.store.transaction() as connection:
            allowance = self._seat_limit(connection, actor.workspace_id)
            self._owner(connection, actor)
            existing = connection.scalar(
                select(db.members.c.user_id)
                .join(db.users)
                .where(db.members.c.workspace_id == actor.workspace_id, db.users.c.email == email)
            )
            if existing:
                raise AccountError("ALREADY_MEMBER", "This customer is already a member", 409)
            member_count = (
                connection.scalar(
                    select(func.count())
                    .select_from(db.members)
                    .where(db.members.c.workspace_id == actor.workspace_id)
                )
                or 0
            )
            pending = (
                connection.scalar(
                    select(func.count())
                    .select_from(db.invitations)
                    .where(
                        db.invitations.c.workspace_id == actor.workspace_id,
                        db.invitations.c.used_at.is_(None),
                        db.invitations.c.expires_at > now_utc(),
                        db.invitations.c.email != email,
                    )
                )
                or 0
            )
            if member_count + pending >= allowance:
                raise AccountError(
                    "SEAT_ALLOWANCE_EXHAUSTED", "Your workspace has no available seats", 402
                )
            now = now_utc()
            connection.execute(
                db.invitations.update()
                .where(
                    db.invitations.c.workspace_id == actor.workspace_id,
                    db.invitations.c.email == email,
                    db.invitations.c.used_at.is_(None),
                )
                .values(used_at=now)
            )
            raw = secrets.token_urlsafe(32)
            connection.execute(
                db.invitations.insert().values(
                    token_hash=token_digest(raw),
                    workspace_id=actor.workspace_id,
                    invited_by=actor.user_id,
                    email=email,
                    role=role,
                    created_at=now,
                    expires_at=now + timedelta(days=7),
                )
            )
            link = f"{self.settings.money_public_web_url.rstrip('/')}/accept-invite#token={raw}"
            self._email(
                connection,
                actor.user_id,
                email,
                "invitation",
                "Your Money workspace invitation",
                f"Sign up or sign in using this email address, then accept your invitation: {link}\n"
                "This invitation expires in 7 days. Ignore it if unexpected.",
            )
            audit_event(
                connection, "member_invited", actor.user_id, actor.workspace_id, {"role": role}
            )

    def accept_invitation(self, user: dict[str, Any], raw: str) -> dict[str, str]:
        self.limit("accept-invitation", user["id"], limit=10)
        with self.store.transaction() as connection:
            initial = (
                connection.execute(
                    select(db.invitations).where(db.invitations.c.token_hash == token_digest(raw))
                )
                .mappings()
                .one_or_none()
            )
            if initial is None or initial["email"] != user["email"]:
                raise AccountError("TOKEN_INVALID", "This invitation is invalid or has expired")
            allowance = self._seat_limit(connection, initial["workspace_id"])
            invite = (
                connection.execute(
                    select(db.invitations)
                    .where(db.invitations.c.token_hash == token_digest(raw))
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if invite["used_at"] or aware(invite["expires_at"]) <= now_utc():
                raise AccountError("TOKEN_INVALID", "This invitation is invalid or has expired")
            inviter = connection.scalar(
                select(db.members.c.role)
                .join(db.users)
                .where(
                    db.members.c.workspace_id == invite["workspace_id"],
                    db.members.c.user_id == invite["invited_by"],
                    db.users.c.deletion_requested_at.is_(None),
                )
            )
            if inviter != "OWNER":
                raise AccountError(
                    "INVITATION_REVOKED", "The invitation is no longer authorised", 403
                )
            existing = connection.scalar(
                select(db.members.c.role).where(
                    db.members.c.workspace_id == invite["workspace_id"],
                    db.members.c.user_id == user["id"],
                )
            )
            if existing is not None:
                raise AccountError("ALREADY_MEMBER", "You already belong to this workspace", 409)
            count = (
                connection.scalar(
                    select(func.count())
                    .select_from(db.members)
                    .where(db.members.c.workspace_id == invite["workspace_id"])
                )
                or 0
            )
            if count >= allowance:
                raise AccountError(
                    "SEAT_ALLOWANCE_EXHAUSTED", "The workspace has no available seats", 402
                )
            connection.execute(
                db.members.insert().values(
                    workspace_id=invite["workspace_id"],
                    user_id=user["id"],
                    role=invite["role"],
                    created_at=now_utc(),
                )
            )
            connection.execute(
                db.invitations.update()
                .where(db.invitations.c.token_hash == token_digest(raw))
                .values(used_at=now_utc())
            )
            audit_event(
                connection,
                "member_joined",
                user["id"],
                invite["workspace_id"],
                {"role": invite["role"]},
            )
            name = connection.scalar(
                select(db.organisations.c.name).where(
                    db.organisations.c.id == invite["workspace_id"]
                )
            )
            return {"id": invite["workspace_id"], "name": str(name), "role": invite["role"]}

    def workspace_members(self, actor: Principal, offset: int = 0) -> dict[str, Any]:
        actor.require_admin()
        with self.store.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(
                        db.users.c.id.label("user_id"),
                        db.users.c.display_name,
                        db.users.c.email,
                        db.members.c.role,
                    )
                    .join(db.members)
                    .where(db.members.c.workspace_id == actor.workspace_id)
                    .order_by(db.users.c.id)
                    .offset(offset)
                    .limit(101)
                )
                .mappings()
                .all()
            )
        return {
            "members": [dict(row) for row in rows[:100]],
            "next_offset": offset + 100 if len(rows) > 100 else None,
        }

    def change_member(self, actor: Principal, user_id: str, role: str | None) -> None:
        if role is not None and role not in {"ADMIN", "MEMBER", "VIEWER"}:
            raise AccountError("ROLE_FORBIDDEN", "This role cannot be assigned", 403)
        with self.store.transaction() as connection:
            connection.execute(
                select(db.organisations.c.id)
                .where(db.organisations.c.id == actor.workspace_id)
                .with_for_update()
            ).one()
            self._owner(connection, actor)
            target = connection.scalar(
                select(db.members.c.role).where(
                    db.members.c.workspace_id == actor.workspace_id, db.members.c.user_id == user_id
                )
            )
            if target is None:
                raise AccountError("MEMBER_NOT_FOUND", "Member not found", 404)
            if target == "OWNER" or user_id == actor.user_id:
                raise AccountError(
                    "OWNER_PROTECTED", "Workspace ownership cannot be removed here", 409
                )
            statement = (
                db.members.delete() if role is None else db.members.update().values(role=role)
            )
            connection.execute(
                statement.where(
                    db.members.c.workspace_id == actor.workspace_id, db.members.c.user_id == user_id
                )
            )
            audit_event(
                connection,
                "member_removed" if role is None else "role_changed",
                actor.user_id,
                actor.workspace_id,
                {"target_user_id": user_id, "role": role},
            )
