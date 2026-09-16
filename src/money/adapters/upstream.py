"""Validated, optional upstream boundaries; no native engines are loaded at import.

Runners are trusted deployment dependencies, not client-supplied callables. These
interfaces restrict data flow; they are not an OS sandbox for arbitrary Python.
Live runners must be installed and qualified separately from the demo pipeline.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from importlib import import_module
from typing import Literal, Protocol

from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    Contract,
    DocumentFact,
    FinancialFact,
    FirmReport,
    LeanValidationReport,
    PriceBar,
    QlibQuantResearchReport,
    ResearchMandate,
    ResearchSnapshot,
    TradingAgentsResearchReport,
)

UPSTREAM_SHAS = {
    "tradingagents": "be952b8eccb49720509af544c6675233bc1f10d0",
    "ai_hedge_fund": "fc1bf250ead209ae5f02c39c3d0062c4bb554505",
    "qlib": "be725493eb1a6bbb42bf11b37aa7669f59610ff1",
}


class UpstreamUnavailable(RuntimeError):
    """A configured and qualified native runner is not available."""


class UnsupportedSnapshotData(ValueError):
    """The frozen evidence cannot faithfully satisfy a native data request."""


class InvalidUpstreamReport(ValueError):
    """A native result fails Money's identity or evidence contract."""


class QualitativeRunner(Protocol):
    def __call__(self, mandate: ResearchMandate, snapshot: ResearchSnapshot) -> FirmReport: ...


def _snapshot_copy(snapshot: ResearchSnapshot) -> ResearchSnapshot:
    # JSON roundtrip revalidates even objects forged with Pydantic model_copy or
    # model_construct, and prevents one runner retaining another's object state.
    return ResearchSnapshot.model_validate_json(snapshot.model_dump_json())


def _validate_report[ReportT: FirmReport](
    result: FirmReport, snapshot: ResearchSnapshot, report_type: type[ReportT], firm: str
) -> ReportT:
    try:
        report = report_type.model_validate_json(result.model_dump_json())
    except (AttributeError, ValidationError) as exc:
        raise InvalidUpstreamReport("runner returned an invalid report schema") from exc
    if report.firm != firm:
        raise InvalidUpstreamReport("runner returned another firm's report")
    if report.snapshot_id != snapshot.snapshot_id or report.snapshot_hash != snapshot.hash:
        raise InvalidUpstreamReport("runner returned a different snapshot identity or hash")
    if report.upstream_sha != UPSTREAM_SHAS[firm]:
        raise InvalidUpstreamReport("runner version does not match the qualified upstream pin")
    if report.runtime != "live":
        raise InvalidUpstreamReport("fixture output cannot be admitted as native research")
    if report.created_at < snapshot.created_at:
        raise InvalidUpstreamReport("report predates its frozen snapshot")
    allowed = {e.evidence_id for e in snapshot.evidence}
    claim_ids = [claim.claim_id for claim in report.claims]
    if len(set(claim_ids)) != len(claim_ids):
        raise InvalidUpstreamReport("duplicate claim identity")
    for claim in report.claims:
        if not claim.evidence_ids or not set(claim.evidence_ids).issubset(allowed):
            raise InvalidUpstreamReport("claim has missing or unknown snapshot evidence")
    return report


class TradingAgentsAdapter:
    """Money graph assembly must inject snapshot tools into native GraphSetup."""

    firm = "tradingagents"

    def __init__(self, runner: QualitativeRunner | None = None) -> None:
        self._runner = runner

    def research(
        self, mandate: ResearchMandate, snapshot: ResearchSnapshot
    ) -> TradingAgentsResearchReport:
        if self._runner is None:
            raise UpstreamUnavailable("TradingAgents snapshot-only runner is not configured")
        facts = _snapshot_copy(snapshot)
        policy = ResearchMandate.model_validate_json(mandate.model_dump_json())
        return _validate_report(
            self._runner(policy, _snapshot_copy(facts)),
            facts, TradingAgentsResearchReport, "tradingagents"
        )


class AIHedgeFundAdapter:
    """An injected native runner may use AIHedgeFundSnapshotClient below."""

    firm = "ai_hedge_fund"

    def __init__(self, runner: QualitativeRunner | None = None) -> None:
        self._runner = runner

    def research(
        self, mandate: ResearchMandate, snapshot: ResearchSnapshot
    ) -> AIHedgeFundResearchReport:
        if self._runner is None:
            raise UpstreamUnavailable("AI-HF snapshot-only runner is not configured")
        facts = _snapshot_copy(snapshot)
        policy = ResearchMandate.model_validate_json(mandate.model_dump_json())
        return _validate_report(
            self._runner(policy, _snapshot_copy(facts)),
            facts, AIHedgeFundResearchReport, "ai_hedge_fund"
        )


class QuantBar(Contract):
    evidence_id: str
    observed_at: AwareDatetime
    open_gbp: Decimal
    high_gbp: Decimal
    low_gbp: Decimal
    close_gbp: Decimal
    volume: int = Field(ge=0)


class QuantResearchInput(Contract):
    """Closed numeric input surface: no news, thesis, confidence, or opinions."""

    snapshot_id: str
    snapshot_hash: str
    ticker: str
    cutoff: AwareDatetime
    minimum_horizon_days: int = Field(ge=1, le=30)
    maximum_horizon_days: int = Field(ge=1, le=30)
    bars: tuple[QuantBar, ...]


class QlibRunner(Protocol):
    def __call__(self, data: QuantResearchInput) -> QlibQuantResearchReport: ...


class QlibAdapter:
    firm = "qlib"

    def __init__(self, runner: QlibRunner | None = None) -> None:
        self._runner = runner

    def research(
        self, mandate: ResearchMandate, snapshot: ResearchSnapshot
    ) -> QlibQuantResearchReport:
        if self._runner is None:
            raise UpstreamUnavailable("approved Qlib inference runner is not configured")
        facts = _snapshot_copy(snapshot)
        policy = ResearchMandate.model_validate_json(mandate.model_dump_json())
        bars = []
        for record in sorted(facts.evidence, key=lambda e: e.observation_time):
            payload = record.payload
            if not isinstance(payload, PriceBar):
                continue
            if not record.available_at(facts.price_cutoff) or record.conflicting:
                raise UnsupportedSnapshotData("Qlib price data lacks safe availability or conflicts")
            divisor = Decimal(100) if payload.currency == "GBX" else Decimal(1)
            bars.append(QuantBar(
                evidence_id=record.evidence_id,
                observed_at=record.observation_time,
                open_gbp=payload.open / divisor,
                high_gbp=payload.high / divisor,
                low_gbp=payload.low / divisor,
                close_gbp=payload.close / divisor,
                volume=payload.volume,
            ))
        if not bars:
            raise UnsupportedSnapshotData("Qlib requires snapshot OHLCV evidence")
        data = QuantResearchInput(
            snapshot_id=facts.snapshot_id,
            snapshot_hash=facts.hash,
            ticker=facts.ticker,
            cutoff=facts.price_cutoff,
            minimum_horizon_days=policy.minimum_horizon_days,
            maximum_horizon_days=policy.maximum_horizon_days,
            bars=tuple(bars),
        )
        numeric_ids = {bar.evidence_id for bar in data.bars}
        report = _validate_report(self._runner(data), facts, QlibQuantResearchReport, "qlib")
        if report.llm_provider_family is not None:
            raise InvalidUpstreamReport("Qlib cannot report LLM-provider research")
        if report.prediction_score is None:
            raise InvalidUpstreamReport("Qlib runner did not produce a numeric prediction")
        if (report.rank is None) != (report.universe_size is None):
            raise InvalidUpstreamReport("Qlib rank requires a stated comparison universe")
        if (report.rank is not None and report.universe_size is not None
                and report.rank > report.universe_size):
            raise InvalidUpstreamReport("Qlib rank exceeds its comparison universe")
        if any(not set(claim.evidence_ids).issubset(numeric_ids) for claim in report.claims):
            raise InvalidUpstreamReport("Qlib cited qualitative evidence it was not given")
        return report


class NativeQlibModel(Protocol):
    def predict(self, dataset: object, segment: str = "test") -> object: ...


@dataclass(frozen=True)
class NativeQlibRunner:
    """Invoke a configured native model; conversion stays explicit and testable.

    The dataset factory must use only its numeric argument. Deployment supplies
    an approved model object, never a client-uploaded pickle or class path.
    """

    model: NativeQlibModel
    dataset_factory: Callable[[QuantResearchInput], object]
    report_factory: Callable[[QuantResearchInput, object], QlibQuantResearchReport]

    def __call__(self, data: QuantResearchInput) -> QlibQuantResearchReport:
        prediction = self.model.predict(self.dataset_factory(data), segment="test")
        return self.report_factory(data, prediction)


class LeanRunner(Protocol):
    def __call__(
        self, snapshot: ResearchSnapshot, reports: tuple[FirmReport, ...]
    ) -> LeanValidationReport: ...


class LeanAdapter:
    """Fixed validation runner; caller must first enforce the durable lock barrier."""

    def __init__(self, runner: LeanRunner | None = None) -> None:
        self._runner = runner

    def validate(
        self, snapshot: ResearchSnapshot, reports: tuple[FirmReport, ...]
    ) -> LeanValidationReport:
        if self._runner is None:
            raise UpstreamUnavailable("LEAN validation runner is not configured")
        facts = _snapshot_copy(snapshot)
        if len(reports) != 3 or {r.firm for r in reports} != set(UPSTREAM_SHAS):
            raise InvalidUpstreamReport("LEAN requires all three independent firm reports")
        report_types: dict[str, type[FirmReport]] = {
            "tradingagents": TradingAgentsResearchReport,
            "ai_hedge_fund": AIHedgeFundResearchReport,
            "qlib": QlibQuantResearchReport,
        }
        checked = tuple(_validate_report(r, facts, report_types[r.firm], r.firm) for r in reports)
        try:
            result = LeanValidationReport.model_validate_json(
                self._runner(_snapshot_copy(facts), checked).model_dump_json()
            )
        except (AttributeError, ValidationError) as exc:
            raise InvalidUpstreamReport("LEAN returned an invalid report schema") from exc
        if result.snapshot_id != facts.snapshot_id:
            raise InvalidUpstreamReport("LEAN returned another snapshot's validation")
        if result.state == "PASS" and not (
            result.observations > 0 and result.walk_forward and result.out_of_sample
            and result.pit_safe and result.survivorship_checked and result.costs_included
            and result.sensitivity_checked and result.spread_bps is not None
            and result.slippage_bps is not None
        ):
            raise InvalidUpstreamReport("LEAN PASS lacks mandatory validation evidence")
        return result


@dataclass(frozen=True)
class AIHedgeFundModelTypes:
    price: type[BaseModel]
    company_news: type[BaseModel]
    company_facts: type[BaseModel]

    @classmethod
    def native(cls) -> AIHedgeFundModelTypes:
        """Load the pinned native data models only when explicitly requested."""
        try:
            models = import_module("hedge_fund.data.models")
        except ImportError as exc:
            raise UpstreamUnavailable("the pinned hedge_fund package is not installed") from exc
        return cls(models.Price, models.CompanyNews, models.CompanyFacts)


class AIHedgeFundSnapshotClient:
    """Native DataClient-compatible evidence bridge with no network capabilities.

    FinancialFact does not yet establish fiscal cadence or TTM construction.
    Financial/earnings/insider requests therefore fail explicitly. This bridge
    cannot currently run the default native fundamentals snapshot builder.
    """

    def __init__(
        self, snapshot: ResearchSnapshot, model_types: AIHedgeFundModelTypes | None = None
    ) -> None:
        self._snapshot = _snapshot_copy(snapshot)
        self._models = model_types or AIHedgeFundModelTypes.native()

    def _ticker(self, ticker: str) -> None:
        if ticker != self._snapshot.ticker:
            raise UnsupportedSnapshotData("requested ticker is outside the frozen snapshot")

    def _end_date(self, ticker: str, end_date: str, kind: Literal["price", "news"]) -> date:
        self._ticker(ticker)
        requested = date.fromisoformat(end_date)
        cutoff = self._snapshot.price_cutoff if kind == "price" else self._snapshot.news_cutoff
        if requested > cutoff.date():
            raise UnsupportedSnapshotData("request extends beyond the snapshot cutoff")
        return requested

    def get_prices(
        self, ticker: str, start_date: str, end_date: str, **kwargs: object
    ) -> list[BaseModel]:
        if kwargs:
            raise UnsupportedSnapshotData("native price options are not supported by this snapshot")
        end = self._end_date(ticker, end_date, "price")
        start = date.fromisoformat(start_date)
        if start > end:
            raise UnsupportedSnapshotData("price interval is reversed")
        rows = []
        for record in sorted(self._snapshot.evidence, key=lambda e: e.observation_time):
            bar = record.payload
            if not isinstance(bar, PriceBar) or not start <= record.observation_time.date() <= end:
                continue
            if not record.available_at(self._snapshot.price_cutoff) or record.conflicting:
                raise UnsupportedSnapshotData("price availability is unknown or conflicting")
            assert record.publication_time is not None
            if record.publication_time.date() > end:
                continue
            divisor = Decimal(100) if bar.currency == "GBX" else Decimal(1)
            rows.append(self._models.price.model_validate({
                "open": float(bar.open / divisor), "close": float(bar.close / divisor),
                "high": float(bar.high / divisor), "low": float(bar.low / divisor),
                "volume": bar.volume, "time": record.observation_time.isoformat(),
            }))
        return rows

    def get_news(
        self, ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000
    ) -> list[BaseModel]:
        end = self._end_date(ticker, end_date, "news")
        start = date.fromisoformat(start_date) if start_date else date.min
        if start > end or not 1 <= limit <= 1000:
            raise UnsupportedSnapshotData("invalid news date range or limit")
        rows = []
        for record in sorted(self._snapshot.evidence, key=lambda e: e.observation_time, reverse=True):
            item = record.payload
            if not isinstance(item, DocumentFact) or item.kind != "news":
                continue
            if not record.available_at(self._snapshot.news_cutoff) or record.conflicting:
                raise UnsupportedSnapshotData("news publication is unknown or conflicting")
            assert record.publication_time is not None  # available_at proved this
            if not start <= record.publication_time.date() <= end:
                continue
            rows.append(self._models.company_news.model_validate({
                "ticker": ticker, "title": item.title, "source": record.source,
                "date": record.publication_time.isoformat(), "url": item.url,
            }))
        return rows[:limit]

    def get_company_facts(self, ticker: str) -> BaseModel:
        self._ticker(ticker)
        return self._models.company_facts.model_validate({
            "ticker": ticker, "name": self._snapshot.instrument.company,
        })

    def get_financial_metrics(
        self, ticker: str, end_date: str, period: str = "ttm", limit: int = 10
    ) -> list[BaseModel]:
        self._ticker(ticker)
        raise UnsupportedSnapshotData("snapshot does not establish fiscal cadence or TTM metrics")

    def get_insider_trades(
        self, ticker: str, end_date: str, start_date: str | None = None, limit: int = 1000
    ) -> list[BaseModel]:
        self._ticker(ticker)
        raise UnsupportedSnapshotData("snapshot has no insider-transaction dataset")

    def get_earnings(self, ticker: str) -> BaseModel | None:
        self._ticker(ticker)
        raise UnsupportedSnapshotData("snapshot has no normalized earnings dataset")

    def get_earnings_history(self, ticker: str, limit: int = 12) -> list[BaseModel]:
        self._ticker(ticker)
        raise UnsupportedSnapshotData("snapshot has no normalized earnings history")

    def get_market_cap(self, ticker: str, end_date: str) -> float | None:
        self._ticker(ticker)
        end = date.fromisoformat(end_date)
        if end > self._snapshot.fundamental_cutoff.date():
            raise UnsupportedSnapshotData("request extends beyond the fundamentals cutoff")
        records = sorted(self._snapshot.evidence, key=lambda e: e.observation_time, reverse=True)
        for record in records:
            fact = record.payload
            if not isinstance(fact, FinancialFact) or fact.metric != "market_cap":
                continue
            if not record.available_at(self._snapshot.fundamental_cutoff) or record.conflicting:
                raise UnsupportedSnapshotData("market-cap availability is unknown or conflicting")
            assert record.publication_time is not None
            if record.publication_time.date() > end or record.observation_time.date() > end:
                continue
            if fact.unit not in {"GBP", "GBX"}:
                raise UnsupportedSnapshotData("market cap must have a GBP or GBX unit")
            return float(fact.value / (100 if fact.unit == "GBX" else 1))
        return None
