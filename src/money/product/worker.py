"""Reconcile durable billing events outside web requests with bounded attempts."""

import argparse
import logging
import signal
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from money.accounts.service import AccountService
from money.api.observability import configure_logging
from money.api.settings import Settings
from money.product import models as db
from money.product.billing import StripeGateway
from money.product.notifications import notify_research_once, notify_subscription
from money.product.service import ProductService
from money.schemas.contracts import utc_now
from money.storage import ResearchStore

logger = logging.getLogger(__name__)


def reconcile_once(
    product: ProductService,
    gateway: StripeGateway | None = None,
    accounts: AccountService | None = None,
) -> bool:
    gateway = gateway or StripeGateway(product.settings)
    event_id = None
    try:
        with product.store.transaction() as connection:
            event = (
                connection.execute(
                    select(db.billing_events)
                    .where(
                        db.billing_events.c.state == "PENDING",
                        db.billing_events.c.available_at <= utc_now(),
                    )
                    .order_by(db.billing_events.c.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if not event:
                return False
            event_id = event["id"]
            # Current provider state, NOT historical webhook status. Serialize reconciliation
            # before fetching so parallel workers cannot overwrite newer observations.
            row = (
                connection.execute(
                    select(db.subscriptions)
                    .where(db.subscriptions.c.customer_id == event["customer_id"])
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if not row:
                raise ValueError("BILLING_CUSTOMER_UNKNOWN")
            subscription = gateway.request("GET", "/subscriptions/" + event["subscription_id"])
            if (
                subscription.get("id") != event["subscription_id"]
                or subscription.get("customer") != event["customer_id"]
                or subscription.get("livemode") is not product.settings.money_stripe_live
            ):
                raise ValueError("BILLING_IDENTITY_MISMATCH")
            workspace = row["workspace_id"]
            if subscription.get("metadata", {}).get("workspace_id") != workspace:
                raise ValueError("BILLING_WORKSPACE_MISMATCH")
            if (
                row["subscription_id"]
                and row["subscription_id"] != subscription["id"]
                and row["status"] not in {"canceled", "incomplete_expired"}
            ):
                raise ValueError("BILLING_DUPLICATE_SUBSCRIPTION")
            items = subscription.get("items", {}).get("data", [])
            if len(items) != 1 or items[0].get("quantity") != 1:
                raise ValueError("BILLING_ITEMS_UNSUPPORTED")
            price = items[0].get("price", {}).get("id")
            plan = next(
                (
                    name
                    for name, value in product.settings.money_stripe_prices.items()
                    if value == price
                ),
                None,
            )
            if plan is None:
                raise ValueError("BILLING_PRICE_UNKNOWN")
            start = subscription.get("current_period_start", items[0].get("current_period_start"))
            end = subscription.get("current_period_end", items[0].get("current_period_end"))
            start_at, end_at = datetime.fromtimestamp(start, UTC), datetime.fromtimestamp(end, UTC)
            if end_at <= start_at or end_at - start_at > timedelta(days=370):
                raise ValueError("BILLING_PERIOD_INVALID")
            status = subscription.get("status")
            if status not in {
                "active",
                "trialing",
                "past_due",
                "unpaid",
                "paused",
                "canceled",
                "incomplete",
                "incomplete_expired",
            }:
                raise ValueError("BILLING_STATUS_UNKNOWN")
            connection.execute(
                db.subscriptions.update()
                .where(db.subscriptions.c.workspace_id == workspace)
                .values(
                    plan=plan,
                    status=status,
                    subscription_id=subscription["id"],
                    period_start=start_at,
                    period_end=end_at,
                    updated_at=utc_now(),
                )
            )
            connection.execute(
                db.billing_events.update()
                .where(db.billing_events.c.id == event_id)
                .values(state="PROCESSED", processed_at=utc_now(), attempts=event["attempts"] + 1)
            )
            product.event(
                connection,
                SimpleNamespace(workspace_id=workspace, user_id="billing-provider"),
                "subscription_reconciled",
                event_id,
            )
            if accounts is not None:
                notify_subscription(connection, accounts, workspace, status)
            return True
    except Exception as error:  # safe worker boundary, no provider payloads logged
        logger.error("billing_reconciliation_failed", extra={"failure_class": type(error).__name__})
        if event_id:
            with product.store.transaction() as connection:
                row = (
                    connection.execute(
                        select(db.billing_events)
                        .where(db.billing_events.c.id == event_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one()
                )
                if row["state"] == "PENDING":
                    attempts = row["attempts"] + 1
                    connection.execute(
                        db.billing_events.update()
                        .where(db.billing_events.c.id == event_id)
                        .values(
                            attempts=attempts,
                            state="FAILED" if attempts >= 5 else "PENDING",
                            available_at=utc_now() + timedelta(seconds=min(3600, 30 * 2**attempts)),
                        )
                    )
        return event_id is not None


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Money billing reconciliation worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    settings = Settings()  # type: ignore[call-arg]
    store = ResearchStore(
        settings.database_url.get_secret_value(),
        allow_sqlite=settings.money_env in {"test", "development"},
    )
    if args.healthcheck:
        try:
            healthy = store.health("commercial-services")["worker"] == "ready"
        except Exception:  # Read-only health boundary; no connection strings in output.
            healthy = False
        finally:
            store.engine.dispose()
        raise SystemExit(0 if healthy else 1)
    product = ProductService(store)
    accounts = AccountService(store, settings) if settings.money_auth_mode == "saas" else None
    stop = threading.Event()
    worker_id = "commercial-" + uuid4().hex

    def shutdown(signum: int, frame: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        while not stop.is_set():
            handled = False
            try:
                store.heartbeat(worker_id, "commercial-services")
                handled = reconcile_once(product, accounts=accounts)
                if settings.money_auth_mode == "saas":
                    handled = notify_research_once(store, settings) or handled
                    from money.accounts.offboarding import close_billing_once
                    from money.product.billing import StripeGateway

                    handled = close_billing_once(store, StripeGateway(product.settings)) or handled
            except Exception as error:
                logger.error(
                    "commercial_worker_unavailable", extra={"failure_class": type(error).__name__}
                )
            if args.once:
                break
            if not handled:
                stop.wait(5)
    finally:
        try:
            store.heartbeat(worker_id, "commercial-services", healthy=False)
        finally:
            store.engine.dispose()


if __name__ == "__main__":
    main()
