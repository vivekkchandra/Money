"""Synthetic contracts only: disabled Qlib never substitutes for qualification."""

from dataclasses import replace
from typing import Any

import pytest
from pydantic import ValidationError
from test_filing_documents import reviewed_manifest

from money.adapters.upstream import (
    InvalidUpstreamReport,
    LeanAdapter,
    QlibAdapter,
    UpstreamUnavailable,
)
from money.api.settings import Settings
from money.crews.cio import deterministic_audit
from money.crews.cross_examination import run_cross_examination
from money.flows.research import build_runtime
from money.policy.governance import consensus, evidence_independence
from money.research import live, preflight
from money.schemas.contracts import ResearchMandate, ResearchSnapshot, content_hash, utc_now


def fixture(enabled: bool = False):
    runtime = build_runtime("demo")
    instrument = runtime.eligibility.get_instrument_metadata("DEMO.L")
    assert instrument is not None
    original = runtime.snapshot_builder(instrument)
    snapshot = ResearchSnapshot.model_validate(
        original.model_dump() | {"qlib_enabled": enabled, "hash": ""}
    )
    firms = tuple(firm for firm in runtime.firms if firm.firm in snapshot.required_first_pass_firms)
    reports = tuple(firm.research(ResearchMandate(), snapshot) for firm in firms)
    lean = runtime.validate(snapshot, reports)
    audit = runtime.audit(snapshot, reports, lean)
    return replace(runtime, qlib_enabled=enabled, firms=firms), snapshot, reports, lean, audit


def test_legacy_snapshot_hash_unchanged_and_disabled_mode_is_bound() -> None:
    _, enabled, _, _, _ = fixture(True)
    assert "qlib_enabled" not in enabled.model_dump()
    assert enabled.hash == content_hash(enabled.model_dump(mode="json", exclude={"hash"}))
    disabled = ResearchSnapshot.model_validate(
        enabled.model_dump() | {"qlib_enabled": False, "hash": ""}
    )
    assert disabled.hash != enabled.hash
    assert disabled.model_dump()["qlib_enabled"] is False
    assert disabled.required_first_pass_firms == {"tradingagents", "ai_hedge_fund"}
    with pytest.raises(ValidationError, match="hash mismatch"):
        ResearchSnapshot.model_validate(disabled.model_dump() | {"qlib_enabled": True})


def test_disabled_mode_cio_still_audits_mandatory_lean() -> None:
    _, snapshot, reports, lean, _ = fixture()
    findings = deterministic_audit(snapshot, reports, lean)
    assert any(item.auditor == "LEAN Auditor" and item.state == "UNSUPPORTED" for item in findings)
    assert all(item.auditor != "Qlib Auditor" for item in findings)
    with pytest.raises(InvalidUpstreamReport, match="configured"):
        deterministic_audit(snapshot, reports[:1], lean)


def test_disabled_snapshot_cannot_invoke_qlib_directly() -> None:
    _, snapshot, _, _, _ = fixture()
    calls = []
    adapter = QlibAdapter(lambda facts: calls.append(facts))
    with pytest.raises(UpstreamUnavailable, match="QLIB_EXPLICITLY_DISABLED"):
        adapter.research(ResearchMandate(), snapshot)
    assert calls == []


def test_disabled_mode_consensus_does_not_replace_lean_with_two_firm_agreement() -> None:
    runtime, snapshot, reports, lean, audit = fixture()
    _, reasons = consensus(
        ResearchMandate(),
        snapshot,
        reports,
        lean,
        audit,
        runtime.red_team(snapshot, reports),
        evidence_independence(snapshot, reports),
        utc_now(),
    )
    assert "FIRST_PASS_INCOMPLETE" not in reasons
    assert "LEAN_INSUFFICIENT_EVIDENCE" in reasons


def test_disabled_cross_examination_has_exact_two_sealed_hashes() -> None:
    _, snapshot, reports, lean, audit = fixture()
    audit = audit.model_copy(update={"completed": True})
    packet = run_cross_examination(snapshot, reports, lean, audit, first_pass_locked=True)
    assert packet.qlib_enabled is False
    assert set(dict(packet.report_hashes)) == snapshot.required_first_pass_firms
    assert len(packet.rounds) <= 2
    with pytest.raises(ValidationError, match="configured"):
        type(packet).model_validate(packet.model_dump() | {"qlib_enabled": True, "hash": ""})


@pytest.mark.parametrize(
    "change",
    [
        {"qlib_enabled": True, "qlib_registry_id": None},
        {"qlib_enabled": True, "qlib_artifact_hash": None},
        {"qlib_enabled": False},
        {"qlib_enabled": "false"},
    ],
)
def test_manifest_rejects_inconsistent_or_coerced_qlib_selection(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        live.LiveManifest.model_validate(reviewed_manifest().model_dump() | change)


def disabled_manifest():
    return live.LiveManifest.model_validate(
        reviewed_manifest().model_dump()
        | {
            "qlib_enabled": False,
            "qlib_registry_id": None,
            "qlib_artifact_hash": None,
        }
    )


def test_disabled_manifest_still_requires_lean_qualification() -> None:
    manifest = disabled_manifest()
    assert manifest.qlib_enabled is False
    for field in ("lean", "lean_costs", "lean_parameters", "lean_qualification"):
        values = manifest.model_dump()
        values.pop(field)
        with pytest.raises(ValidationError):
            live.LiveManifest.model_validate(values)


def test_disabled_live_runtime_never_loads_registry_or_constructs_qlib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MONEY_QLIB_ENABLED", raising=False)
    manifest = disabled_manifest()
    monkeypatch.setenv(
        manifest.tradingagents.credential_environment_variable, "synthetic-unit-credential"
    )

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("Disabled Qlib must not load a model or construct a runner")

    monkeypatch.setattr(live.ModelRegistry, "load_active", forbidden)
    monkeypatch.setattr(live, "QlibNativeRunner", forbidden)
    monkeypatch.setattr(live, "LiveSnapshotBuilder", lambda *args, **kwargs: object())
    monkeypatch.setattr(live, "Trading212MetadataProvider", lambda *args, **kwargs: object())
    runtime = live.build_live_runtime(object(), manifest)
    assert runtime.qlib_enabled is False
    assert {firm.firm for firm in runtime.firms} == {"tradingagents", "ai_hedge_fund"}
    assert isinstance(runtime.validate.__self__, LeanAdapter)
    assert runtime.provenance.qlib_enabled is False


def test_live_env_cannot_override_pinned_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONEY_QLIB_ENABLED", "true")
    with pytest.raises(ValueError, match="PINNED_MANIFEST"):
        live.build_live_runtime(object(), disabled_manifest())


def test_enabled_model_failure_is_not_a_disabled_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONEY_QLIB_ENABLED", raising=False)

    def missing(*_args: Any, **_kwargs: Any) -> None:
        raise ValueError("QLIB_PROMOTION_REQUIRED")

    monkeypatch.setattr(live.ModelRegistry, "load_active", missing)
    with pytest.raises(ValueError, match="QLIB_PROMOTION_REQUIRED"):
        live.build_live_runtime(object(), reviewed_manifest())


def test_preflight_explicit_disabled_mode_does_not_clear_lean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONEY_QLIB_ENABLED", "false")
    monkeypatch.delenv("MONEY_LIVE_MANIFEST", raising=False)
    report = preflight.deployment_preflight("worker")
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["promoted_qlib_model"]["status"] == "NOT_REQUIRED"
    assert checks["lean"]["status"] == "NOT_VERIFIED"
    assert report["production_status"] == "PRODUCTION BLOCKED"


@pytest.mark.parametrize("value", ["yes", "0", "FALSE", 0])
def test_settings_requires_explicit_true_false(value: object) -> None:
    with pytest.raises(ValueError, match="MONEY_QLIB_ENABLED_REQUIRES_TRUE_OR_FALSE"):
        Settings.explicit_qlib_selection(value)


def test_disabled_objective_can_describe_verified_scenario_without_inventing_qlib_rank() -> None:
    from test_signal_generation import fixture as signal_fixture

    from money.research.objective import AsymmetryAnalyzer
    from money.schemas.contracts import AuditFinding, Candidate, DecisionPacket, ResearchState
    from money.signals.generation import generate_signal

    values = signal_fixture()
    snapshot = ResearchSnapshot.model_validate(
        values["snapshot"].model_dump() | {"qlib_enabled": False, "hash": ""}
    )
    reports = tuple(
        report.model_copy(update={"snapshot_hash": snapshot.hash})
        for report in values["reports"][:2]
    )
    first = reports[0]
    extra = first.claims[0].model_copy(update={"evidence_ids": ("bar-0", "bar-59")})
    reports = (first.model_copy(update={"claims": (extra,)}), reports[1])
    audit = values["audit"].model_copy(
        update={
            "findings": tuple(
                AuditFinding(
                    auditor="synthetic-test-verifier",
                    claim_id=claim.claim_id,
                    state="VERIFIED",
                    explanation="Synthetic evidence linkage only",
                    evidence_ids=claim.evidence_ids,
                )
                for report in reports
                for claim in report.claims
            )
        }
    )
    independence = evidence_independence(snapshot, reports)
    state, reasons = consensus(
        values["mandate"],
        snapshot,
        reports,
        values["lean"],
        audit,
        values["red_team"],
        independence,
        utc_now(),
    )
    assert state == ResearchState.RESEARCH_CANDIDATE, reasons
    values.update(snapshot=snapshot, reports=reports, audit=audit, state=state)
    design = generate_signal(**values)
    assert design.signal is not None, design.reasons
    packet = DecisionPacket(
        research_id=values["research_id"],
        mandate=values["mandate"],
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.hash,
        candidate=Candidate(ticker=snapshot.ticker, discovery=()),
        eligibility=snapshot.instrument,
        reports=reports,
        lean=values["lean"],
        audit=audit,
        red_team=values["red_team"],
        independence=independence,
        final_state=state,
        reasons=reasons,
        sources=snapshot.evidence,
        issued_at=utc_now(),
        signal=design.signal,
        runtime="live",
        frozen_snapshot=snapshot,
        qlib_enabled=False,
    )
    result = AsymmetryAnalyzer.assess(packet, design, utc_now())
    assert result is not None
    assert result.qlib_rank is None and result.qlib_universe_size is None
