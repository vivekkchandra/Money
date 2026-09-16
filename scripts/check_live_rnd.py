"""Opt-in real personal-data acceptance; never a commercial or native-firm PASS.

Examples (credentials remain in the environment, never CLI arguments):
  MONEY_ENV=development MONEY_RESEARCH_MODE=live_rnd uv run python scripts/check_live_rnd.py --providers-only
  uv run python scripts/check_live_rnd.py --run-jobs --workspace rnd-acceptance-20260916

The job variant requires an already migrated diagnostic database and an unused
explicit workspace. It creates bounded durable diagnostic jobs and retains their
audit/evidence records. It never migrates, deletes data, or places trades.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from money.api.settings import Settings
from money.data.rnd_market import RndMarketSnapshot, YFinanceProvider
from money.data.rnd_official import collect_macro_context, collect_official_context
from money.research.rnd import NATIVE_COMPONENTS, RndRuntime, build_rnd_runtime
from money.research.rnd_contracts import RndResult, RndSnapshot
from money.schemas.contracts import ResearchMandate, utc_now
from money.storage import ResearchStore
from money.storage import models as db
from money.worker import supervise_job

ROOT = Path(__file__).resolve().parents[1]
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_WORKSPACE = re.compile(r"^rnd-acceptance-[a-z0-9][a-z0-9_-]{0,59}$")
_SAFE_CODES = frozenset(
    {
        "RND_PROVIDER_UNAVAILABLE",
        "RND_PROVIDER_TIMEOUT",
        "RND_PROVIDER_RATE_LIMIT",
        "RND_PROVIDER_CIRCUIT_OPEN",
        "RND_RUNTIME_UNAVAILABLE",
        "RND_HISTORY_MISSING",
        "RND_PROVIDER_DATA_INVALID",
        "RND_STALE_DATA",
        "RND_SYMBOL_MISMATCH",
        "RND_CURRENCY_UNSUPPORTED",
        "RND_PROVIDER_CONFLICT",
        "RND_QUOTE_INVALID",
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_DNS_UNAVAILABLE",
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_CIRCUIT_OPEN",
        "PROVIDER_BUSY",
        "SOURCE_URL_DENIED",
        "OFFICIAL_RESPONSE_INVALID",
        "OFFICIAL_NO_RECORDS",
        "SEC_EXACT_TICKER_MATCH_REQUIRED",
        "SEC_CONTACT_USER_AGENT_REQUIRED",
        "COMPANIES_HOUSE_CREDENTIAL_MISSING",
        "COMPANY_NUMBER_MAPPING_MISSING",
        "OFFICIAL_COUNTRY_UNSUPPORTED",
        "PIT_VIOLATION",
        "PERSONAL_RND_CONFIGURATION_REQUIRED",
        "DIAGNOSTIC_WORKSPACE_REQUIRED",
        "DIAGNOSTIC_WORKSPACE_NOT_EMPTY",
        "MIGRATIONS_NOT_CURRENT",
        "DATABASE_UNAVAILABLE",
        "DIAGNOSTIC_JOB_NOT_CLAIMED",
        "DIAGNOSTIC_JOB_FAILED",
        "DIAGNOSTIC_RESULT_INVALID",
        "CONFIGURATION_INVALID",
        "PROVIDERS_ONLY",
        "MARKET_UNAVAILABLE",
        "WORKER_INTERRUPTED",
    }
)


class AcceptanceFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProviderProbeSettings(BaseSettings):
    """Provider-only operation deliberately requires no invented DB or API token."""

    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)
    money_env: Literal["development", "test"]
    money_research_mode: Literal["live_rnd"]
    money_enable_synthetic_demo: bool = False
    money_rnd_provider_timeout_seconds: float = Field(default=30, ge=5, le=60)
    money_sec_user_agent: str | None = Field(default=None, min_length=10, max_length=200)
    companies_house_api_key: SecretStr | None = None
    money_rnd_company_numbers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def explicit_company_identity(self) -> ProviderProbeSettings:
        if self.money_enable_synthetic_demo:
            raise ValueError("PERSONAL_RND_CONFIGURATION_REQUIRED")
        if any(
            not _SYMBOL.fullmatch(symbol) or not re.fullmatch(r"[A-Z0-9]{8}", number)
            for symbol, number in self.money_rnd_company_numbers.items()
        ):
            raise ValueError("COMPANY_NUMBER_MAPPING_INVALID")
        return self


def safe_code(error: Exception | str) -> str:
    candidate = error if isinstance(error, str) else getattr(error, "code", None)
    if isinstance(candidate, str) and candidate in _SAFE_CODES:
        return candidate
    if isinstance(error, SQLAlchemyError):
        return "DATABASE_UNAVAILABLE"
    if isinstance(error, ValidationError):
        return "CONFIGURATION_INVALID"
    return "PROVIDER_UNAVAILABLE"


def step(status: str, reason: str | None = None, **evidence: Any) -> dict[str, Any]:
    return {"status": status, **({"reason": safe_code(reason)} if reason else {}), **evidence}


def provider_runtime(settings: ProviderProbeSettings) -> RndRuntime:
    """Use real adapters, without a DB cache or replacement of missing providers."""
    provider = YFinanceProvider(timeout_seconds=settings.money_rnd_provider_timeout_seconds)

    def official(ticker: str, market: dict[str, Any]) -> dict[str, Any]:
        return collect_official_context(
            ticker,
            market["instrument"]["listing_country"],
            user_agent=settings.money_sec_user_agent,
            companies_house_key=(
                settings.companies_house_api_key.get_secret_value()
                if settings.companies_house_api_key
                else None
            ),
            company_number=settings.money_rnd_company_numbers.get(ticker),
        )

    return RndRuntime(
        fetch_market=lambda ticker: provider.snapshot(ticker).model_dump(mode="json"),
        fetch_official=official,
        fetch_macro=collect_macro_context,
    )


def context_summary(context: dict[str, Any]) -> dict[str, Any]:
    records = context.get("records")
    count = len(records) if isinstance(records, list) else 0
    status = context.get("status")
    if status == "READY" and count:
        return step("PASSED", record_count=count)
    if status == "NOT_CONFIGURED":
        return step("NOT_CONFIGURED", str(context.get("reason", "PROVIDER_UNAVAILABLE")))
    return step("FAILED", str(context.get("reason") or "OFFICIAL_NO_RECORDS"), record_count=count)


def probe_instrument(runtime: RndRuntime, ticker: str) -> dict[str, Any]:
    report: dict[str, Any] = {"ticker": ticker}
    for name in ("official", "snapshot", "worker", "result"):
        report[name] = step("NOT_RUN", "MARKET_UNAVAILABLE")
    try:
        market = RndMarketSnapshot.model_validate(runtime.fetch_market(ticker))
        if market.instrument.ticker != ticker:
            raise AcceptanceFailure("RND_SYMBOL_MISMATCH")
        report["market"] = step(
            "PASSED",
            provider="yfinance",
            provider_version=market.provider_version,
            observations=len(market.history),
            currency=market.quote.currency,
            raw_currency=market.quote.raw_currency,
            quote_time=market.quote.observed_at.isoformat(),
            retrieved_at=market.retrieved_at.isoformat(),
            content_hash=market.content_hash,
            freshness=market.quote.freshness,
        )
    except ValidationError:
        report["market"] = step("FAILED", "RND_PROVIDER_DATA_INVALID")
        return report
    except Exception as error:
        report["market"] = step("FAILED", safe_code(error))
        return report
    try:
        report["official"] = context_summary(
            runtime.fetch_official(ticker, market.model_dump(mode="json"))
        )
    except Exception as error:
        report["official"] = step("FAILED", safe_code(error))
    for name in ("snapshot", "worker", "result"):
        report[name] = step("NOT_RUN", "PROVIDERS_ONLY")
    return report


def require_current_migrations(store: ResearchStore) -> None:
    expected = set(ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads())
    with store.engine.connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
    if not expected or current != expected:
        raise AcceptanceFailure("MIGRATIONS_NOT_CURRENT")


def diagnostic_store(settings: Settings, workspace: str) -> ResearchStore:
    if (
        settings.money_research_mode != "live_rnd"
        or settings.money_env not in {"development", "test"}
        or settings.money_enable_synthetic_demo
    ):
        raise AcceptanceFailure("PERSONAL_RND_CONFIGURATION_REQUIRED")
    if not _WORKSPACE.fullmatch(workspace):
        raise AcceptanceFailure("DIAGNOSTIC_WORKSPACE_REQUIRED")
    local = settings.money_deployment_env == "local"
    store = ResearchStore(
        settings.database_url.get_secret_value(),
        allow_sqlite=local,
        workspace_id=workspace,
        pool_size=1,
    )
    try:
        require_current_migrations(store)
        with store.engine.connect() as connection:
            existing = connection.scalar(
                select(db.jobs.c.id)
                .where(
                    db.jobs.c.workspace_id == workspace,
                )
                .limit(1)
            )
        if existing is not None:
            raise AcceptanceFailure("DIAGNOSTIC_WORKSPACE_NOT_EMPTY")
    except BaseException:
        store.engine.dispose()
        raise
    return store


def verify_persisted_job(store: ResearchStore, job_id: str) -> dict[str, Any]:
    """Verify insert-only snapshot and packet through a fresh database connection."""
    with store.engine.connect() as connection:
        job = (
            connection.execute(
                select(db.jobs).where(
                    db.jobs.c.id == job_id,
                    db.jobs.c.workspace_id == store.workspace_id,
                )
            )
            .mappings()
            .one()
        )
        raw_snapshot = connection.scalar(
            select(db.snapshots.c.payload).where(db.snapshots.c.job_id == job_id)
        )
        raw_packet = connection.scalar(
            select(db.packets.c.payload).where(db.packets.c.job_id == job_id)
        )
    worker = step(
        "PASSED" if job["status"] == "COMPLETE" else "FAILED",
        scope="WORKER_EXECUTION_ONLY",
        job_id=job_id,
        terminal_status=job["status"],
    )
    if job["status"] != "COMPLETE" or raw_snapshot is None or raw_packet is None:
        return {
            "worker": worker,
            "snapshot": step("NOT_RUN", "DIAGNOSTIC_JOB_FAILED"),
            "result": step("FAILED", str(job["error_code"] or "DIAGNOSTIC_JOB_FAILED")),
        }
    snapshot = RndSnapshot.model_validate(raw_snapshot)
    packet = RndResult.model_validate(raw_packet)
    if (
        job["research_kind"] != "live_rnd"
        or snapshot.ticker != job["ticker"]
        or packet.ticker != job["ticker"]
        or packet.research_id != job_id
        or packet.snapshot_hash != snapshot.hash
        or packet.snapshot_id != snapshot.snapshot_id
        or packet.signal is not None
        or packet.final_state != "INSUFFICIENT_EVIDENCE"
        or {item.component for item in packet.components if item.status == "NOT_CONFIGURED"}
        & set(NATIVE_COMPONENTS)
        != set(NATIVE_COMPONENTS)
    ):
        raise AcceptanceFailure("DIAGNOSTIC_RESULT_INVALID")
    return {
        "worker": worker,
        "snapshot": step(
            "PASSED", snapshot_hash=snapshot.hash, evidence_records=len(snapshot.evidence)
        ),
        "result": step(
            "PASSED",
            scope="PERSISTED_RND_EVIDENCE_ONLY",
            final_state=packet.final_state,
            native_research="NOT_CONFIGURED",
            signal_issued=False,
        ),
    }


def run_diagnostic_job(store: ResearchStore, settings: Settings, ticker: str) -> dict[str, Any]:
    job = store.create_job(
        ticker,
        ResearchMandate(),
        research_kind="live_rnd",
        max_attempts=1,
        capacity=settings.money_queue_capacity,
        idempotency_key="live-rnd-acceptance:" + ticker,
    )
    claim = store.claim_job(
        "rnd-acceptance-" + uuid4().hex,
        lease_seconds=settings.money_worker_lease_seconds,
        job_timeout_seconds=settings.money_job_timeout_seconds,
        research_kind="live_rnd",
        job_id=job["id"],
    )
    if claim is None or claim.job_id != job["id"]:
        raise AcceptanceFailure("DIAGNOSTIC_JOB_NOT_CLAIMED")
    # Existing supervisor starts the real separate compute process and run_once.
    # Its process deadline covers native calls, provider I/O and database stalls.
    supervise_job(store, settings, claim, threading.Event())
    return verify_persisted_job(store, job["id"])


def check(
    runtime: RndRuntime,
    tickers: tuple[str, ...],
    *,
    store: ResearchStore | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    reports = []
    for ticker in tickers:
        report = probe_instrument(runtime, ticker)
        if store is not None and settings is not None and report["market"]["status"] == "PASSED":
            try:
                report.update(run_diagnostic_job(store, settings, ticker))
            except Exception as error:
                report.update(
                    worker=step("FAILED", safe_code(error)),
                    snapshot=step("NOT_RUN", "DIAGNOSTIC_JOB_FAILED"),
                    result=step("NOT_RUN", "DIAGNOSTIC_JOB_FAILED"),
                )
        reports.append(report)
    try:
        macro = context_summary(runtime.fetch_macro())
    except Exception as error:
        macro = step("FAILED", safe_code(error))
    return {
        "status": "INCOMPLETE_RESEARCH",
        "mode": "live_rnd",
        "scope": "PERSONAL_RND_ONLY",
        "checked_at": utc_now().isoformat(),
        "production_qualified": False,
        "native_research": {component: "NOT_CONFIGURED" for component in NATIVE_COMPONENTS},
        "instruments": reports,
        "macro": macro,
        "job_execution": "ENABLED" if store is not None else "NOT_REQUESTED",
        "limitation": "Provider data and persisted evidence are not qualified multi-firm research.",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--providers-only",
        action="store_true",
        help="Real provider calls; no database required or job writes",
    )
    action.add_argument(
        "--run-jobs",
        action="store_true",
        help="Authorise diagnostic job writes and bounded separate worker execution",
    )
    parser.add_argument("--workspace", help="Unused diagnostic workspace: rnd-acceptance-<suffix>")
    parser.add_argument(
        "--ticker", action="append", help="Up to five symbols; defaults to BARC.L and AAPL"
    )
    parser.add_argument("--job-timeout-seconds", type=int, default=180)
    arguments = parser.parse_args(argv)
    tickers = tuple(
        dict.fromkeys(symbol.upper() for symbol in (arguments.ticker or ("BARC.L", "AAPL")))
    )
    if not 1 <= len(tickers) <= 5 or any(
        not _SYMBOL.fullmatch(symbol) or symbol == "DEMO.L" for symbol in tickers
    ):
        parser.error("provide one to five real stock symbols, never DEMO.L")
    if not 30 <= arguments.job_timeout_seconds <= 300:
        parser.error("job timeout must be between 30 and 300 seconds")
    if arguments.run_jobs and (
        not arguments.workspace or not _WORKSPACE.fullmatch(arguments.workspace)
    ):
        parser.error("--run-jobs requires --workspace rnd-acceptance-<suffix>")
    if arguments.providers_only and arguments.workspace:
        parser.error("--workspace is only used with --run-jobs")
    store = None
    try:
        provider_settings = ProviderProbeSettings()  # type: ignore[call-arg]
        settings = None
        if arguments.run_jobs:
            settings = Settings(money_job_timeout_seconds=arguments.job_timeout_seconds)  # type: ignore[call-arg]
            store = diagnostic_store(settings, arguments.workspace)
            runtime = build_rnd_runtime(settings, store)
        else:
            runtime = provider_runtime(provider_settings)
        report = check(runtime, tickers, store=store, settings=settings)
    except Exception as error:
        report = {
            "status": "BLOCKED",
            "reason": safe_code(error),
            "production_qualified": False,
            "native_research": {component: "NOT_CONFIGURED" for component in NATIVE_COMPONENTS},
            "instruments": [],
            "macro": step("NOT_RUN"),
        }
    finally:
        if store is not None:
            store.engine.dispose()
    print(json.dumps(report, indent=2, allow_nan=False))
    # Native firms are intentionally unconfigured in this data-only mode. Exit 2
    # makes incomplete acceptance explicit even when every data operation passes.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
