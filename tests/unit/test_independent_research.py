"""Local execution tests use explicit fakes, never production evidence artifacts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from money.adapters.native import NativeRunSettings
from money.adapters.upstream import UPSTREAM_SHAS
from money.data.security import ProviderFailure
from money.qualification.core import QualificationContext
from money.research import independent
from money.research.call_telemetry import InferenceReceipt, capture_calls, emit_calls
from money.research.inference_config import InferenceSelection
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    Claim,
    FirmReport,
    ResearchSnapshot,
    TradingAgentsResearchReport,
    content_hash,
)

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QualificationContext:
    monkeypatch.setattr(independent, "utc_now", lambda: NOW)
    return QualificationContext(tmp_path / "output", Path.cwd(), {
        "MONEY_USAGE_MODE": "personal_research",
        "TRADING212_API_KEY": "synthetic-native-broker-key-not-real",
        "TRADING212_API_SECRET": "synthetic-native-broker-secret-not-real",
    }, NOW)


@pytest.fixture
def snapshot(context: QualificationContext, monkeypatch: pytest.MonkeyPatch) -> ResearchSnapshot:
    """Synthetic broker transport exercises actual full-universe freezing contracts."""
    from money.qualification import universe
    from money.research.testing_snapshot import build_testing_snapshot, freeze_research_universe

    class Broker:
        def metadata_response(self, kind: str) -> tuple[bytes, tuple[Any, ...]]:
            assert kind in {"instruments", "exchanges"}
            items = [
                {"ticker": "EXAMPLEl_EQ", "name": "Unit test fixture issuer", "type": "STOCK", "currencyCode": "GBX"},
                {"ticker": "ANOTHER_EQ", "name": "Another unit test fixture", "type": "STOCK", "currencyCode": "GBP"},
            ] if kind == "instruments" else []
            return json.dumps(items).encode(), tuple(items)

    class Enricher:
        requests_used = 0

        def enrich(self, row: dict[str, Any]) -> dict[str, Any]:
            return row

    monkeypatch.setattr(universe, "utc_now", lambda: NOW)
    master = universe.finalize_universe(context, broker=Broker(), enricher=Enricher())
    rows, frozen = freeze_research_universe(context, master)
    row = next(row for row in rows if row["trading212_id"] == "EXAMPLEl_EQ")
    return build_testing_snapshot(context, row, frozen)


@pytest.fixture
def selections() -> dict[str, InferenceSelection]:
    selected = InferenceSelection(
        provider="ollama",
        model="qwen3:14b",
        endpoint="http://127.0.0.1:11434/v1/chat/completions",
        endpoint_scope="local",
        authentication="none",
    )
    return dict.fromkeys(("tradingagents", "ai_hedge_fund"), selected)


def report(firm: str, snapshot: ResearchSnapshot, statement: str | None = None) -> FirmReport:
    schema = TradingAgentsResearchReport if firm == "tradingagents" else AIHedgeFundResearchReport
    return schema(
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.hash,
        conclusion="Only current membership is documented in this unit fixture.",
        claims=(
            Claim(
                claim_id=firm + "-claim",
                family="risk",
                statement=statement or "Business fundamentals are absent from this snapshot.",
                evidence_ids=(snapshot.evidence[0].evidence_id,),
            ),
        ),
        model_version="unit-fixture",
        prompt_version="unit-fixture",
        upstream_sha=UPSTREAM_SHAS[firm],
        model_family="unit-fixture",
        created_at=NOW,
    )


def ready(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    prepared = {
        "complete": True,
        "runtimes": {firm: {"complete": True, "errors": []} for firm in
                     ("tradingagents", "ai_hedge_fund")},
        "pins": {"tradingagents": "unit-fixture", "hedge_fund": "unit-fixture"},
        "execution_identity": "unit-fixture",
        "security": {"passed": True, "findings": [], "artifacts": []},
        "artifacts": [],
    }
    monkeypatch.setattr(
        independent, "prepare_native_environments", lambda ctx: prepared,
    )
    return prepared


def test_two_native_firms_seal_same_snapshot_without_company_reviews(
    context: QualificationContext,
    snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready(monkeypatch)
    calls: list[tuple[str, str]] = []

    def native(ctx: QualificationContext, firm: str, frozen: ResearchSnapshot, *args: Any) -> FirmReport:
        assert frozen.model_dump_json() == snapshot.model_dump_json()
        assert "claims" not in frozen.model_dump()
        calls.append((firm, frozen.hash))
        return report(firm, frozen)

    monkeypatch.setattr(independent, "_report", native)
    result = independent.run_local_independent_research(context, snapshot, selections)
    assert result["complete"] is True
    assert result["state"] == "FIRST_PASS_LOCKED"
    assert result["qlib_enabled"] is False
    assert calls == [("tradingagents", snapshot.hash), ("ai_hedge_fund", snapshot.hash)]
    assert len(result["reports"]) == 2
    assert result["production_qualified"] is False
    assert all(run["status"] == "SUCCEEDED" for run in result["firm_runs"])
    assert all(run["duration_seconds"] >= 0 for run in result["firm_runs"])
    comparison = context.read_json("outputs/research-comparison.json")
    assert comparison["status"] == "LIMITED_COMPARISON"
    assert comparison["missing_data"] == list(snapshot.missing_data)
    assert comparison["lean_validated"] is False
    assert comparison["cio_completed"] is False
    assert len(comparison["agreements"]) == 1
    assert not context.blockers


def test_native_source_and_security_remain_required_without_enrichment(
    context: QualificationContext,
    snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = ready(monkeypatch)
    prepared["complete"] = False
    for runtime in prepared["runtimes"].values():
        runtime.update(complete=False, errors=["NATIVE_SOURCE_DIGEST_MISMATCH"])
    monkeypatch.setattr(independent, "_report", lambda *args: pytest.fail("native ran without source"))
    result = independent.run_local_independent_research(context, snapshot, selections)
    assert result["complete"] is False
    assert len(context.blockers) == 2
    assert all("LOCAL_NATIVE_ENVIRONMENT_REQUIRED_" in item["code"] for item in context.blockers)
    assert all("NATIVE_SOURCE_DIGEST_MISMATCH" in item["action"] for item in context.blockers)


def test_security_findings_are_not_suppressed(
    context: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = ready(monkeypatch)
    findings = [{"name": "chromadb", "advisories": ["unit-advisory"]}]
    prepared["security"] = {"passed": False, "findings": findings, "artifacts": []}
    prepared["complete"] = False
    prepared["runtimes"]["tradingagents"].update(
        complete=False, errors=["LOCAL_NATIVE_DEPENDENCY_SECURITY_REQUIRED"],
    )
    result = independent.run_local_native_preflight(context)
    assert result["complete"] is False
    assert result["security"]["findings"] == findings
    assert context.blockers[0]["code"] == "LOCAL_NATIVE_DEPENDENCY_SECURITY_REQUIRED"
    assert result["hosted_egress_qualified"] is False


def test_each_report_is_preserved_and_only_missing_firm_resumes(
    context: QualificationContext,
    snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = ready(monkeypatch)
    calls: list[str] = []

    def native(ctx: QualificationContext, firm: str, frozen: ResearchSnapshot, *args: Any) -> FirmReport:
        calls.append(firm)
        if firm == "ai_hedge_fund" and calls.count(firm) == 1:
            raise TimeoutError("fixture timeout")
        return report(firm, frozen)

    monkeypatch.setattr(independent, "_report", native)
    first = independent.run_local_independent_research(context, snapshot, selections)
    assert first["complete"] is False
    assert first["debate_completed"] is False
    assert len(first["reports"]) == 1
    failed = next(run for run in first["firm_runs"] if run["firm"] == "ai_hedge_fund")
    assert failed["error_code"] == "NATIVE_AGENT_TIMEOUT"
    assert failed["call_accounting_complete"] is False
    first_bytes = context.read_bytes("outputs/research-reports/tradingagents.json")
    # The next real audit emits a new receipt timestamp/hash, but the same
    # approved source/runtime and frozen snapshot must reuse the original firm.
    prepared["security"]["artifacts"] = [context.artifact({"fresh_audit_fixture": True})]
    second = independent.run_local_independent_research(context, snapshot, selections)
    assert second["complete"] is True
    assert calls == ["tradingagents", "ai_hedge_fund", "ai_hedge_fund"]
    cached = next(run for run in second["firm_runs"] if run["firm"] == "tradingagents")
    assert cached["cache_hit"] is True
    assert cached["llm_calls_recorded"] == 0
    assert context.read_bytes("outputs/research-reports/tradingagents.json") == first_bytes


def test_failed_inference_receipts_reach_both_firm_diagnostics_and_outer_accounting(
    context: QualificationContext, snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection], monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready(monkeypatch)

    def native(ctx: QualificationContext, firm: str, frozen: ResearchSnapshot, *args: Any) -> FirmReport:
        selected = selections[firm]
        emit_calls([InferenceReceipt(
            provider=selected.provider, model=selected.model, status="FAILED",
            duration_seconds=0.25, error_code="PROVIDER_TIMEOUT",
        )])
        raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True)

    monkeypatch.setattr(independent, "_report", native)
    with capture_calls() as calls:
        result = independent.run_local_independent_research(context, snapshot, selections)
    assert len(calls) == 2
    assert result["complete"] is False
    assert result["reports"] == []
    for run in result["firm_runs"]:
        assert run["llm_calls_recorded"] == 1
        assert run["call_accounting_complete"] is True
        assert run["calls"][0]["duration_seconds"] == 0.25
        assert run["error_code"] == "PROVIDER_TIMEOUT"


def test_failed_new_audit_cannot_resume_previously_valid_reports(
    context: QualificationContext,
    snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = ready(monkeypatch)
    monkeypatch.setattr(independent, "_report", lambda ctx, firm, frozen, *args: report(firm, frozen))
    assert independent.run_local_independent_research(context, snapshot, selections)["complete"]
    original = context.read_bytes("outputs/research-reports/tradingagents.json")
    prepared["complete"] = False
    prepared["security"]["passed"] = False
    prepared["runtimes"]["tradingagents"].update(
        complete=False, errors=["LOCAL_NATIVE_DEPENDENCY_SECURITY_REQUIRED"],
    )
    monkeypatch.setattr(independent, "_report", lambda *args: pytest.fail("failed audit executed"))
    result = independent.run_local_independent_research(context, snapshot, selections)
    assert not result["complete"]
    assert not result["reports"]
    assert not result["debate_completed"]
    assert context.read_bytes("outputs/research-reports/tradingagents.json") == original


def test_comparison_failure_preserves_reports_without_first_pass_lock(
    context: QualificationContext,
    snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready(monkeypatch)
    monkeypatch.setattr(independent, "_report", lambda ctx, firm, frozen, *args: report(firm, frozen))

    def unavailable(*args: Any) -> None:
        raise ValueError("Fixture comparison could not validate")

    monkeypatch.setattr(independent, "compare_independent_reports", unavailable)
    result = independent.run_local_independent_research(context, snapshot, selections)
    assert len(result["reports"]) == 2
    assert result["complete"] is False
    assert result["state"] == "RESEARCH_ELIGIBLE"
    assert result["debate_completed"] is False
    assert context.blockers[-1]["code"] == "LOCAL_NATIVE_COMPARISON_REQUIRED"
    for reference in result["artifacts"]:
        assert json.loads(context.verify_artifact(*reference)).get("state") != "FIRST_PASS_LOCKED"
    assert context.read_json("outputs/research-comparison.json") is None


def test_membership_expiring_during_installation_still_stops_native(
    context: QualificationContext,
    snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready(monkeypatch)
    monkeypatch.setattr(independent, "utc_now", lambda: NOW + timedelta(days=1))
    monkeypatch.setattr(independent, "_report", lambda *args: pytest.fail("expired membership ran"))
    with pytest.raises(ValueError, match="CURRENT_BROKER_MEMBERSHIP"):
        independent.run_local_independent_research(context, snapshot, selections)


@pytest.mark.parametrize("firm", ["tradingagents", "ai_hedge_fund"])
def test_each_report_uses_only_its_verified_interpreter_and_selected_credential(
    context: QualificationContext,
    snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection],
    monkeypatch: pytest.MonkeyPatch,
    firm: str,
) -> None:
    received = {}

    class Runtime:
        def __init__(self, **kwargs: Any) -> None:
            received.update(kwargs)

        def __call__(self, mandate: Any, frozen: ResearchSnapshot) -> FirmReport:
            assert frozen == snapshot
            return report(firm, frozen)

    monkeypatch.setattr(independent, "IsolatedNativeRunner", Runtime)
    context.environ["OPENAI_API_KEY"] = "UNIT_TEST_UNRELATED_KEY_DO_NOT_FORWARD"
    environment = ".venv-tradingagents" if firm == "tradingagents" else ".venv-ai-hedge-fund"
    runtime = {
        "complete": True, "security": {"passed": True},
        "python": str(context.repo / environment / "bin/python"),
        "python_version": "3.12.14", "inventory_sha256": content_hash("fixture inventory"),
        "environment_sha256": content_hash("fixture environment"),
    }
    result = independent._report(
        context, firm, snapshot, selections[firm], NativeRunSettings(), runtime,
        content_hash("fixture bridge"),
    )
    assert result.firm == firm
    assert received["interpreter"] == Path(runtime["python"])
    assert received["role"] == firm
    assert received["credential"] is None
    assert "environ" not in received
    assert "peer_reports" not in received
    assert received["expected_inventory_sha256"] == runtime["inventory_sha256"]


def test_preflight_audits_isolated_closures_not_unused_shared_packages(
    context: QualificationContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from money.qualification import native

    ready(monkeypatch)
    monkeypatch.setattr(native, "_security", lambda *args: pytest.fail("shared closure was used"))
    result = independent.run_local_native_preflight(context)
    assert result["complete"] is True
    assert set(result["runtimes"]) == {"tradingagents", "ai_hedge_fund"}
    assert result["production_qualified"] is False
    assert result["hosted_egress_qualified"] is False


def test_wrong_snapshot_report_cannot_unlock_first_pass(
    context: QualificationContext,
    snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready(monkeypatch)
    monkeypatch.setattr(
        independent,
        "_report",
        lambda ctx, firm, frozen, *args: report(firm, frozen).model_copy(
            update={"snapshot_hash": content_hash("wrong snapshot fixture")}
        ),
    )
    result = independent.run_local_independent_research(context, snapshot, selections)
    assert result["state"] == "RESEARCH_ELIGIBLE"
    assert result["reports"] == []
    assert result["debate_completed"] is False


def test_exception_secrets_do_not_reach_artifacts_or_blockers(
    context: QualificationContext,
    snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ready(monkeypatch)
    sentinel = "UNIT_TEST_SECRET_DO_NOT_LOG_VALUE"
    context.environ["OPENAI_API_KEY"] = sentinel

    def fails(*args: Any) -> FirmReport:
        raise ValueError(sentinel)

    monkeypatch.setattr(independent, "_report", fails)
    independent.run_local_independent_research(context, snapshot, selections)
    assert sentinel not in json.dumps(context.blockers)
    assert sentinel not in capsys.readouterr().out
    for path in context.root.rglob("*.json"):
        assert sentinel.encode() not in path.read_bytes()


@pytest.mark.parametrize("age", [timedelta(hours=24), timedelta(days=3)])
def test_stale_membership_stops_native_execution(
    context: QualificationContext,
    snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection],
    age: timedelta,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context.now += age
    monkeypatch.setattr(
        independent, "run_local_native_preflight", lambda *args: pytest.fail("stale snapshot ran")
    )
    with pytest.raises(ValueError, match="CURRENT_BROKER_MEMBERSHIP"):
        independent.run_local_independent_research(context, snapshot, selections)


def test_disabling_source_attestation_is_not_a_research_option(
    context: QualificationContext,
    snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection],
) -> None:
    with pytest.raises(ValueError, match="SOURCE_ATTESTATION"):
        independent.run_local_independent_research(
            context, snapshot, selections, NativeRunSettings(verify_source_pin=False)
        )


def test_comparison_preserves_disagreement_without_majority_or_confidence_averaging(
    snapshot: ResearchSnapshot,
) -> None:
    reports = [
        report("tradingagents", snapshot, "No business data means the thesis cannot be verified."),
        report("ai_hedge_fund", snapshot, "The listed company requires further investigation."),
    ]
    original = [value.model_dump_json() for value in reports]
    compared = independent.compare_independent_reports(snapshot, reports)
    assert compared["agreements"] == []
    assert len(compared["differences_requiring_assessment"]) == 1
    assert compared["confidence"] == "NOT_AGGREGATED"
    assert compared["thesis_robustness"] == "NOT_ESTABLISHED_BY_TEXT_COMPARISON"
    assert [value.model_dump_json() for value in reports] == original


def test_comparison_requires_both_sealed_firms(snapshot: ResearchSnapshot) -> None:
    with pytest.raises(ValueError, match="BOTH_INDEPENDENT"):
        independent.compare_independent_reports(snapshot, [report("tradingagents", snapshot)])


@pytest.mark.parametrize("defect", ["missing_hash", "partial_universe", "replay", "unbound", "changed_credential", "changed_member"])
def test_native_requires_actual_complete_authenticated_universe(
    context: QualificationContext, snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection], monkeypatch: pytest.MonkeyPatch,
    defect: str,
) -> None:
    monkeypatch.setattr(independent, "run_local_native_preflight", lambda *args: pytest.fail("Invalid membership reached native preflight"))
    raw = json.loads(context.verify_artifact(snapshot.universe_hash, "artifacts/" + snapshot.universe_hash + ".json"))
    if defect == "missing_hash":
        digest = content_hash("missing synthetic fixture artifact")
    else:
        if defect == "partial_universe":
            raw["members"] = [member for member in raw["members"] if member["trading212_id"] == "EXAMPLEl_EQ"]
        elif defect in {"replay", "unbound"}:
            provenance = json.loads(context.verify_artifact(*raw["source_provenance"]))
            if defect == "replay":
                provenance["scope"] = "SAVED_RESPONSE_REPLAY_ONLY"
            else:
                provenance["credential_binding_verified_this_run"] = False
            raw["source_provenance"] = context.artifact(provenance)
        elif defect == "changed_credential":
            context.environ["TRADING212_API_KEY"] = "different-synthetic-broker-key"
        elif defect == "changed_member":
            raw["members"][0]["name"] = "Forged fixture identity"
        digest = context.artifact(raw)[0]
    altered = ResearchSnapshot.model_validate({
        **snapshot.model_dump(), "universe_hash": digest, "hash": "",
    })
    with pytest.raises(ValueError, match="AUTHENTICATED_UNIVERSE_BINDING"):
        independent.run_local_independent_research(context, altered, selections)


def test_matching_hash_does_not_authorize_invented_metadata_record(
    context: QualificationContext, snapshot: ResearchSnapshot,
    selections: dict[str, InferenceSelection], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(independent, "run_local_native_preflight", lambda *args: pytest.fail("Forged metadata reached native preflight"))
    value = snapshot.model_dump()
    value["evidence"][0]["payload"]["excerpt"] = json.dumps({"invented": "unit test"})
    value["evidence"][0]["hash"] = ""
    value["hash"] = ""
    altered = ResearchSnapshot.model_validate(value)
    with pytest.raises(ValueError, match="AUTHENTICATED_UNIVERSE_BINDING"):
        independent.run_local_independent_research(context, altered, selections)
