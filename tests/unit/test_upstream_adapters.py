from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict

from money.adapters.upstream import (
    UPSTREAM_SHAS,
    AIHedgeFundAdapter,
    AIHedgeFundModelTypes,
    AIHedgeFundSnapshotClient,
    InvalidUpstreamReport,
    LeanAdapter,
    NativeQlibRunner,
    QlibAdapter,
    QuantResearchInput,
    TradingAgentsAdapter,
    UnsupportedSnapshotData,
    UpstreamUnavailable,
)
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    Claim,
    DocumentFact,
    EvidenceRecord,
    FirmReport,
    InstrumentMetadata,
    LeanValidationReport,
    PriceBar,
    QlibQuantResearchReport,
    ResearchMandate,
    ResearchSnapshot,
    TradingAgentsResearchReport,
)

NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)


@pytest.fixture
def snapshot() -> ResearchSnapshot:
    provenance = {
        "snapshot_id": "snapshot-1", "source": "Example source", "provider": "example",
        "retrieval_time": NOW, "publication_time": NOW - timedelta(days=1),
        "observation_time": NOW - timedelta(days=2), "fresh_until": NOW + timedelta(days=1),
        "pit_safe": True,
    }
    bar = EvidenceRecord(
        **provenance, evidence_id="bar-1", source_id="bar-1", canonical_source_id="bar-1",
        payload=PriceBar(open=100, high=110, low=95, close=105, volume=10000, currency="GBX"),
    )
    news = EvidenceRecord(
        **provenance, evidence_id="news-1", source_id="news-1", canonical_source_id="article-1",
        payload=DocumentFact(kind="news", title="Company announcement",
                             excerpt="BUY: qualitative confidence and opinions",
                             url="https://example.com/news"),
    )
    return ResearchSnapshot(
        snapshot_id="snapshot-1", ticker="TEST.L", created_at=NOW,
        price_cutoff=NOW, news_cutoff=NOW, filing_cutoff=NOW, fundamental_cutoff=NOW,
        instrument=InstrumentMetadata(
            ticker="TEST.L", company="Test plc", instrument_type="STOCK", quote_currency="GBX",
            isa_available=True, currently_available=True, activities_verified=True,
            verified_at=NOW, source="example", provider="example", source_id="TEST.L",
        ),
        evidence=(bar, news),
    )


def report(snapshot: ResearchSnapshot, firm: str = "tradingagents") -> FirmReport:
    values = {
        "snapshot_id": snapshot.snapshot_id, "snapshot_hash": snapshot.hash,
        "conclusion": "Research observation", "model_version": "test-native-v1",
        "prompt_version": "v1", "upstream_sha": UPSTREAM_SHAS[firm],
        "model_family": "statistical" if firm == "qlib" else "qualitative",
        "created_at": NOW, "claims": (Claim(claim_id="claim-1", family="technical",
                                             statement="Price observation",
                                             evidence_ids=("bar-1",)),),
    }
    if firm == "tradingagents":
        return TradingAgentsResearchReport(**values)
    if firm == "ai_hedge_fund":
        return AIHedgeFundResearchReport(**values)
    return QlibQuantResearchReport(**values, prediction_score=0.25)


@pytest.mark.parametrize("adapter", [TradingAgentsAdapter(), AIHedgeFundAdapter(), QlibAdapter()])
def test_unconfigured_firms_are_unavailable(adapter: object, snapshot: ResearchSnapshot) -> None:
    with pytest.raises(UpstreamUnavailable):
        adapter.research(ResearchMandate(), snapshot)


def test_unconfigured_lean_is_unavailable(snapshot: ResearchSnapshot) -> None:
    with pytest.raises(UpstreamUnavailable):
        LeanAdapter().validate(snapshot, ())


@pytest.mark.parametrize("field,value", [
    ("snapshot_id", "other-snapshot"), ("snapshot_hash", "incorrect"),
    ("upstream_sha", "a" * 40), ("runtime", "demo"),
    ("created_at", NOW - timedelta(days=1)),
])
def test_qualitative_adapter_rejects_wrong_provenance(
    snapshot: ResearchSnapshot, field: str, value: object
) -> None:
    bad = report(snapshot).model_copy(update={field: value})
    with pytest.raises(InvalidUpstreamReport):
        TradingAgentsAdapter(lambda mandate, facts: bad).research(ResearchMandate(), snapshot)


def test_rejects_another_firms_output(snapshot: ResearchSnapshot) -> None:
    with pytest.raises(InvalidUpstreamReport):
        TradingAgentsAdapter(lambda mandate, facts: report(facts, "ai_hedge_fund")).research(
            ResearchMandate(), snapshot
        )


@pytest.mark.parametrize("references", [(), ("other-firm-report",)])
def test_claims_require_known_snapshot_evidence(
    snapshot: ResearchSnapshot, references: tuple[str, ...]
) -> None:
    bad = report(snapshot).model_copy(update={"claims": (
        Claim(claim_id="wrong", family="fundamental", statement="Unsupported",
              evidence_ids=references),
    )})
    with pytest.raises(InvalidUpstreamReport, match="evidence"):
        TradingAgentsAdapter(lambda mandate, facts: bad).research(ResearchMandate(), snapshot)


def test_first_pass_call_surface_has_no_peer_reports(snapshot: ResearchSnapshot) -> None:
    received = []

    def runner(mandate: ResearchMandate, facts: ResearchSnapshot) -> FirmReport:
        received.append((mandate, facts))
        assert not hasattr(facts, "reports")
        assert facts is not snapshot
        return report(facts, "ai_hedge_fund")

    result = AIHedgeFundAdapter(runner).research(ResearchMandate(), snapshot)
    assert result.firm == "ai_hedge_fund"
    assert len(received) == 1
    for adapter in (TradingAgentsAdapter, AIHedgeFundAdapter, QlibAdapter):
        assert list(inspect.signature(adapter.research).parameters) == ["self", "mandate", "snapshot"]


def test_runner_cannot_redefine_expected_snapshot(snapshot: ResearchSnapshot) -> None:
    def runner(mandate: ResearchMandate, facts: ResearchSnapshot) -> FirmReport:
        object.__setattr__(facts, "snapshot_id", "tampered")
        return report(facts)

    with pytest.raises(InvalidUpstreamReport, match="snapshot"):
        TradingAgentsAdapter(runner).research(ResearchMandate(), snapshot)
    assert snapshot.snapshot_id == "snapshot-1"


def test_qlib_gets_numeric_gbp_features_only(snapshot: ResearchSnapshot) -> None:
    received = []

    def runner(data: QuantResearchInput) -> QlibQuantResearchReport:
        received.append(data)
        return QlibQuantResearchReport.model_validate_json(report(snapshot, "qlib").model_dump_json())

    result = QlibAdapter(runner).research(ResearchMandate(), snapshot)
    assert result.prediction_score == 0.25
    assert received[0].bars[0].close_gbp == Decimal("1.05")
    assert received[0].bars[0].volume == 10000
    serialized = received[0].model_dump_json()
    for forbidden in ("BUY", "confidence", "opinions", "stretch_profit", "news-1", "claims"):
        assert forbidden not in serialized


def test_qlib_cannot_claim_unseen_qualitative_evidence(snapshot: ResearchSnapshot) -> None:
    bad = report(snapshot, "qlib").model_copy(update={"claims": (
        Claim(claim_id="q", family="quantitative", statement="News-based prediction",
              evidence_ids=("news-1",)),
    )})
    with pytest.raises(InvalidUpstreamReport, match="qualitative"):
        QlibAdapter(lambda data: bad).research(ResearchMandate(), snapshot)


@pytest.mark.parametrize("changes", [
    {"llm_provider_family": "some-llm"}, {"prediction_score": None},
    {"rank": 2}, {"rank": 3, "universe_size": 2}, {"prediction_score": float("nan")},
])
def test_qlib_rejects_invalid_model_metadata(snapshot: ResearchSnapshot, changes: dict) -> None:
    bad = report(snapshot, "qlib").model_copy(update=changes)
    with pytest.raises(InvalidUpstreamReport):
        QlibAdapter(lambda data: bad).research(ResearchMandate(), snapshot)


def test_native_qlib_runner_calls_only_inference_segment(snapshot: ResearchSnapshot) -> None:
    class Model:
        def predict(self, dataset: object, segment: str = "test") -> object:
            assert dataset == (Decimal("1.05"),)
            assert segment == "test"
            return 0.125

    def convert(data: QuantResearchInput, prediction: object) -> QlibQuantResearchReport:
        return QlibQuantResearchReport.model_validate_json(
            report(snapshot, "qlib").model_copy(update={"prediction_score": prediction}).model_dump_json()
        )

    runner = NativeQlibRunner(Model(), lambda data: tuple(b.close_gbp for b in data.bars), convert)
    assert QlibAdapter(runner).research(ResearchMandate(), snapshot).prediction_score == 0.125


def test_lean_rejects_missing_firm_and_unsupported_pass(snapshot: ResearchSnapshot) -> None:
    adapter = LeanAdapter(lambda facts, reports: LeanValidationReport(
        snapshot_id=facts.snapshot_id, state="PASS", runner_version="test",
    ))
    with pytest.raises(InvalidUpstreamReport, match="three"):
        adapter.validate(snapshot, (report(snapshot),))
    reports = tuple(report(snapshot, firm) for firm in UPSTREAM_SHAS)
    with pytest.raises(InvalidUpstreamReport, match="PASS"):
        adapter.validate(snapshot, reports)


def test_lean_preserves_insufficient_evidence(snapshot: ResearchSnapshot) -> None:
    adapter = LeanAdapter(lambda facts, reports: LeanValidationReport(
        snapshot_id=facts.snapshot_id, state="INSUFFICIENT_EVIDENCE", runner_version="test",
        findings=("No qualified validation dataset",),
    ))
    reports = tuple(report(snapshot, firm) for firm in UPSTREAM_SHAS)
    assert adapter.validate(snapshot, reports).state == "INSUFFICIENT_EVIDENCE"


class NativeRecord(BaseModel):
    """Lightweight model stand-in; tests conversion without upstream packages."""

    model_config = ConfigDict(extra="allow")


@pytest.fixture
def native_client(snapshot: ResearchSnapshot) -> AIHedgeFundSnapshotClient:
    return AIHedgeFundSnapshotClient(
        snapshot, AIHedgeFundModelTypes(NativeRecord, NativeRecord, NativeRecord)
    )


def test_aihf_snapshot_bridge_converts_prices_and_news(native_client: AIHedgeFundSnapshotClient) -> None:
    prices = native_client.get_prices("TEST.L", "2026-09-01", "2026-09-16")
    assert prices[0].model_dump()["close"] == 1.05
    news = native_client.get_news("TEST.L", "2026-09-16")
    assert news[0].model_dump()["title"] == "Company announcement"
    assert native_client.get_company_facts("TEST.L").model_dump()["name"] == "Test plc"


def test_aihf_bridge_enforces_ticker_and_cutoff(native_client: AIHedgeFundSnapshotClient) -> None:
    with pytest.raises(UnsupportedSnapshotData, match="ticker"):
        native_client.get_prices("OTHER.L", "2026-09-01", "2026-09-16")
    with pytest.raises(UnsupportedSnapshotData, match="cutoff"):
        native_client.get_prices("TEST.L", "2026-09-01", "2026-09-17")
    # The bar was observed Sep 14 but only published Sep 15.
    assert native_client.get_prices("TEST.L", "2026-09-01", "2026-09-14") == []


def test_aihf_bridge_never_fabricates_ttm(native_client: AIHedgeFundSnapshotClient) -> None:
    with pytest.raises(UnsupportedSnapshotData, match="cadence"):
        native_client.get_financial_metrics("TEST.L", "2026-09-16")
    with pytest.raises(UnsupportedSnapshotData, match="insider"):
        native_client.get_insider_trades("TEST.L", "2026-09-16")
