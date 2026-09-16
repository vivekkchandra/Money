"""Personal public-data studies; no demo adapters or commercial publication path.

Incomplete native research stays explicitly unavailable. Collecting real market
data must never be represented as running TradingAgents, Qlib, LEAN or CrewAI.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from money.api.settings import Settings
from money.data.rnd_cache import RndProviderCache
from money.data.security import ProviderFailure
from money.research.rnd_contracts import RndComponent, RndEvidence, RndResult, RndSnapshot
from money.research.rnd_store import complete_study, save_snapshot
from money.schemas.contracts import content_hash, utc_now
from money.storage import ResearchStore

LIMITATIONS = (
    "R&D / PERSONAL USE. Free/public data may be delayed; not commercially licensed market data.",
    "Not investment advice. No research signal, allocation, or broker action is issued.",
    "ISA eligibility and ethical suitability are not established by a Yahoo listing.",
    "Current vendor history/fundamentals are not verified historical point-in-time evidence.",
    "The commercial mandate and qualification gates remain unchanged.",
)
NATIVE_COMPONENTS = ("tradingagents", "ai_hedge_fund", "qlib", "lean", "crewai")


class _ContextUnavailable(ProviderFailure):
    def __init__(self, context: dict[str, Any]) -> None:
        super().__init__("OFFICIAL_CONTEXT_UNAVAILABLE", retryable=True)
        self.context = context


@dataclass(frozen=True)
class RndRuntime:
    fetch_market: Callable[[str], dict[str, Any]]
    fetch_official: Callable[[str, dict[str, Any]], dict[str, Any]]
    fetch_macro: Callable[[], dict[str, Any]]


def build_rnd_runtime(settings: Settings, store: ResearchStore) -> RndRuntime:
    if settings.money_research_mode != "live_rnd" or settings.money_env not in {
        "development", "test",
    } or settings.money_enable_synthetic_demo:
        raise ValueError("PERSONAL_RND_CONFIGURATION_REQUIRED")
    from money.data.rnd_market import YFinanceProvider
    from money.data.rnd_official import collect_macro_context, collect_official_context

    provider = YFinanceProvider(timeout_seconds=settings.money_rnd_provider_timeout_seconds)
    cache = RndProviderCache(store)

    def cached_context(
        name: str, key: str, fetch: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        def checked() -> dict[str, Any]:
            value = fetch()
            if value.get("status") != "READY":
                # A wrapper's explicit failure must not count as a successful probe.
                raise _ContextUnavailable(value)
            return value
        try:
            return cache.get(name, key, checked, ttl_seconds=86400)
        except _ContextUnavailable as error:
            return error.context

    def sec_admission() -> bool:
        # One global bucket across tenants and worker processes. Fixed-window
        # boundary bursts are <=10/sec; native SEC calls are additionally paced.
        return bool(store.for_workspace("rnd-public-providers").consume_rate_limit(
            "sec-official-global", 5, 1,
        )["allowed"])

    def official(ticker: str, market: dict[str, Any]) -> dict[str, Any]:
        instrument = market["instrument"]
        # A missing mapping/key is represented explicitly, not replaced by Yahoo filings.
        country = instrument.get("listing_country", "UNKNOWN")
        number = settings.money_rnd_company_numbers.get(ticker)
        def fetch() -> dict[str, Any]:
            return collect_official_context(
                ticker, country, user_agent=settings.money_sec_user_agent,
                companies_house_key=(settings.companies_house_api_key.get_secret_value()
                                     if settings.companies_house_api_key else None),
                company_number=number, rate_gate=sec_admission,
            )
        configured = (country == "US" and settings.money_sec_user_agent) or (
            country == "GB" and settings.companies_house_api_key and number
        )
        if not configured:
            return fetch()  # Missing configuration is reported without any external call.
        name = "sec-edgar" if country == "US" else "companies-house"
        return cached_context(name, ticker + ":" + (number or "") + ":v1", fetch)

    return RndRuntime(
        fetch_market=lambda ticker: cache.get(
            "yfinance", ticker + ":6mo:v1",
            lambda: provider.snapshot(ticker).model_dump(mode="json"), ttl_seconds=60,
        ),
        fetch_official=official,
        fetch_macro=lambda: cached_context("bank-of-england", "IUDBEDR:v1", collect_macro_context),
    )


def _optional(operation: Callable[[], dict[str, Any]], provider: str) -> dict[str, Any]:
    try:
        return operation()
    except (ProviderFailure, ValueError, TimeoutError, ConnectionError) as error:
        code = error.code if isinstance(error, ProviderFailure) else "PROVIDER_UNAVAILABLE"
        return {"provider": provider, "status": "UNAVAILABLE", "reason": code, "records": []}


def _evidence(provider: str, kind: str, data: dict[str, Any]) -> RndEvidence:
    raw = data.get("retrieved_at") or data.get("retrieval_time")
    retrieved = datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else utc_now()
    # Wrappers report unavailable context too; it is never a factual confirmation.
    return RndEvidence(
        evidence_id=content_hash({"provider": provider, "kind": kind, "data": data}),
        provider=provider, kind=kind, retrieval_time=retrieved, payload=data,
    )


def study_analysis(market: dict[str, Any]) -> dict[str, Any]:
    bars = market["history"]
    first, last = float(bars[0]["close"]), float(bars[-1]["close"])
    splits = any(float(bar.get("stock_splits", 0) or 0) for bar in bars)
    return {
        "observations": len(bars), "first_close": first, "last_close": last,
        "raw_currency": market["instrument"]["currency"],
        "price_change_fraction": None if splits else last / first - 1,
        "split_events_present": splits,
        "return_semantics": "Observed price change only, not total return or backtest performance",
    }


def run_rnd(job_id: str, store: ResearchStore, runtime: RndRuntime) -> None:
    job = store.get_job(job_id)
    if not job or job["research_kind"] != "live_rnd" or job["ticker"] == "DEMO.L":
        raise ValueError("RND_JOB_BOUNDARY")
    checkpoint = store.get_checkpoint(job_id)
    if checkpoint["snapshot"] is None:
        store.update_stage(job_id, "SNAPSHOT_BUILD")
        market = runtime.fetch_market(job["ticker"])
        official = _optional(lambda: runtime.fetch_official(job["ticker"], market), "official")
        macro = _optional(runtime.fetch_macro, "macro")
        evidence = (
            _evidence("yfinance", "market", market),
            _evidence(official.get("provider", "official"), "filings", official),
            _evidence(macro.get("provider", "macro"), "macro", macro),
        )
        snapshot = RndSnapshot(
            snapshot_id=str(uuid4()), ticker=job["ticker"], created_at=utc_now(), market=market,
            official=(official,), macro=(macro,), limitations=LIMITATIONS,
            evidence=evidence,
        )
        save_snapshot(store, job_id, snapshot)
    else:
        snapshot = RndSnapshot.model_validate(checkpoint["snapshot"])
    components = [RndComponent(
        component="yfinance", status="READY", reason="Validated market evidence fetched and frozen",
    )]
    for item in (*snapshot.official, *snapshot.macro):
        state = item.get("status", "UNAVAILABLE")
        if state not in {"READY", "UNAVAILABLE", "NOT_CONFIGURED", "FAILED", "RATE_LIMITED"}:
            state = "UNAVAILABLE"
        components.append(RndComponent(
            component=item.get("provider", "official"), status=state,
            reason=item.get("reason") or "Official-source metadata retrieved",
        ))
    components.extend(RndComponent(
        component=component, status="NOT_CONFIGURED",
        reason="No qualified personal R&D runtime executed; no report has been fabricated",
    ) for component in NATIVE_COMPONENTS)
    complete_study(store, job_id, RndResult(
        research_id=job_id, ticker=job["ticker"], issued_at=utc_now(),
        snapshot_id=snapshot.snapshot_id, snapshot_hash=snapshot.hash,
        analysis=study_analysis(snapshot.market), components=tuple(components),
        limitations=snapshot.limitations, reasons=("NATIVE_RESEARCH_INCOMPLETE",),
    ))
