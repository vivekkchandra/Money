"""Transactional customer entitlements and paginated tenant-owned product records."""

from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import Connection, func, or_, select

from money.product import models as db
from money.product.billing import BillingUnavailable, StripeGateway
from money.product.settings import ProductSettings
from money.reference import load_reference_catalog
from money.schemas.contracts import utc_now
from money.storage import ResearchStore
from money.storage.models import jobs
from money.storage.production_models import alert_outbox
from money.storage.store import aware, serialize


class EntitlementDenied(ValueError):
    """No expensive work was admitted."""


def month_window(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, (start + timedelta(days=32)).replace(day=1)


class ProductService:
    @staticmethod
    def require_current_role(connection: Connection, actor: Any, roles: set[str]) -> None:
        """Recheck durable authority under the workspace lock, never a cached role."""
        from money.accounts import models as accounts

        # Subscription -> workspace is shared with seat/member changes. Identity
        # is read, not locked, to avoid inversion with deletion's user -> sub lock.
        workspace = connection.scalar(
            select(accounts.organisations.c.id)
            .where(accounts.organisations.c.id == actor.workspace_id)
            .with_for_update()
        )
        role = connection.scalar(
            select(accounts.members.c.role)
            .join(accounts.users)
            .where(
                accounts.members.c.workspace_id == actor.workspace_id,
                accounts.members.c.user_id == actor.user_id,
                accounts.users.c.deletion_requested_at.is_(None),
                accounts.users.c.email_verified_at.is_not(None),
            )
        )
        if workspace is None or role not in roles:
            raise EntitlementDenied("ROLE_FORBIDDEN")

    def __init__(self, store: ResearchStore, settings: ProductSettings | None = None) -> None:
        self.store = store
        self.settings = settings or ProductSettings()
        self.catalog = load_reference_catalog()

    @staticmethod
    def event(connection: Connection, actor: Any, name: str, reference: str | None = None) -> None:
        connection.execute(
            db.product_events.insert().values(
                id=str(uuid4()),
                workspace_id=actor.workspace_id,
                user_id=actor.user_id,
                event=name,
                reference_id=reference,
                created_at=utc_now(),
            )
        )

    def subscription(self, connection: Connection, workspace: str) -> dict[str, Any]:
        row = (
            connection.execute(
                select(db.subscriptions)
                .where(db.subscriptions.c.workspace_id == workspace)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        now = utc_now()
        if row is None:
            start, end = month_window(now)
            values = dict(
                workspace_id=workspace,
                plan="FREE",
                status="active",
                period_start=start,
                period_end=end,
                updated_at=now,
            )
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            insert = sqlite_insert if connection.dialect.name == "sqlite" else pg_insert
            connection.execute(
                insert(db.subscriptions)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["workspace_id"])
            )
            row = (
                connection.execute(
                    select(db.subscriptions)
                    .where(db.subscriptions.c.workspace_id == workspace)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
        result = dict(row)
        if result["plan"] == "FREE" and aware(result["period_end"]) <= now:
            start, end = month_window(now)
            result.update(period_start=start, period_end=end, updated_at=now)
            connection.execute(
                db.subscriptions.update()
                .where(db.subscriptions.c.workspace_id == workspace)
                .values(period_start=start, period_end=end, updated_at=now)
            )
        return result

    def reserve_research(
        self, connection: Connection, job_id: str, actor: Any, ticker: str
    ) -> None:
        if actor.role not in {"OWNER", "ADMIN", "MEMBER"}:
            raise EntitlementDenied("ROLE_FORBIDDEN")
        sub = self.subscription(connection, actor.workspace_id)
        from money.accounts.offboarding import assert_workspace_open

        assert_workspace_open(connection, actor.workspace_id)
        self.require_current_role(connection, actor, {"OWNER", "ADMIN", "MEMBER"})
        if sub.get("customer_id") and connection.scalar(
            select(db.billing_events.c.id)
            .where(
                db.billing_events.c.customer_id == sub["customer_id"],
                db.billing_events.c.state.in_(["PENDING", "FAILED"]),
            )
            .limit(1)
        ):
            raise EntitlementDenied("BILLING_RECONCILIATION_REQUIRED")
        if sub["status"] not in {"active", "trialing"} or aware(sub["period_end"]) <= utc_now():
            raise EntitlementDenied("SUBSCRIPTION_INACTIVE")
        plan = self.catalog.plan(sub["plan"])
        if ticker != "DEMO.L" and not plan.live_research:
            raise EntitlementDenied("PLAN_LIVE_RESEARCH_UNAVAILABLE")
        used = (
            connection.scalar(
                select(func.count())
                .select_from(db.usage_records)
                .where(
                    db.usage_records.c.workspace_id == actor.workspace_id,
                    db.usage_records.c.period_start == sub["period_start"],
                )
            )
            or 0
        )
        if used >= plan.research_jobs_per_period:
            raise EntitlementDenied("RESEARCH_ALLOWANCE_EXHAUSTED")
        # create_job holds the queue admission lock for this transaction, so an
        # account cannot race requests across workspaces to exceed this ceiling.
        user_used = self.user_usage(connection, actor.user_id)
        if user_used >= self.settings.money_user_jobs_per_month:
            raise EntitlementDenied("ACCOUNT_RESEARCH_ALLOWANCE_EXHAUSTED")
        connection.execute(
            db.usage_records.insert().values(
                job_id=job_id,
                workspace_id=actor.workspace_id,
                user_id=actor.user_id,
                period_start=sub["period_start"],
                period_end=sub["period_end"],
                plan=sub["plan"],
                max_tokens=0 if ticker == "DEMO.L" else plan.max_job_tokens,
                max_seconds=plan.max_job_seconds,
                data_version=sha256(str(self.catalog.diagnostics()).encode()).hexdigest(),
                created_at=utc_now(),
            )
        )
        self.event(connection, actor, "research_requested", job_id)

    @staticmethod
    def user_usage(connection: Connection, user_id: str) -> int:
        start, end = month_window(utc_now())
        return int(
            connection.scalar(
                select(func.count())
                .select_from(db.usage_records)
                .where(
                    db.usage_records.c.user_id == user_id,
                    db.usage_records.c.created_at >= start,
                    db.usage_records.c.created_at < end,
                )
            )
            or 0
        )

    def summary(self, actor: Any) -> dict[str, Any]:
        with self.store.transaction() as connection:
            sub = self.subscription(connection, actor.workspace_id)
            plan = self.catalog.plan(sub["plan"])
            used = (
                connection.scalar(
                    select(func.count())
                    .select_from(db.usage_records)
                    .where(
                        db.usage_records.c.workspace_id == actor.workspace_id,
                        db.usage_records.c.period_start == sub["period_start"],
                    )
                )
                or 0
            )
            return {
                "plan": sub["plan"],
                "status": sub["status"],
                "used": used,
                "allowance": plan.research_jobs_per_period,
                "remaining": max(0, plan.research_jobs_per_period - used),
                "period_start": aware(sub["period_start"]).isoformat(),
                "period_end": aware(sub["period_end"]).isoformat(),
                "billing_available": self.settings.money_billing_enabled,
                "entitlements": plan.model_dump(),
                "cost_status": "unknown",
                "account_monthly_limit": self.settings.money_user_jobs_per_month,
                "account_monthly_used": self.user_usage(connection, actor.user_id),
            }

    def history(
        self,
        actor: Any,
        query: str = "",
        state: str = "",
        offset: int = 0,
        limit: int = 25,
        created_from: date | None = None,
        created_to: date | None = None,
        sort: Literal["newest", "oldest"] = "newest",
    ) -> dict[str, Any]:
        if (
            not 1 <= limit <= 100
            or not 0 <= offset <= 100000
            or sort not in {"newest", "oldest"}
            or (created_from and created_to and created_from > created_to)
        ):
            raise ValueError("INVALID_HISTORY_RANGE")
        statement = select(jobs).where(jobs.c.workspace_id == actor.workspace_id)
        if query:
            statement = statement.where(
                or_(jobs.c.ticker.contains(query.upper(), autoescape=True), jobs.c.id == query)
            )
        if state:
            statement = statement.where(jobs.c.status == state)
        if created_from:
            statement = statement.where(
                jobs.c.created_at >= datetime.combine(created_from, time.min, UTC)
            )
        if created_to:
            statement = statement.where(
                jobs.c.created_at <= datetime.combine(created_to, time.max, UTC)
            )
        ordering = jobs.c.created_at.desc() if sort == "newest" else jobs.c.created_at.asc()
        with self.store.engine.connect() as connection:
            rows = (
                connection.execute(
                    statement.order_by(ordering, jobs.c.id).offset(offset).limit(limit + 1)
                )
                .mappings()
                .all()
            )
        public = [
            {
                key: value
                for key, value in serialize(row).items()
                if key not in {"lease_token", "worker_id", "lease_until", "request_hash"}
            }
            for row in rows[:limit]
        ]
        return {"jobs": public, "next_offset": offset + limit if len(rows) > limit else None}

    def watch(self, actor: Any, ticker: str, remove: bool = False) -> None:
        if actor.role == "VIEWER":
            raise EntitlementDenied("ROLE_FORBIDDEN")
        with self.store.transaction() as connection:
            self.subscription(connection, actor.workspace_id)
            predicate = (db.watchlist.c.workspace_id == actor.workspace_id) & (
                db.watchlist.c.ticker == ticker
            )
            if remove:
                connection.execute(db.watchlist.delete().where(predicate))
            elif not connection.execute(select(db.watchlist).where(predicate)).first():
                count = (
                    connection.scalar(
                        select(func.count())
                        .select_from(db.watchlist)
                        .where(db.watchlist.c.workspace_id == actor.workspace_id)
                    )
                    or 0
                )
                if count >= 200:
                    raise EntitlementDenied("WATCHLIST_LIMIT")
                connection.execute(
                    db.watchlist.insert().values(
                        workspace_id=actor.workspace_id,
                        ticker=ticker,
                        created_by=actor.user_id,
                        created_at=utc_now(),
                    )
                )
            self.event(connection, actor, "watchlist_removed" if remove else "watchlist_added")

    def list_watch(self, actor: Any) -> list[dict[str, Any]]:
        with self.store.engine.connect() as connection:
            return [
                serialize(row)
                for row in connection.execute(
                    select(db.watchlist)
                    .where(db.watchlist.c.workspace_id == actor.workspace_id)
                    .order_by(db.watchlist.c.ticker)
                    .limit(200)
                ).mappings()
            ]

    def notifications(self, actor: Any) -> list[dict[str, Any]]:
        with self.store.engine.connect() as connection:
            rows = connection.execute(
                select(
                    alert_outbox.c.id,
                    alert_outbox.c.job_id,
                    alert_outbox.c.created_at,
                    db.notification_reads.c.read_at,
                )
                .outerjoin(
                    db.notification_reads,
                    (db.notification_reads.c.notification_id == alert_outbox.c.id)
                    & (db.notification_reads.c.user_id == actor.user_id)
                    & (db.notification_reads.c.workspace_id == actor.workspace_id),
                )
                .where(
                    alert_outbox.c.workspace_id == actor.workspace_id,
                    alert_outbox.c.channel == "web",
                )
                .order_by(alert_outbox.c.created_at.desc())
                .limit(100)
            ).mappings()
            return [
                {**serialize(row), "message": "Your research record is ready to review."}
                for row in rows
            ]

    def mark_read(self, actor: Any, notification_id: str) -> None:
        with self.store.transaction() as connection:
            self.subscription(connection, actor.workspace_id)
            exists = connection.execute(
                select(alert_outbox.c.id).where(
                    alert_outbox.c.id == notification_id,
                    alert_outbox.c.workspace_id == actor.workspace_id,
                    alert_outbox.c.channel == "web",
                )
            ).first()
            if not exists:
                raise EntitlementDenied("RESOURCE_NOT_FOUND")
            found = connection.execute(
                select(db.notification_reads).where(
                    db.notification_reads.c.user_id == actor.user_id,
                    db.notification_reads.c.notification_id == notification_id,
                )
            ).first()
            if not found:
                connection.execute(
                    db.notification_reads.insert().values(
                        user_id=actor.user_id,
                        workspace_id=actor.workspace_id,
                        notification_id=notification_id,
                        read_at=utc_now(),
                    )
                )

    def checkout(self, actor: Any, plan: str, key: str, *, portal: bool = False) -> str:
        if actor.role != "OWNER":
            raise EntitlementDenied("ROLE_FORBIDDEN")
        if not self.settings.money_billing_enabled:
            raise BillingUnavailable("BILLING_UNAVAILABLE")
        if not self.store.for_workspace(actor.workspace_id).consume_rate_limit(
            "billing:" + actor.user_id, 6, 60
        )["allowed"]:
            raise EntitlementDenied("BILLING_RATE_LIMITED")
        gateway = StripeGateway(self.settings)
        with self.store.transaction() as connection:
            sub = self.subscription(connection, actor.workspace_id)
            from money.accounts.offboarding import assert_workspace_open

            assert_workspace_open(connection, actor.workspace_id)
            self.require_current_role(connection, actor, {"OWNER"})
            customer = sub.get("customer_id")
            if not customer:
                result = gateway.request(
                    "POST",
                    "/customers",
                    {
                        "metadata[workspace_id]": actor.workspace_id,
                    },
                    f"money-customer-{actor.workspace_id}",
                )
                customer = result["id"]
                if not isinstance(customer, str) or not customer.startswith("cus_"):
                    raise BillingUnavailable("BILLING_RESPONSE_INVALID")
                connection.execute(
                    db.subscriptions.update()
                    .where(db.subscriptions.c.workspace_id == actor.workspace_id)
                    .values(customer_id=customer)
                )
        # Persist customer identity before checkout: a webhook may arrive immediately.
        origin = self.settings.money_public_web_url.rstrip("/")
        if portal:
            with self.store.transaction() as connection:
                self.subscription(connection, actor.workspace_id)
                assert_workspace_open(connection, actor.workspace_id)
                self.require_current_role(connection, actor, {"OWNER"})
                result = gateway.request(
                    "POST",
                    "/billing_portal/sessions",
                    {"customer": customer, "return_url": origin + "/account"},
                    f"money-portal-{actor.workspace_id}-{key}",
                )
                return gateway.hosted_url(result, "billing.stripe.com")
        price = self.settings.money_stripe_prices.get(plan)
        if not price:
            raise BillingUnavailable("PLAN_NOT_AVAILABLE")
        with self.store.transaction() as connection:
            sub = self.subscription(connection, actor.workspace_id)
            assert_workspace_open(connection, actor.workspace_id)
            self.require_current_role(connection, actor, {"OWNER"})
            if sub.get("subscription_id") and sub["status"] not in {
                "canceled",
                "incomplete_expired",
            }:
                raise EntitlementDenied("USE_BILLING_PORTAL")
            if sub.get("checkout_until") and aware(sub["checkout_until"]) > utc_now():
                if sub["checkout_plan"] != plan:
                    raise EntitlementDenied("CHECKOUT_ALREADY_PENDING")
                if sub["checkout_url"]:
                    return str(sub["checkout_url"])
                key, until = sub["checkout_key"], aware(sub["checkout_until"])
                parameters = sub["checkout_payload"]
            else:
                if sub.get("checkout_key") == key:
                    raise EntitlementDenied("CHECKOUT_EXPIRED_USE_NEW_KEY")
                until = utc_now() + timedelta(minutes=31)
                parameters = {
                    "mode": "subscription",
                    "customer": customer,
                    "line_items[0][price]": price,
                    "line_items[0][quantity]": "1",
                    "client_reference_id": actor.workspace_id,
                    "subscription_data[metadata][workspace_id]": actor.workspace_id,
                    "success_url": origin + "/account?billing=pending",
                    "cancel_url": origin + "/pricing",
                    "expires_at": str(int(until.timestamp())),
                }
                connection.execute(
                    db.subscriptions.update()
                    .where(db.subscriptions.c.workspace_id == actor.workspace_id)
                    .values(
                        checkout_key=key,
                        checkout_plan=plan,
                        checkout_until=until,
                        checkout_url=None,
                        checkout_payload=parameters,
                    )
                )
        # A lost response can now retry exactly the same key AND parameters.
        result = gateway.request(
            "POST", "/checkout/sessions", parameters, f"money-checkout-{actor.workspace_id}-{key}"
        )
        url = gateway.hosted_url(result, "checkout.stripe.com")
        with self.store.transaction() as connection:
            sub = self.subscription(connection, actor.workspace_id)
            if sub["checkout_key"] != key or sub["checkout_plan"] != plan:
                raise EntitlementDenied("CHECKOUT_CHANGED")
            connection.execute(
                db.subscriptions.update()
                .where(db.subscriptions.c.workspace_id == actor.workspace_id)
                .values(
                    checkout_key=key, checkout_plan=plan, checkout_url=url, checkout_until=until
                )
            )
            self.event(connection, actor, "checkout_created")
            return url
