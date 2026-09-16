"""Tenant-scoped commercial control endpoints; webhook execution is asynchronous."""

import hashlib
import re
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from money.accounts.api import current_principal
from money.product import models as db
from money.product.billing import BillingUnavailable, authenticate_event
from money.product.service import EntitlementDenied, ProductService
from money.schemas.contracts import ResearchMandate, Ticker, utc_now
from money.storage.models import jobs, queue_control

router = APIRouter(prefix="/v1/product")


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BillingRequest(StrictRequest):
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,64}$")


class CheckoutRequest(BillingRequest):
    plan: str = Field(pattern=r"^(PRO|TEAM|ENTERPRISE)$")


class WatchRequest(StrictRequest):
    ticker: Ticker


class PreferencesRequest(StrictRequest):
    mandate: ResearchMandate = Field(default_factory=ResearchMandate)
    research_email: bool = False


class ProductEventRequest(StrictRequest):
    event: Literal["result_opened", "frontend_error"]
    research_id: UUID | None = None


def service(request: Request) -> ProductService:
    if request.app.state.settings.money_auth_mode != "saas":
        raise HTTPException(404, "Not available")
    return ProductService(request.app.state.store, request.app.state.product_settings)


Product = Annotated[ProductService, Depends(service)]
Actor = Annotated[Any, Depends(current_principal)]


@router.get("/summary")
def summary(product: Product, actor: Actor) -> dict[str, Any]:
    return product.summary(actor)


@router.post("/events", status_code=202)
def record_event(body: ProductEventRequest, product: Product, actor: Actor) -> dict[str, bool]:
    if not product.store.for_workspace(actor.workspace_id).consume_rate_limit(
        "analytics:" + actor.user_id, 30, 60
    )["allowed"]:
        raise HTTPException(429, "Please try again later")
    with product.store.transaction() as connection:
        if body.research_id and not connection.scalar(
            select(jobs.c.id).where(
                jobs.c.id == str(body.research_id), jobs.c.workspace_id == actor.workspace_id
            )
        ):
            raise HTTPException(404, "Research not found")
        product.event(
            connection, actor, body.event, str(body.research_id) if body.research_id else None
        )
    return {"accepted": True}


@router.get("/admin")
def admin(product: Product, actor: Actor) -> dict[str, Any]:
    if actor.user_id not in product.settings.money_internal_admin_user_ids:
        raise HTTPException(403, "Internal administrator access required")
    from money.accounts.models import (
        deletion_requests,
        email_outbox,
        organisations,
        users,
        workspace_closures,
    )
    from money.product.metering import admin_call_summary

    with product.store.transaction() as connection:
        counts = {
            name: connection.scalar(select(func.count()).select_from(table)) or 0
            for name, table in (
                ("users", users),
                ("workspaces", organisations),
                ("research_jobs", jobs),
                ("subscriptions", db.subscriptions),
            )
        }
        subscriptions = [
            dict(row)
            for row in connection.execute(
                select(
                    db.subscriptions.c.plan, db.subscriptions.c.status, func.count().label("count")
                ).group_by(db.subscriptions.c.plan, db.subscriptions.c.status)
            ).mappings()
        ]
        failed = [
            dict(row)
            for row in connection.execute(
                select(
                    jobs.c.id,
                    jobs.c.workspace_id,
                    jobs.c.current_stage,
                    jobs.c.error_code,
                    jobs.c.updated_at,
                )
                .where(jobs.c.status == "FAILED")
                .order_by(jobs.c.updated_at.desc())
                .limit(50)
            ).mappings()
        ]
        billing = {
            name: connection.scalar(
                select(func.count())
                .select_from(db.billing_events)
                .where(db.billing_events.c.state == state)
            )
            or 0
            for name, state in (("pending", "PENDING"), ("failed", "FAILED"))
        }
        email = {
            name: connection.scalar(
                select(func.count()).select_from(email_outbox).where(email_outbox.c.state == state)
            )
            or 0
            for name, state in (("queued", "QUEUED"), ("failed", "FAILED"))
        }
        offboarding = {
            "pending_requests": [
                dict(row)
                for row in connection.execute(
                    select(
                        deletion_requests.c.user_id,
                        deletion_requests.c.state,
                        deletion_requests.c.requested_at,
                        deletion_requests.c.erasure_after,
                    )
                    .where(deletion_requests.c.state != "ERASED")
                    .order_by(deletion_requests.c.requested_at)
                    .limit(50)
                ).mappings()
            ],
            "failed_closures": [
                dict(row)
                for row in connection.execute(
                    select(
                        workspace_closures.c.workspace_id,
                        workspace_closures.c.failure_code,
                        workspace_closures.c.attempts,
                    )
                    .where(workspace_closures.c.state == "FAILED")
                    .order_by(workspace_closures.c.requested_at)
                    .limit(50)
                ).mappings()
            ],
        }
        product.event(connection, actor, "admin_diagnostics_viewed")
    return {
        "counts": counts,
        "subscription_counts": subscriptions,
        "failed_jobs": failed,
        "billing": billing,
        "email": email,
        "offboarding": offboarding,
        "research": product.store.operational_metrics(),
        "call_usage": admin_call_summary(product.store),
        "reference_tables": product.catalog.diagnostics(),
    }


@router.get("/plans")
def plans(product: Product, actor: Actor) -> dict[str, Any]:
    return {
        "plans": [
            product.catalog.plan(name).model_dump()
            for name in ("FREE", "PRO", "TEAM", "ENTERPRISE")
        ]
    }


@router.get("/preferences")
def preferences(product: Product, actor: Actor) -> dict[str, Any]:
    with product.store.engine.connect() as connection:
        value = connection.scalar(
            select(db.preferences.c.payload).where(
                db.preferences.c.workspace_id == actor.workspace_id
            )
        )
    return value or PreferencesRequest().model_dump(mode="json")


@router.put("/preferences")
def set_preferences(body: PreferencesRequest, product: Product, actor: Actor) -> dict[str, Any]:
    if actor.role not in {"OWNER", "ADMIN"}:
        raise EntitlementDenied("ROLE_FORBIDDEN")
    with product.store.transaction() as connection:
        product.subscription(connection, actor.workspace_id)
        exists = connection.execute(
            select(db.preferences).where(db.preferences.c.workspace_id == actor.workspace_id)
        ).first()
        value = body.model_dump(mode="json")
        if exists:
            connection.execute(
                db.preferences.update()
                .where(db.preferences.c.workspace_id == actor.workspace_id)
                .values(payload=value, updated_at=utc_now())
            )
        else:
            connection.execute(
                db.preferences.insert().values(
                    workspace_id=actor.workspace_id, payload=value, updated_at=utc_now()
                )
            )
        product.event(connection, actor, "preferences_updated")
    return value


@router.get("/history")
def history(
    product: Product,
    actor: Actor,
    query: str = Query(default="", max_length=80),
    state: str = Query(default="", pattern=r"^(|QUEUED|RUNNING|COMPLETE|REJECTED|FAILED)$"),
    offset: int = Query(default=0, ge=0, le=100000),
    limit: int = Query(default=25, ge=1, le=100),
    created_from: date | None = None,
    created_to: date | None = None,
    sort: Literal["newest", "oldest"] = "newest",
) -> dict[str, Any]:
    if created_from and created_to and created_from > created_to:
        raise HTTPException(422, "Start date must not be after end date")
    return product.history(actor, query, state, offset, limit, created_from, created_to, sort)


@router.get("/watchlist")
def watchlist(product: Product, actor: Actor) -> dict[str, Any]:
    return {"items": product.list_watch(actor)}


@router.post("/watchlist")
def add_watchlist(body: WatchRequest, product: Product, actor: Actor) -> dict[str, bool]:
    product.watch(actor, body.ticker)
    return {"ok": True}


@router.delete("/watchlist/{ticker}")
def remove_watchlist(ticker: Ticker, product: Product, actor: Actor) -> dict[str, bool]:
    product.watch(actor, ticker, remove=True)
    return {"ok": True}


@router.get("/notifications")
def notifications(product: Product, actor: Actor) -> dict[str, Any]:
    return {"items": product.notifications(actor)}


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: str, product: Product, actor: Actor) -> dict[str, bool]:
    if len(notification_id) > 128:
        raise HTTPException(404, "Notification not found")
    product.mark_read(actor, notification_id)
    return {"ok": True}


@router.post("/billing/checkout")
def checkout(body: CheckoutRequest, product: Product, actor: Actor) -> dict[str, str]:
    return {"url": product.checkout(actor, body.plan, body.idempotency_key)}


@router.post("/billing/portal")
def portal(body: BillingRequest, product: Product, actor: Actor) -> dict[str, str]:
    return {"url": product.checkout(actor, "", body.idempotency_key, portal=True)}


@router.post("/billing/webhook", status_code=202)
async def webhook(request: Request, product: Product) -> dict[str, bool]:
    raw = await request.body()
    try:
        event = authenticate_event(
            raw, request.headers.get("stripe-signature", ""), product.settings
        )
        kind = event.get("type", "")
        obj = event.get("data", {}).get("object", {})
        sub_id = (
            obj.get("id") if kind.startswith("customer.subscription.") else obj.get("subscription")
        )
        # New invoice schemas put the subscription reference under parent.
        if kind.startswith("invoice.") and not sub_id:
            sub_id = (obj.get("parent") or {}).get("subscription_details", {}).get("subscription")
        if sub_id is not None and not re.fullmatch(r"sub_[A-Za-z0-9_]{1,120}", str(sub_id)):
            raise ValueError("INVALID_WEBHOOK")
        accepted = kind in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "checkout.session.completed",
            "invoice.paid",
            "invoice.payment_failed",
        }
        customer_id = obj.get("customer")
        if accepted and (
            not isinstance(customer_id, str)
            or not re.fullmatch(r"cus_[A-Za-z0-9_]{1,120}", customer_id)
        ):
            raise ValueError("INVALID_WEBHOOK")
        if not isinstance(kind, str) or len(kind) > 100:
            raise ValueError("INVALID_WEBHOOK")
    except (ValueError, TypeError, AttributeError) as error:
        raise HTTPException(400, "Invalid billing event") from error
    digest = hashlib.sha256(raw).hexdigest()
    with product.store.transaction() as connection:
        connection.execute(queue_control.update().where(queue_control.c.id == 1).values(id=1))
        existing = (
            connection.execute(
                select(db.billing_events).where(db.billing_events.c.id == event["id"])
            )
            .mappings()
            .first()
        )
        if existing:
            if existing["content_hash"] != digest:
                raise HTTPException(409, "Billing event identity conflict")
        else:
            connection.execute(
                db.billing_events.insert().values(
                    id=event["id"],
                    kind=kind,
                    subscription_id=sub_id,
                    customer_id=customer_id if accepted else None,
                    content_hash=digest,
                    state="PENDING" if accepted and sub_id else "IGNORED",
                    attempts=0,
                    available_at=utc_now(),
                    created_at=utc_now(),
                )
            )
    return {"accepted": True}


def safe_product_error(error: Exception) -> tuple[int, dict[str, str]]:
    if isinstance(error, BillingUnavailable):
        return 503, {
            "code": "BILLING_UNAVAILABLE",
            "detail": "Billing is temporarily unavailable. Your workspace is unchanged.",
        }
    code = str(error) if isinstance(error, EntitlementDenied) else "PRODUCT_UNAVAILABLE"
    status = 403 if code == "ROLE_FORBIDDEN" else 404 if code == "RESOURCE_NOT_FOUND" else 402
    return status, {
        "code": code,
        "detail": "Your current workspace permissions or allowance do not permit this action.",
    }
