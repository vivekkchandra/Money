"""Container worker: claims persisted jobs and computes outside HTTP requests."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from money.api.settings import Settings
from money.storage import Claim, LeaseLost, ResearchStore

logger = logging.getLogger(__name__)


def run_once(
    store: ResearchStore,
    runtime: Any,
    *,
    worker_id: str,
    mode: str,
    lease_seconds: int = 120,
    runner: Callable[[str, ResearchStore, Any], None] | None = None,
) -> bool:
    """Execute at most one job; the API never calls this operation."""
    if runner is None:
        from money.flows.research import run_research

        runner = run_research
    store.heartbeat(worker_id, mode)
    claim = store.claim_job(worker_id, lease_seconds=lease_seconds)
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
        if result and result["status"] not in {"COMPLETE", "REJECTED", "FAILED"}:
            owned.fail_job(
                claim.job_id, "WORKER_INCOMPLETE", "Research did not reach a final state."
            )
    except LeaseLost:
        logger.warning("Research worker lost its lease for job %s", claim.job_id)
    except Exception as error:  # noqa: BLE001 - isolate failures at the job boundary
        # Do not put provider errors, prompts, credentials or connection URLs in responses.
        logger.error("Research failed for job %s: %s", claim.job_id, type(error).__name__)
        try:
            owned.fail_job(
                claim.job_id, "RESEARCH_FAILED", "Research could not be completed safely."
            )
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Money research compute worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = Settings()  # type: ignore[call-arg]
    store = ResearchStore(
        settings.database_url.get_secret_value(),
        allow_sqlite=settings.money_env in {"development", "test"},
    )
    # Only a worker imports the compute plane.
    from money.flows.research import build_runtime

    runtime = build_runtime(settings.money_research_mode)
    worker_id = f"money-worker-{uuid4()}"
    stop = threading.Event()

    def shutdown(signum: int, frame: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        while not stop.is_set():
            try:
                handled = run_once(
                    store,
                    runtime,
                    worker_id=worker_id,
                    mode=settings.money_research_mode,
                    lease_seconds=settings.money_worker_lease_seconds,
                )
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
