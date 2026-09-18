"""Research admission fixtures are synthetic, never qualification artifacts."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from money.qualification import universe
from money.qualification.core import QualificationContext
from money.qualification.universe_admission import (
    classify_research_admission,
    current_research_rows,
    optional_enrichment_status,
)
from money.qualification.universe_normalize import normalize_universe
from money.qualification.universe_policy import UNIVERSE_POLICY_VERSION

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)


def stock(**changes: Any) -> dict[str, Any]:
    return {
        "ticker": "TESTl_EQ", "type": "STOCK", "currencyCode": "GBX",
        "isin": "GB0006389398", "name": "Synthetic Test PLC", "shortName": "TEST",
        **changes,
    }


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QualificationContext:
    monkeypatch.setattr(universe, "utc_now", lambda: NOW)
    monkeypatch.setattr(universe, "_update_universe_diagnostics", lambda *a, **kw: None)
    return QualificationContext(tmp_path / "bundle", tmp_path, {
        "MONEY_USAGE_MODE": "personal_research",
        "TRADING212_API_KEY": "synthetic-broker-credential-not-real",
        "TRADING212_API_SECRET": "synthetic-broker-secret-not-real",
    }, NOW)


class Broker:
    def __init__(self, instruments: list[dict[str, Any]]) -> None:
        self.instruments = instruments
        self.calls: list[str] = []

    def metadata_response(self, kind: str) -> tuple[bytes, tuple[Any, ...]]:
        assert kind in {"instruments", "exchanges"}  # no execution/account endpoints
        self.calls.append(kind)
        rows = self.instruments if kind == "instruments" else []
        return json.dumps(rows).encode(), tuple(rows)


class NoCompanyEvidence:
    requests_used = 0

    def enrich(self, row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "provider_reasons": ["PROVIDER_ACCESS_DENIED"],
                "provider_request_diagnostics": [{"provider": "eodhd", "endpoint": "fundamentals", "http_status": 403}]}


@pytest.mark.parametrize("currency", ["GBP", "GBX"])
@pytest.mark.parametrize("isin", [None, "", "US0378331005"])
def test_admission_requires_neither_isin_company_nor_venue(currency: str, isin: str | None) -> None:
    row = normalize_universe([stock(currencyCode=currency, isin=isin, name=None)], [], observed_at=NOW)[0]
    classify_research_admission(row, now=NOW, authenticated_live=True)
    assert row["research_state"] == "RESEARCH_ELIGIBLE"
    assert row["research_reasons"] == []


@pytest.mark.parametrize("changes", [{"type": "ETF"}, {"currencyCode": "USD"}, {"ticker": None}, {"isin": "corrupt"}])
def test_basic_nonstock_currency_and_identity_errors_still_block(changes: dict[str, Any]) -> None:
    row = normalize_universe([stock(**changes)], [], observed_at=NOW)[0]
    classify_research_admission(row, now=NOW, authenticated_live=True)
    assert row["research_state"] == "DISCOVERED"
    assert row["research_reasons"]


@pytest.mark.parametrize("ethics", ["FAIL", "UNKNOWN", "NOT_YET_SCREENED", "PASS"])
def test_ethics_is_annotation_not_research_gate(ethics: str) -> None:
    row = normalize_universe([stock()], [], observed_at=NOW)[0]
    row["ethical_state"] = ethics
    row["qualification_state"] = "UNRESOLVED_ETHICAL"
    classify_research_admission(row, now=NOW, authenticated_live=True)
    assert row["research_state"] == "RESEARCH_ELIGIBLE"
    assert row["ethical_status"] == ("NOT_SCREENED" if ethics == "NOT_YET_SCREENED" else ethics)


def test_provider_failure_missing_reviews_and_company_credentials_do_not_block(ctx: QualificationContext) -> None:
    broker = Broker([stock(), stock(ticker="SECOND_EQ", currencyCode="GBP", isin=None)])
    result = universe.finalize_universe(ctx, broker=broker, enricher=NoCompanyEvidence())
    assert result["summary"]["research_eligible"] == 2
    assert result["summary"]["gbp_gbx_stocks"] == 2
    assert result["summary"]["gbp_stocks"] == result["summary"]["gbx_stocks"] == 1
    assert result["eligibility_reviews"] == []
    assert len(current_research_rows(ctx, result)) == 2
    assert result["stocks"][0]["enrichment_status"]["eodhd_fundamentals"] == "ACCESS_DENIED"
    assert result["stocks"][0]["production_qualification_state"] == "NOT_EVALUATED"
    assert broker.calls == ["instruments", "exchanges"]
    assert not ctx._path("inputs/universe/account-scope.json").exists()
    assert ctx.read_json("outputs/universe-review-queue.json")["instruments"] == []


def test_duplicate_does_not_block_other_valid_research_candidates(ctx: QualificationContext) -> None:
    result = universe.finalize_universe(ctx, broker=Broker([
        stock(), stock(ticker="ALIAS_EQ"), stock(ticker="OTHER_EQ", isin="US0378331005")
    ]), enricher=NoCompanyEvidence())
    admitted = current_research_rows(ctx, result)
    assert [row["trading212_id"] for row in admitted] == ["OTHER_EQ"]
    assert result["summary"]["research_identity_conflicts"] == 2


def test_admission_checkpoint_precedes_optional_network_work(ctx: QualificationContext) -> None:
    class InspectCheckpoint(NoCompanyEvidence):
        def enrich(self, row: dict[str, Any]) -> dict[str, Any]:
            checkpoint = ctx.read_json("outputs/research-admission.json")
            assert checkpoint["summary"]["research_eligible"] == 1
            assert checkpoint["enrichment_complete"] is False
            assert len(current_research_rows(ctx, checkpoint)) == 1
            return super().enrich(row)
    universe.finalize_universe(ctx, broker=Broker([stock()]), enricher=InspectCheckpoint())


def test_old_policy_migrates_without_destroying_raw_or_signing_reviews(ctx: QualificationContext) -> None:
    ctx.write_json("state/bulk-universe-mode.json", {"universe_policy_version": "old"})
    review = {"review": {"status": "UNRESOLVED", "reviewed_by": None}}
    ctx.write_json("inputs/universe/account-scope.json", review)
    raw = ctx.artifact(b"Synthetic historical evidence retained unchanged")
    result = universe.finalize_universe(ctx, broker=Broker([stock()]), enricher=NoCompanyEvidence())
    assert result["policy_migration"]["rebuilt"] is True
    assert result["universe_policy_version"] == UNIVERSE_POLICY_VERSION
    assert ctx.read_json("inputs/universe/account-scope.json") == review
    assert ctx.verify_artifact(*raw) == b"Synthetic historical evidence retained unchanged"


def test_stale_replay_and_changed_credentials_cannot_admit(ctx: QualificationContext) -> None:
    result = universe.finalize_universe(ctx, broker=Broker([stock()]), enricher=NoCompanyEvidence())
    ctx.now = NOW + timedelta(days=1)
    with pytest.raises(ValueError, match="CURRENT_AUTHENTICATED"):
        current_research_rows(ctx, result)
    ctx.now = NOW
    replay = {**result, "scope": "SAVED_RESPONSE_REPLAY_ONLY", "status": "REPLAYED"}
    with pytest.raises(ValueError, match="CURRENT_AUTHENTICATED"):
        current_research_rows(ctx, replay)
    ctx.environ["TRADING212_API_SECRET"] = "different-synthetic-secret"
    with pytest.raises(ValueError, match="CURRENT_AUTHENTICATED"):
        current_research_rows(ctx, result)


def test_tampered_projection_and_raw_response_cannot_admit(ctx: QualificationContext) -> None:
    result = universe.finalize_universe(ctx, broker=Broker([stock()]), enricher=NoCompanyEvidence())
    altered = deepcopy(result)
    altered["stocks"][0]["trading212_id"] = "FAKE_EQ"
    with pytest.raises(ValueError, match="PROJECTION_MISMATCH"):
        current_research_rows(ctx, altered)
    ref = result["provenance"]["response_artifacts"]["instruments"]
    ctx.write_bytes(ref[1], b"[]")
    with pytest.raises(ValueError, match="CURRENT_AUTHENTICATED"):
        current_research_rows(ctx, result)


def test_optional_evidence_stale_and_credentials_never_written(ctx: QualificationContext) -> None:
    row = {"provider_datasets": {"eodhd:ohlcv": {"status": "RETRIEVED", "record_count": 1}},
           "provider_evidence_observed_at": (NOW - timedelta(days=2)).isoformat(),
           "provider_evidence_valid_until": (NOW - timedelta(days=1)).isoformat()}
    assert optional_enrichment_status(row, ctx.environ, now=NOW)["eodhd_market_data"] == "STALE"
    universe.finalize_universe(ctx, broker=Broker([stock()]), enricher=NoCompanyEvidence())
    for path in ctx.root.rglob("*"):
        if path.is_file():
            contents = path.read_bytes()
            for name in ("TRADING212_API_KEY", "TRADING212_API_SECRET"):
                assert ctx.environ[name].encode() not in contents
