"""Synthetic fixtures test research boundaries; never emitted as live evidence."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from money.adapters.native import NativeRunSettings, parse_analysis, snapshot_payload
from money.adapters.upstream import InvalidUpstreamReport, UnsupportedSnapshotData
from money.qualification import research_testing, runner, universe
from money.qualification.core import QualificationContext
from money.research.testing_snapshot import build_testing_snapshot, freeze_research_universe
from money.schemas.contracts import ResearchSnapshot, utc_now


class FixtureBroker:
    def metadata_response(self, kind: str) -> tuple[bytes, tuple[Any, ...]]:
        assert kind in {"instruments", "exchanges"}
        rows = [{"ticker": "FIXTUREl_EQ", "type": "STOCK", "currencyCode": "GBP"},
                {"ticker": "OTHERl_EQ", "type": "STOCK", "currencyCode": "GBX"}] if kind == "instruments" else []
        return json.dumps(rows).encode(), tuple(rows)


class NoEnrichment:
    requests_used = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def enrich(self, row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "ethical_state": "FAIL", "provider_reasons": ["PROVIDER_ACCESS_DENIED"]}


@pytest.fixture
def ctx(tmp_path: Path) -> QualificationContext:
    return QualificationContext(tmp_path / "bundle", Path.cwd(), {
        "MONEY_USAGE_MODE": "personal_research", "MONEY_QLIB_ENABLED": "false",
        "MONEY_INFERENCE_CONFIG": "data/configuration/ollama-inference.json",
        "TRADING212_API_KEY": "synthetic-test-key-not-a-real-key",
        "TRADING212_API_SECRET": "synthetic-test-secret-not-a-real-secret",
    }, utc_now())


def master(ctx: QualificationContext) -> dict[str, Any]:
    return universe.finalize_universe(ctx, broker=FixtureBroker(), enricher=NoEnrichment(), max_requests=0)


def snapshot(ctx: QualificationContext) -> ResearchSnapshot:
    rows, ref = freeze_research_universe(ctx, master(ctx))
    return build_testing_snapshot(ctx, rows[0], ref)


def test_freezes_complete_universe_before_one_stock_and_needs_no_company(ctx: QualificationContext) -> None:
    rows, ref = freeze_research_universe(ctx, master(ctx))
    frozen = json.loads(ctx.verify_artifact(*ref))
    assert len(frozen["members"]) == 2
    result = build_testing_snapshot(ctx, rows[0], ref)
    assert result.universe_hash == ref[0]
    assert result.purpose == "RESEARCH_TESTING"
    assert result.qlib_enabled is False
    assert result.instrument.isa_available is None
    assert result.instrument.activities_verified is False
    assert len(result.evidence) == 1
    assert result.evidence[0].payload.kind == "instrument_metadata"
    assert result.evidence[0].publication_time is None
    assert result.evidence[0].pit_safe is False
    assert "NO_FINANCIAL_EVIDENCE" in result.missing_data
    with pytest.raises(ValueError, match="FORBIDDEN"):
        result.require_commercial_release()


def test_current_research_prompt_exposes_missing_data_not_fake_fundamentals(ctx: QualificationContext) -> None:
    result = snapshot(ctx)
    prompt = json.loads(snapshot_payload(result))
    assert prompt["purpose"] == "RESEARCH_TESTING"
    assert prompt["missing_data"]
    assert prompt["evidence"][0]["historical_pit_verified"] is False
    assert all(item["status"] == "UNAVAILABLE" for item in prompt["enrichment"])
    assert "claims" not in prompt


def test_unverified_pit_not_admitted_to_historical_or_production_prompts(ctx: QualificationContext) -> None:
    result = snapshot(ctx)
    with pytest.raises(ValueError, match="unsafe historical"):
        ResearchSnapshot.model_validate({**result.model_dump(), "historical": True, "hash": ""})
    strict = ResearchSnapshot.model_validate({**result.model_dump(), "purpose": "PRODUCTION_QUALIFICATION", "hash": ""})
    with pytest.raises(UnsupportedSnapshotData):
        snapshot_payload(strict)


def test_expired_membership_or_mutated_identity_cannot_build_snapshot(ctx: QualificationContext) -> None:
    rows, ref = freeze_research_universe(ctx, master(ctx))
    with pytest.raises(ValueError, match="NOT_IN_FROZEN"):
        build_testing_snapshot(ctx, {**rows[0], "quote_currency": "USD"}, ref)
    ctx.now += timedelta(days=1)
    with pytest.raises(ValueError, match="EXPIRED"):
        build_testing_snapshot(ctx, rows[0], ref)


def test_research_facets_must_reference_real_claims(ctx: QualificationContext) -> None:
    result = snapshot(ctx)
    data = {"conclusion": "Only membership observed.", "claims": [{
        "claim_id": "limit", "family": "risk", "statement": "No company data supplied.",
        "evidence_ids": [result.evidence[0].evidence_id],
    }], "analysis": {"risks": ["limit"], "missing_data": ["fundamentals"], "confidence": "LOW"}}
    assert parse_analysis(json.dumps(data), result).analysis.confidence == "LOW"
    data["analysis"]["risks"] = ["fabricated"]
    with pytest.raises(ValueError, match="UNKNOWN_CLAIM"):
        parse_analysis(json.dumps(data), result)
    data.pop("analysis")
    with pytest.raises(InvalidUpstreamReport, match="structured analysis"):
        parse_analysis(json.dumps(data), result)


def test_personal_runner_reaches_native_without_company_and_does_not_run_lean_early(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from money.qualification import universe_providers
    from money.research import independent

    existing = master(ctx)
    monkeypatch.setattr(universe, "finalize_universe", lambda *args, **kwargs: existing)
    monkeypatch.setattr(universe_providers, "BulkProviderEnricher", NoEnrichment)
    calls = []

    def native(context: QualificationContext, frozen: ResearchSnapshot, *args: Any) -> dict[str, Any]:
        calls.append(frozen)
        context.block("LOCAL_NATIVE_SOURCE_REQUIRED_TRADINGAGENTS", "Install pinned source.")
        return {"complete": False, "reports": []}

    monkeypatch.setattr(independent, "run_local_independent_research", native)
    monkeypatch.setattr(research_testing, "_run_lean", lambda *args: pytest.fail("LEAN ran without first pass"))
    result = runner.run(ctx)
    assert len(calls) == 1
    assert result["state"] == "RESEARCH_ELIGIBLE"
    assert result["research_eligible"] == 2
    assert result["manifest_sha256"] is None
    assert result["production_ready"] is False
    assert [b["code"] for b in result["blockers"]] == ["LOCAL_NATIVE_SOURCE_REQUIRED_TRADINGAGENTS"]
    dag = ctx.read_json("outputs/qualification-blocker-dag.json")
    assert next(s for s in dag["stages"] if s["id"] == "snapshot")["requires"] == ["basic_identity"]
    assert not ctx._path("manifest.json").exists()


@pytest.mark.parametrize("configured,expected", [(None, 900), (600, 600), (1800, 1800)])
def test_whole_agent_budget_is_separate_from_http_timeout_and_call_cap(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch, configured: int | None, expected: int,
) -> None:
    from money.qualification import universe_providers
    from money.research import independent

    if configured is not None:
        ctx.environ = dict(ctx.environ) | {"MONEY_RESEARCH_AGENT_TIMEOUT_SECONDS": str(configured)}
    existing = master(ctx)
    monkeypatch.setattr(universe, "finalize_universe", lambda *a, **k: existing)
    monkeypatch.setattr(universe_providers, "BulkProviderEnricher", NoEnrichment)
    recorded = []

    def native(context: QualificationContext, frozen: ResearchSnapshot, selections: dict,
               limits: NativeRunSettings) -> dict[str, Any]:
        recorded.append(limits)
        assert limits.timeout_seconds == expected
        assert limits.max_calls == 24 and limits.verify_source_pin
        assert all(s.timeout_seconds == 180 for s in selections.values())
        assert all(s.inference({}).configuration.timeout_seconds == 180 for s in selections.values())
        assert all(s.reasoning_effort == "none" for s in selections.values())
        context.block("LOCAL_NATIVE_REPORT_REQUIRED", "Fixture does not execute native inference.")
        return {"complete": False}

    monkeypatch.setattr(independent, "run_local_independent_research", native)
    monkeypatch.setattr(research_testing, "_run_lean", lambda *a: pytest.fail("LEAN ran without reports"))
    result = runner.run(ctx)
    assert len(recorded) == 1
    assert result["state"] == "RESEARCH_ELIGIBLE"
    assert result["qlib_enabled"] is False and result["lean_mandatory"] is True
    assert ctx.read_json("outputs/research-mode.json")["agent_timeout_seconds"] == expected
    assert ctx.read_json("outputs/research-mode.json")["native_max_calls"] == 24
    assert not ctx._path("manifest.json").exists()
    # The local runner passes explicit settings, never changing hosted defaults.
    assert NativeRunSettings().timeout_seconds == 180


@pytest.mark.parametrize("value", ["0", "-1", "1801", "1.5", "NaN", "fixture-private-invalid"])
def test_invalid_agent_budget_rejected_before_any_retrieval_or_native_stage(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch, value: str, capsys: Any,
) -> None:
    ctx.environ = dict(ctx.environ) | {"MONEY_RESEARCH_AGENT_TIMEOUT_SECONDS": value}
    monkeypatch.setattr(universe, "finalize_universe", lambda *a, **k: pytest.fail("retrieval ran"))
    assert runner.main(["--output", str(ctx.root)], environment=ctx.environ) == 2
    output = capsys.readouterr().out
    assert "RUNNER_INITIALIZATION_FAILED" in output
    assert value not in output


def test_no_current_universe_stops_every_native_and_backtest_stage(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from money.research import independent

    monkeypatch.setattr(universe, "finalize_universe", lambda *a, **k: {"status": "REFRESH_FAILED"})
    monkeypatch.setattr(independent, "run_local_independent_research", lambda *a: pytest.fail("Native ran"))
    result = runner.run(ctx)
    assert result["state"] == "DISCOVERED"
    assert result["research_eligible"] == 0
    assert result["blockers"][0]["code"] == "TRADING212_LIVE_METADATA_REQUIRED"


def test_secret_bearing_native_failure_never_saved_or_printed(ctx: QualificationContext, capsys: Any) -> None:
    secret = ctx.environ["TRADING212_API_SECRET"]

    def broken() -> dict[str, Any]:
        print(secret)
        raise ValueError(secret)

    assert runner._execute(ctx, "research-test", broken) == {}
    assert secret not in capsys.readouterr().out
    for file in ctx.root.rglob("*"):
        if file.is_file():
            assert secret.encode() not in file.read_bytes()


def test_unknown_optional_company_data_cannot_grant_commercial_manifest(ctx: QualificationContext) -> None:
    assert runner.assemble_manifest(ctx, []) is None
    assert ctx.blockers[0]["code"] == "PERSONAL_RESEARCH_COMMERCIAL_RELEASE_FORBIDDEN"


def test_large_universe_output_does_not_hit_small_proof_file_limit(ctx: QualificationContext) -> None:
    # A complete broker projection is larger than one evidence blob. Its
    # separate bounded writer must not turn successful retrieval into failure.
    value = {"stocks": [{"description": "synthetic" * 300_000}]}
    assert runner._execute(ctx, "research-universe", lambda: value) == value
    assert ctx.blockers == []


def test_malformed_optional_reports_leave_valid_broker_snapshot(ctx: QualificationContext) -> None:
    rows, ref = freeze_research_universe(ctx, master(ctx))
    result = build_testing_snapshot(ctx, {**rows[0], "provider_reports": ["invalid"]}, ref)
    assert len(result.evidence) == 1
    assert "OPTIONAL_PROVIDER_REPORTS_MALFORMED" in result.missing_data


def test_optional_provider_exception_does_not_prevent_independent_research(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from money.qualification import universe_providers
    from money.research import independent

    existing = master(ctx)
    monkeypatch.setattr(universe, "finalize_universe", lambda *a, **k: existing)

    class Broken(NoEnrichment):
        def enrich(self, row: dict[str, Any]) -> dict[str, Any]:
            raise ValueError("synthetic optional acquisition error")

    monkeypatch.setattr(universe_providers, "BulkProviderEnricher", Broken)
    calls = []

    def native(context: QualificationContext, frozen: ResearchSnapshot, *args: Any) -> dict[str, Any]:
        calls.append(frozen.hash)
        context.block("LOCAL_NATIVE_REPORT_REQUIRED", "Fixture runtime intentionally unavailable")
        return {"complete": False}

    monkeypatch.setattr(independent, "run_local_independent_research", native)
    result = runner.run(ctx)
    assert len(calls) == 1
    assert result["state"] == "RESEARCH_ELIGIBLE"
    assert result["blockers"][0]["code"] == "LOCAL_NATIVE_REPORT_REQUIRED"


def test_invalid_optional_market_config_provides_no_prices_but_keeps_admission(ctx: QualificationContext) -> None:
    from money.research.market_data import load_research_market_data

    ctx.write_json("inputs/research-market-data.json", {"providers": ["unsupported"]})
    rows, ref = freeze_research_universe(ctx, master(ctx))
    acquired = load_research_market_data(ctx, rows[0])
    assert acquired.dataset is None
    assert acquired.attempts[0].code == "MARKET_CONFIGURATION_INVALID"
    assert build_testing_snapshot(ctx, rows[0], ref).purpose == "RESEARCH_TESTING"
