"""Container worker: claims persisted jobs and computes outside HTTP requests."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import signal
import threading
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from money.api.errors import classify_failure
from money.api.observability import configure_logging, failure_context
from money.api.settings import Settings
from money.storage import Claim, LeaseLost, ResearchStore

logger = logging.getLogger("money.worker")


def run_once(
    store: ResearchStore,
    runtime: Any,
    *,
    worker_id: str,
    mode: str,
    lease_seconds: int = 120,
    job_timeout_seconds: int = 1800,
    runner: Callable[[str, ResearchStore, Any], None] | None = None,
    claimed: Claim | None = None,
) -> bool:
    """Execute at most one job; the API never calls this operation."""
    if runner is None:
        if mode == "live_rnd":
            from money.research.rnd import run_rnd

            runner = run_rnd
        else:
            from money.flows.research import run_research

            runner = run_research
    store.heartbeat(worker_id, mode)
    claim = claimed or store.claim_job(
        worker_id, lease_seconds=lease_seconds, job_timeout_seconds=job_timeout_seconds,
        research_kind="live_rnd" if mode == "live_rnd" else "standard",
    )
    if claim is None:
        return False
    owned = store.for_claim(claim)
    stop = threading.Event()
    heartbeat = threading.Thread(
        target=_renew,
        args=(owned, claim, mode, lease_seconds, stop),
        daemon=True,
    )
    heartbeat.start()
    try:
        runner(claim.job_id, owned, runtime)
        result = owned.get_job(claim.job_id)
        if result and result["status"] not in {"COMPLETE", "REJECTED", "FAILED", "QUEUED"}:
            owned.fail_job(
                claim.job_id, "WORKER_INCOMPLETE", "Research did not reach a final state."
            )
    except LeaseLost:
        logger.warning("Research worker lost its lease for job %s", claim.job_id)
    except Exception as error:  # noqa: BLE001 - isolate failures at the job boundary
        # Do not put provider errors, prompts, credentials or connection URLs in responses.
        failure = classify_failure(error)
        logger.error(
            "research_failure",
            extra={
                "research_id": claim.job_id,
                "failure_class": failure.code,
                "retryable": failure.retryable,
                **failure_context(error),
            },
        )
        try:
            if failure.retryable:
                owned.retry_job(claim.job_id, failure.code)
            else:
                owned.fail_job(claim.job_id, failure.code, failure.message)
        except LeaseLost:
            pass
    finally:
        stop.set()
        heartbeat.join(timeout=5)
    return True


def _renew(
    store: ResearchStore, claim: Claim, mode: str, seconds: int, stop: threading.Event
) -> None:
    while not stop.wait(min(seconds / 3, 30)):
        try:
            store.renew_lease(claim.job_id, seconds)
            store.heartbeat(claim.worker_id, mode)
        except Exception as error:  # noqa: BLE001 - heartbeat failure must fence computation
            logger.error("Worker heartbeat stopped: %s", type(error).__name__)
            return


def _compute_process(settings: Settings, claim: Claim) -> None:
    """A fresh process contains each research run and can be killed at its deadline."""
    if os.name == "posix":
        os.setsid()
    configure_logging()
    store = ResearchStore(
        settings.database_url.get_secret_value(),
        allow_sqlite=settings.money_env in {"development", "test"},
    )
    try:
        from money.flows.research import build_runtime

        try:
            job = store.get_job(claim.job_id)
            runtime: Any
            selected_mode = settings.money_research_mode
            if settings.money_auth_mode == "saas" and job and job["ticker"] == "DEMO.L":
                if (
                    settings.money_env not in {"development", "test"}
                    or not settings.money_enable_synthetic_demo
                ):
                    raise ValueError("SYNTHETIC_DEMO_DISABLED")
                selected_mode = "demo"
            if selected_mode == "live_rnd":
                from money.research.rnd import build_rnd_runtime

                runtime = build_rnd_runtime(settings, store.for_claim(claim))
            else:
                runtime = build_runtime(
                    selected_mode,
                    store=store.for_claim(claim),
                    live_manifest=settings.money_live_manifest,
                    manifest_hash=settings.money_live_manifest_sha256,
                )
        except Exception as error:
            logger.error(
                "research_configuration_failed",
                extra={"research_id": claim.job_id, **failure_context(error)},
            )
            store.for_claim(claim).fail_job(
                claim.job_id,
                "LIVE_CONFIGURATION_UNAVAILABLE",
                "Qualified research configuration is unavailable; no signal was issued.",
            )
            return
        run_once(
            store,
            runtime,
            worker_id=claim.worker_id,
            mode=settings.money_research_mode,
            lease_seconds=settings.money_worker_lease_seconds,
            job_timeout_seconds=settings.money_job_timeout_seconds,
            claimed=claim,
        )
    finally:
        store.engine.dispose()


def supervise_job(
    store: ResearchStore, settings: Settings, claim: Claim, stop: threading.Event
) -> None:
    """Bound whole-job wall time, including hangs in upstream threads/native libraries."""
    from sqlalchemy import select

    from money.product.models import usage_records

    with store.engine.connect() as connection:
        allowance = connection.scalar(
            select(usage_records.c.max_seconds).where(usage_records.c.job_id == claim.job_id)
        )
    # Resolve the durable allowance before any child can execute paid work. A
    # database failure must not leave a child outside the cleanup boundary.
    process = multiprocessing.get_context("spawn").Process(
        target=_compute_process, args=(settings, claim), name="money-research-job"
    )
    deadline = time.monotonic() + min(
        settings.money_job_timeout_seconds, allowance or settings.money_job_timeout_seconds
    )
    try:
        process.start()
        while process.is_alive() and not stop.is_set() and time.monotonic() < deadline:
            process.join(timeout=1)
        if process.is_alive():
            if os.name == "posix" and process.pid is not None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    process.terminate()
            else:
                process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                if os.name == "posix" and process.pid is not None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        process.kill()
                else:
                    process.kill()
                process.join(timeout=5)
        result = store.get_job(claim.job_id)
        if result and result["status"] not in {"COMPLETE", "REJECTED", "FAILED", "QUEUED"}:
            try:
                store.for_claim(claim).retry_job(claim.job_id, "WORKER_INTERRUPTED")
            except LeaseLost:
                # Read-time deadline/lease recovery is authoritative if the child lost ownership.
                store.get_job(claim.job_id)
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        process.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Money research compute worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    parser.add_argument(
        "--healthcheck", action="store_true", help="Check durable worker service heartbeat"
    )
    args = parser.parse_args()
    configure_logging()
    settings = Settings()  # type: ignore[call-arg]
    store = ResearchStore(
        settings.database_url.get_secret_value(),
        allow_sqlite=settings.money_env in {"development", "test"},
    )
    if args.healthcheck:
        try:
            healthy = store.health(settings.money_research_mode)["worker"] == "ready"
        except Exception:  # noqa: BLE001 - health checks must never print credentials
            healthy = False
        finally:
            store.engine.dispose()
        raise SystemExit(0 if healthy else 1)
    worker_id = f"money-worker-{uuid4()}"
    stop = threading.Event()

    def shutdown(signum: int, frame: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        while not stop.is_set():
            try:
                store.heartbeat(worker_id, settings.money_research_mode)
                claim = store.claim_job(
                    worker_id,
                    lease_seconds=settings.money_worker_lease_seconds,
                    job_timeout_seconds=settings.money_job_timeout_seconds,
                    research_kind=(
                        "live_rnd" if settings.money_research_mode == "live_rnd" else "standard"
                    ),
                )
                handled = claim is not None
                if claim is not None:
                    supervise_job(store, settings, claim, stop)
            except Exception as error:  # noqa: BLE001 - keep worker alive during database recovery
                logger.error("Worker queue unavailable: %s", type(error).__name__)
                handled = False
            if args.once:
                break
            if not handled:
                stop.wait(settings.money_worker_poll_seconds)
    finally:
        try:
            store.heartbeat(worker_id, settings.money_research_mode, healthy=False)
        finally:
            store.engine.dispose()


if __name__ == "__main__":
    main()
