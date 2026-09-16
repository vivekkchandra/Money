"""Terminal research notifications from durable state, outside the research transaction."""

from types import SimpleNamespace

from sqlalchemy import Connection, exists, select

from money.accounts.models import members, users
from money.accounts.service import AccountService
from money.api.settings import Settings
from money.product.models import preferences, product_events, usage_records
from money.product.service import ProductService
from money.storage import ResearchStore
from money.storage.models import jobs, queue_control


def notify_subscription(
    connection: Connection, accounts: AccountService, workspace_id: str, status: str
) -> None:
    """Email verified workspace owners only; the billing event transaction deduplicates."""
    recipients = (
        connection.execute(
            select(users.c.id, users.c.email)
            .join(members, members.c.user_id == users.c.id)
            .where(
                members.c.workspace_id == workspace_id,
                members.c.role == "OWNER",
                users.c.email_notifications.is_(True),
                users.c.deletion_requested_at.is_(None),
                users.c.email_verified_at.is_not(None),
            )
        )
        .mappings()
        .all()
    )
    issue = status in {"past_due", "unpaid", "incomplete"}
    subject = "Action needed for your Money subscription" if issue else "Money subscription updated"
    message = (
        "Please review your billing account."
        if issue
        else "Your workspace subscription has changed."
    )
    for recipient in recipients:
        accounts._email(
            connection,
            recipient["id"],
            recipient["email"],
            "subscription",
            subject,
            message + "\n" + accounts.settings.money_public_web_url.rstrip("/") + "/account",
        )


def notify_research_once(store: ResearchStore, settings: Settings) -> bool:
    """Atomically enqueue email and audit once. In-app completion is already atomic."""
    accounts = AccountService(store, settings)
    with store.transaction() as connection:
        connection.execute(queue_control.update().where(queue_control.c.id == 1).values(id=1))
        row = (
            connection.execute(
                select(
                    jobs.c.id,
                    jobs.c.workspace_id,
                    jobs.c.status,
                    usage_records.c.user_id,
                    users.c.email,
                    users.c.email_notifications,
                    users.c.deletion_requested_at,
                )
                .join(usage_records, usage_records.c.job_id == jobs.c.id)
                .join(users, users.c.id == usage_records.c.user_id)
                .where(
                    jobs.c.status.in_(["COMPLETE", "REJECTED", "FAILED"]),
                    ~exists(
                        select(product_events.c.id).where(
                            product_events.c.reference_id == jobs.c.id,
                            product_events.c.event.in_(["research_completed", "research_failed"]),
                        )
                    ),
                )
                .order_by(jobs.c.completed_at)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if not row:
            return False
        success = row["status"] != "FAILED"
        actor = SimpleNamespace(workspace_id=row["workspace_id"], user_id=row["user_id"])
        ProductService.event(
            connection, actor, "research_completed" if success else "research_failed", row["id"]
        )
        preference = (
            connection.scalar(
                select(preferences.c.payload).where(
                    preferences.c.workspace_id == row["workspace_id"]
                )
            )
            or {}
        )
        if (
            row["email_notifications"]
            and not row["deletion_requested_at"]
            and preference.get("research_email") is True
        ):
            message = (
                "Your research record is ready to review."
                if success
                else "Your research could not be completed. Your request remains saved."
            )
            accounts._email(
                connection,
                row["user_id"],
                row["email"],
                "research_status",
                "Money research update",
                message + "\n" + settings.money_public_web_url.rstrip("/") + "/research",
            )
        return True
