"""Service-authenticated identity API; the Next.js proxy owns browser cookies."""

import re
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from money.accounts.service import AccountError, AccountService, Principal, public_user

router = APIRouter(prefix="/v1")


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmailBody(StrictBody):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def email_address(cls, value: str) -> str:
        result = value.strip().lower()
        if not re.fullmatch(
            r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", result
        ):
            raise ValueError("A valid email address is required")
        local, domain = result.rsplit("@", 1)
        if (
            len(local) > 64
            or local.startswith(".")
            or local.endswith(".")
            or ".." in local
            or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in domain.split(".")
            )
        ):
            raise ValueError("A valid email address is required")
        return result


class PasswordBody(StrictBody):
    password: SecretStr = Field(min_length=15, max_length=128)


class LoginBody(EmailBody):
    password: SecretStr = Field(min_length=1, max_length=128)


class SignupBody(EmailBody, PasswordBody):
    display_name: str = Field(min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$")

    @field_validator("display_name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A display name is required")
        return value.strip()


class TokenBody(StrictBody):
    token: SecretStr = Field(min_length=32, max_length=128)


class ResetBody(TokenBody, PasswordBody):
    pass


class WorkspaceBody(StrictBody):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$")

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A workspace name is required")
        return value.strip()


class PreferencesBody(StrictBody):
    email_notifications: bool


class RoleBody(StrictBody):
    role: Literal["ADMIN", "MEMBER", "VIEWER"]


class InvitationBody(EmailBody, RoleBody):
    pass


class OwnershipBody(PasswordBody):
    user_id: UUID


def accounts(request: Request) -> AccountService:
    if request.app.state.settings.money_auth_mode != "saas":
        raise AccountError("ACCOUNTS_UNAVAILABLE", "Customer accounts are not configured", 503)
    result: AccountService = request.app.state.accounts
    return result


def current_principal(request: Request) -> Principal:
    return accounts(request).principal(
        request.headers.get("X-Money-Session"), request.headers.get("X-Money-Workspace")
    )


def current_user(request: Request) -> dict[str, Any]:
    return accounts(request).authenticated_user(request.headers.get("X-Money-Session"))


Service = Annotated[AccountService, Depends(accounts)]
User = Annotated[dict[str, Any], Depends(current_user)]
Actor = Annotated[Principal, Depends(current_principal)]


@router.post("/account/signup", status_code=202)
def signup(body: SignupBody, service: Service) -> dict[str, Any]:
    service.signup(body.email, body.password.get_secret_value(), body.display_name.strip())
    return {"accepted": True, "message": "Check your email to continue"}


@router.post("/account/login")
def login(body: LoginBody, service: Service) -> dict[str, Any]:
    # This endpoint is not browser-accessible: service authentication and no CORS.
    # Proxy must remove session_token before sending its browser JSON response.
    return service.login(body.email, body.password.get_secret_value())


@router.post("/account/verify-email")
def verify_email(body: TokenBody, service: Service) -> dict[str, bool]:
    service.verify_email(body.token.get_secret_value())
    return {"verified": True}


@router.post("/account/forgot-password", status_code=202)
def forgot_password(body: EmailBody, service: Service) -> dict[str, bool]:
    service.forgot_password(body.email)
    return {"accepted": True}


@router.post("/account/resend-verification", status_code=202)
def resend_verification(body: EmailBody, service: Service) -> dict[str, bool]:
    service.resend_verification(body.email)
    return {"accepted": True}


@router.post("/account/reset-password")
def reset_password(body: ResetBody, service: Service) -> dict[str, bool]:
    service.reset_password(body.token.get_secret_value(), body.password.get_secret_value())
    return {"reset": True}


@router.post("/account/logout")
def logout(body: StrictBody, request: Request, service: Service) -> dict[str, bool]:
    service.logout(request.headers.get("X-Money-Session"))
    return {"signed_out": True}


@router.post("/account/rotate-session")
def rotate(body: StrictBody, request: Request, service: Service) -> dict[str, str]:
    return service.rotate(request.headers.get("X-Money-Session"))


@router.get("/account/sessions")
def sessions(user: User, request: Request, service: Service) -> dict[str, Any]:
    return service.list_sessions(user, request.headers.get("X-Money-Session"))


@router.delete("/account/sessions/{session_id}")
def revoke_session(session_id: str, user: User, service: Service) -> dict[str, bool]:
    if not re.fullmatch(r"[a-f0-9]{64}", session_id):
        raise AccountError("SESSION_NOT_FOUND", "Session not found", 404)
    service.revoke_session(user, session_id)
    return {"revoked": True}


@router.get("/account/me")
def me(user: User, service: Service) -> dict[str, Any]:
    return {"user": public_user(user), "workspaces": service.workspaces(user["id"])}


@router.get("/workspaces")
def workspaces(user: User, service: Service) -> dict[str, Any]:
    return {"workspaces": service.workspaces(user["id"])}


@router.post("/workspaces", status_code=201)
def create_workspace(body: WorkspaceBody, user: User, service: Service) -> dict[str, Any]:
    return {"workspace": service.create_workspace(user, body.name.strip())}


@router.post("/workspaces/invitations", status_code=202)
def invite(body: InvitationBody, actor: Actor, service: Service) -> dict[str, bool]:
    service.invite(actor, body.email, body.role)
    return {"accepted": True}


@router.post("/workspaces/accept-invitation")
def accept_invitation(body: TokenBody, user: User, service: Service) -> dict[str, Any]:
    return {"workspace": service.accept_invitation(user, body.token.get_secret_value())}


@router.get("/workspaces/members")
def members(
    actor: Actor, service: Service, offset: int = Query(default=0, ge=0, le=10000)
) -> dict[str, Any]:
    return service.workspace_members(actor, offset)


@router.patch("/workspaces/members/{user_id}")
def change_member(user_id: UUID, body: RoleBody, actor: Actor, service: Service) -> dict[str, bool]:
    service.change_member(actor, str(user_id), body.role)
    return {"updated": True}


@router.delete("/workspaces/members/{user_id}")
def remove_member(user_id: UUID, actor: Actor, service: Service) -> dict[str, bool]:
    service.change_member(actor, str(user_id), None)
    return {"removed": True}


@router.post("/workspaces/transfer-ownership")
def transfer_ownership(body: OwnershipBody, actor: Actor, service: Service) -> dict[str, bool]:
    service.transfer_ownership(actor, body.password.get_secret_value(), str(body.user_id))
    return {"transferred": True}


@router.get("/account/export")
def export_account(user: User, service: Service) -> dict[str, Any]:
    return service.export(user)


@router.post("/account/deletion", status_code=202)
def delete_account(body: PasswordBody, user: User, service: Service) -> dict[str, bool]:
    service.request_deletion(user, body.password.get_secret_value())
    return {"requested": True}


@router.patch("/account/preferences")
def preferences(body: PreferencesBody, user: User, service: Service) -> dict[str, bool]:
    from money.accounts.models import users
    from money.accounts.service import audit_event

    with service.store.transaction() as connection:
        connection.execute(
            users.update()
            .where(users.c.id == user["id"])
            .values(email_notifications=body.email_notifications)
        )
        audit_event(connection, "notification_preferences_updated", user["id"])
    return {"email_notifications": body.email_notifications}
