"""Research boundary fixtures only; no fixture is a qualified runtime/data proof."""

from datetime import UTC, datetime, timedelta

import pytest

from money.adapters.upstream import UPSTREAM_SHAS
from money.backtest.lean import LeanContainerRunner, LeanContainerSettings
from money.qualification.core import QualificationContext
from money.research.backtesting import (
    ResearchCostAssumptions,
    prepare_backtest,
    run_configured_research_lean,
    run_research_lean,
)
from money.research.market_data import (
    ExactMarketMapping,
    HistoricalMarketBar,
    HistoricalMarketData,
    MarketProof,
)
from money.schemas.contracts import (
    Claim,
    FirmReport,
    InstrumentMetadata,
    PriceBar,
    ResearchSnapshot,
    content_hash,
)

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)


def fixture(*, historical=True, actions=True):
    proof = MarketProof(sha256=content_hash({"fixture": "archive"}), path="artifacts/fixture.json")
    bars = tuple(
        HistoricalMarketBar(
            timestamp=NOW - timedelta(days=150 - index),
            available_at=NOW - timedelta(days=150 - index) + timedelta(hours=1)
            if historical else None,
            price=PriceBar(open=100, high=110, low=90, close=105, volume=1000, currency="GBX"),
        )
        for index in range(120)
    )
    market = HistoricalMarketData(
        mapping=ExactMarketMapping(
            provider="fixture", trading212_id="FIXl_EQ", symbol="FIX.RETURNED", currency="GBX",
            evidence=(proof,),
        ),
        bars=bars, observed_at=NOW, valid_until=NOW + timedelta(days=1),
        proof_refs=(proof,), historical_availability_verified=historical,
        historical_availability_proofs=(proof,) if historical else (),
        corporate_actions_complete=actions,
        adjustment_basis="RAW_NO_ACTIONS" if actions else "UNKNOWN",
    )
    snapshot = ResearchSnapshot(
        purpose="RESEARCH_TESTING", usage_mode="PERSONAL_RESEARCH", qlib_enabled=False,
        universe_hash=content_hash({"fixture": "admitted universe"}),
        snapshot_id="fixture-snapshot", ticker="FIX", created_at=NOW,
        price_cutoff=NOW, news_cutoff=NOW, filing_cutoff=NOW, fundamental_cutoff=NOW,
        instrument=InstrumentMetadata(
            ticker="FIX", company="Fixture", instrument_type="STOCK", quote_currency="GBX",
            currently_available=True, verified_at=NOW, provider="trading212",
            source_id="FIXl_EQ", source="Fixture current metadata",
            activities_verified=False,
        ),
        evidence=market.evidence_records("fixture-snapshot"),
    )
    reports = tuple(
        FirmReport(
            firm=firm, snapshot_id=snapshot.snapshot_id, snapshot_hash=snapshot.hash,
            conclusion="Fixture report for isolated unit test; not a real native run.",
            claims=(Claim(claim_id=firm, family="technical", statement="Fixture price observation",
                          evidence_ids=(snapshot.evidence[0].evidence_id,)),),
            model_version="fixture", prompt_version="fixture", upstream_sha=UPSTREAM_SHAS[firm],
            model_family="fixture", created_at=NOW,
        )
        for firm in ("tradingagents", "ai_hedge_fund")
    )
    costs = ResearchCostAssumptions(
        version="fixture-assumptions", spread_bps=10, slippage_bps=5, fees_bps=2,
        rationale="Explicit hypothetical scenario; not broker observations.",
    )
    return snapshot, reports, market, costs


def test_backtest_ready_needs_no_company_ethics_or_cost_signature():
    snapshot, reports, market, costs = fixture()
    result = prepare_backtest(snapshot, reports, market, costs, first_pass_locked=True)
    assert result.state == "BACKTEST_READY"
    assert not result.blockers
    assert result.assumptions.basis == "ASSUMED_NOT_OBSERVED_BROKER_COSTS"
    assert "reviewed_by" not in result.model_dump_json()
    assert not result.commercial_release_permitted
    with pytest.raises(ValueError, match="COMMERCIAL"):
        snapshot.require_commercial_release()


def test_prices_available_now_are_not_historical_pit_evidence():
    snapshot, reports, market, costs = fixture(historical=False)
    result = prepare_backtest(snapshot, reports, market, costs, first_pass_locked=True)
    assert result.state == "BLOCKED"
    assert result.blockers == ("HISTORICAL_PRICE_AVAILABILITY_EVIDENCE_REQUIRED",)


def test_missing_action_handling_does_not_become_complete():
    snapshot, reports, market, costs = fixture(actions=False)
    result = prepare_backtest(snapshot, reports, market, costs, first_pass_locked=True)
    assert result.blockers == ("STRATEGY_CORPORATE_ACTION_HANDLING_REQUIRED",)


@pytest.mark.parametrize("missing", ["lock", "reports", "market", "costs", "runtime"])
def test_no_lean_execution_without_actual_prerequisites(monkeypatch, missing):
    snapshot, reports, market, costs = fixture()
    settings = LeanContainerSettings(image="fixture/lean@sha256:" + content_hash({"fixture": "image"}))

    def forbidden(*args):
        raise AssertionError("LEAN must not execute")

    monkeypatch.setattr(LeanContainerRunner, "__call__", forbidden)
    result = run_research_lean(
        snapshot, () if missing == "reports" else reports,
        None if missing == "market" else market,
        None if missing == "costs" else costs,
        None if missing == "runtime" else settings,
        first_pass_locked=missing != "lock",
    )
    assert result.state == "BLOCKED"
    assert not result.execution_performed
    assert result.blockers
    assert not result.production_qualified


def test_wrong_security_and_report_identity_remain_blocked():
    snapshot, reports, market, costs = fixture()
    bad = market.model_copy(update={"mapping": market.mapping.model_copy(update={"trading212_id": "OTHER"}), "hash": ""})
    result = prepare_backtest(snapshot, reports, bad, costs, first_pass_locked=True)
    assert "EXACT_MARKET_SECURITY_MAPPING_REQUIRED" in result.blockers
    result = prepare_backtest(snapshot, (reports[0], reports[0]), market, costs, first_pass_locked=True)
    assert "INDEPENDENT_FIRST_PASS_REPORTS_REQUIRED" in result.blockers


def test_wrapper_creates_unsigned_config_does_not_fake_lock_or_costs(tmp_path):
    ctx = QualificationContext(tmp_path, tmp_path, {}, NOW)
    snapshot, reports, market, _ = fixture()
    result = run_configured_research_lean(ctx, snapshot, {
        "complete": True, "state": "FIRST_PASS_LOCKED",
        "reports": [report.model_dump(mode="json") for report in reports],
        "artifacts": [],
    }, market)
    assert not result["complete"]
    assert "FIRST_PASS_LOCKED_REQUIRED" in result["blockers"]
    configuration = ctx.read_json("inputs/research-backtest.json")
    assert configuration["assumptions"] is None
    assert configuration["runtime"] is None
    assert "reviewed_by" not in str(configuration)
    assert "approved" not in str(configuration)


def test_wrapper_finds_genuine_bytes_of_seal_not_last_artifact(tmp_path):
    ctx = QualificationContext(tmp_path, tmp_path, {}, NOW)
    snapshot, reports, market, costs = fixture(historical=False)
    raw_reports = [report.model_dump(mode="json") for report in reports]
    refs = [ctx.artifact(item) for item in raw_reports]
    refs.append(ctx.artifact({
        "state": "FIRST_PASS_LOCKED", "snapshot_hash": snapshot.hash,
        "qlib_enabled": False, "reports": raw_reports,
    }))
    refs.append(ctx.artifact({"state": "comparison", "production_qualified": False}))
    ctx.write_json("inputs/research-backtest.json", {
        "assumptions": costs.model_dump(mode="json"),
    })
    result = run_configured_research_lean(ctx, snapshot, {
        "complete": True, "reports": raw_reports, "artifacts": refs,
    }, market)
    assert "FIRST_PASS_LOCKED_REQUIRED" not in result["blockers"]
    assert "DOCUMENTED_RESEARCH_COST_ASSUMPTIONS_REQUIRED" not in result["blockers"]
    assert "HISTORICAL_PRICE_AVAILABILITY_EVIDENCE_REQUIRED" in result["blockers"]
    assert "PINNED_LEAN_RUNTIME_IMAGE_REQUIRED" in result["blockers"]
