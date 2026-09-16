"""Scripted inference tests; native lifecycle coverage is not paid qualification."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from money.adapters.native import NativeRunSettings
from money.adapters.native_process import BoundedNativeRunner, NativeProcessPolicy
from money.adapters.upstream import InvalidUpstreamReport
from money.crews.cross_examination import (
    Challenge,
    ChallengeInvocation,
    ChallengeResponse,
    ChallengeVerification,
    CrossExaminationPacket,
    run_cross_examination,
)
from money.crews.native_cross_examination import (
    CrewAIChallengeVerifier,
    DeterministicChallengeResponder,
    NativeFirmChallengeRunner,
    _parse_response,
)
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    AuditFinding,
    CIOAuditReport,
    Claim,
    DocumentFact,
    EvidenceRecord,
    FirmReport,
    InstrumentMetadata,
    LeanValidationReport,
    PriceBar,
    QlibQuantResearchReport,
    ResearchSnapshot,
    TradingAgentsResearchReport,
    Usage,
    content_hash,
)

NOW = datetime.now(UTC)


class ScriptedInference:
    provider = "scripted-test"
    model = "scripted-test"

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        return self.replies.pop(0)

    def usage(self) -> Usage:
        return Usage(input_tokens=len(self.prompts) * 10, output_tokens=len(self.prompts) * 5)


@pytest.fixture
def snapshot() -> ResearchSnapshot:
    records = []
    for index in range(25):
        stamp = NOW - timedelta(days=30 - index)
        records.append(EvidenceRecord(snapshot_id="correspondence-test", evidence_id=f"bar-{index}",
            source="synthetic", provider="synthetic", source_id=f"bar-{index}", canonical_source_id=f"bar-{index}",
            observation_time=stamp, publication_time=stamp, retrieval_time=NOW,
            fresh_until=NOW + timedelta(days=1), pit_safe=True,
            payload=PriceBar(open=100, close=105, low=95, high=110, volume=2000 if index == 24 else 1000, currency="GBX")))
    records.append(EvidenceRecord(snapshot_id="correspondence-test", evidence_id="announcement",
        source="synthetic", provider="synthetic", source_id="announcement", canonical_source_id="announcement",
        observation_time=NOW, publication_time=NOW, retrieval_time=NOW, fresh_until=NOW + timedelta(days=1),
        pit_safe=True, payload=DocumentFact(kind="announcement", title="Product announcement",
            excerpt="The company announced a new product.", url="https://example.com/announcement")))
    instrument = InstrumentMetadata(ticker="TEST.L", company="Synthetic Company", instrument_type="STOCK",
        quote_currency="GBX", verified_at=NOW, source="synthetic", provider="synthetic", source_id="TEST.L",
        isa_available=True, currently_available=True, activities_verified=True)
    return ResearchSnapshot(snapshot_id="correspondence-test", ticker="TEST.L", instrument=instrument,
        created_at=NOW, price_cutoff=NOW, news_cutoff=NOW, filing_cutoff=NOW, fundamental_cutoff=NOW,
        evidence=tuple(records))


def reports(snapshot: ResearchSnapshot, *, statement: str = "Relative volume is 2.0.",
            classification: str = "INFERENCE", evidence: str = "bar-24") -> tuple[FirmReport, ...]:
    return tuple(schema(snapshot_id=snapshot.snapshot_id, snapshot_hash=snapshot.hash,
        conclusion=f"PRIVATE-{firm}", model_version="test", prompt_version="test",
        upstream_sha="a" * 40, model_family="test", created_at=NOW,
        claims=(Claim(claim_id=f"{firm}:fact", family="technical", statement=statement,
                      classification=classification, evidence_ids=(evidence,)),))
        for firm, schema in (("tradingagents", TradingAgentsResearchReport),
                             ("ai_hedge_fund", AIHedgeFundResearchReport), ("qlib", QlibQuantResearchReport)))


def challenge(report: FirmReport) -> Challenge:
    return Challenge(challenge_id="challenge-1", respondent=report.firm, claim_id=report.claims[0].claim_id,
        original_report_hash=content_hash(report), original_claim=report.claims[0],
        question="Verify the original claim with independent evidence.",
        evidence_ids=tuple(f"bar-{i}" for i in range(4, 25))
        if report.claims[0].evidence_ids == ("bar-24",) else report.claims[0].evidence_ids)


def response(snapshot: ResearchSnapshot, item: Challenge) -> ChallengeResponse:
    return ChallengeResponse(challenge_id=item.challenge_id, respondent=item.respondent,
        snapshot_hash=snapshot.hash, original_report_hash=item.original_report_hash,
        position="MAINTAIN", explanation="Evidence supports the original claim.", evidence_ids=item.evidence_ids)


def lean(snapshot: ResearchSnapshot) -> LeanValidationReport:
    return LeanValidationReport(snapshot_id=snapshot.snapshot_id, state="INSUFFICIENT_EVIDENCE", runner_version="test")


def response_json(evidence: str = "bar-24") -> str:
    return json.dumps({"position": "MAINTAIN", "explanation": "The unchanged claim follows from the cited data.",
                       "evidence_ids": [evidence]})


def test_tradingagents_role_contract_has_only_own_report_and_two_calls(snapshot: ResearchSnapshot,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    # Native import substituted only for a contract test; demo trace is mandatory.
    import money.crews.native_cross_examination as correspondence

    original = reports(snapshot)
    item = challenge(original[0])
    inference = ScriptedInference(["Native bear reconsideration", response_json()])

    def role_factory(chat):
        def role(state):
            assert "PRIVATE-ai_hedge_fund" not in json.dumps(state)
            assert "PRIVATE-qlib" not in json.dumps(state)
            note = chat.invoke("Reconsider the own-firm claim.")
            return {"investment_debate_state": {"current_response": note.content}}
        return role

    monkeypatch.setattr(correspondence, "import_module",
                        lambda name: SimpleNamespace(create_bear_researcher=role_factory))
    monkeypatch.setattr(correspondence, "_native_chat", lambda session, context: SimpleNamespace(
        invoke=lambda value: SimpleNamespace(content=session.complete("policy", context + value))))
    before = tuple(report.model_dump_json() for report in original)
    result = NativeFirmChallengeRunner("tradingagents", inference,
        NativeRunSettings(verify_source_pin=False))(snapshot, original[0], item, 1)
    assert result.position == "MAINTAIN" and result.invocation is not None
    assert result.invocation.calls == 2 and result.invocation.runtime == "demo"
    assert result.invocation.usage.input_tokens == 20
    assert "PRIVATE-tradingagents" in inference.prompts[0][1]
    assert "PRIVATE-ai_hedge_fund" not in str(inference.prompts)
    assert tuple(report.model_dump_json() for report in original) == before


def test_real_aihf_response_lifecycle_is_snapshot_scoped(snapshot: ResearchSnapshot,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path(__file__).resolve().parents[2] / "upstreams/ai-hedge-fund"
    if not (source / "hedge_fund/signals/llm_agent.py").exists():
        pytest.skip("SKIPPED_EXTERNAL_UNAVAILABLE: pinned AI-HF checkout is absent")
    monkeypatch.syspath_prepend(str(source))
    monkeypatch.setattr("sys.dont_write_bytecode", True)
    own = reports(snapshot)[1]
    inference = ScriptedInference([response_json()])
    result = NativeFirmChallengeRunner("ai_hedge_fund", inference,
        NativeRunSettings(verify_source_pin=False))(snapshot, own, challenge(own), 1)
    assert result.position == "MAINTAIN" and result.invocation is not None
    assert result.invocation.calls == 1 and result.invocation.runtime == "demo"
    assert "PRIVATE-ai_hedge_fund" in str(inference.prompts)
    assert "PRIVATE-tradingagents" not in str(inference.prompts)


@pytest.mark.parametrize("bad", [
    '{"position":"MAINTAIN","explanation":"Claim","evidence_ids":[]}',
    '{"position":"MAINTAIN","explanation":"Claim","evidence_ids":["peer-report"]}',
    '{"position":"MAINTAIN","explanation":"BUY NOW","evidence_ids":["bar-24"]}',
    '{"position":"MAINTAIN","explanation":"90% confidence","evidence_ids":["bar-24"]}',
    '{"position":"MAINTAIN","explanation":"Claim","evidence_ids":["bar-24"],"tool_calls":[]}',
])
def test_native_response_schema_and_evidence_fail_closed(snapshot: ResearchSnapshot, bad: str) -> None:
    with pytest.raises(InvalidUpstreamReport):
        _parse_response(bad, snapshot, ("bar-24",))


def test_valid_snapshot_evidence_outside_challenge_permission_is_not_usable(snapshot: ResearchSnapshot) -> None:
    with pytest.raises(InvalidUpstreamReport, match="supporting evidence"):
        _parse_response(response_json("announcement"), snapshot, ("bar-24",))
    with pytest.raises(InvalidUpstreamReport):
        _parse_response(response_json(), snapshot, ())
    original = reports(snapshot)
    item = challenge(original[0]).model_copy(update={"evidence_ids": ("bar-24",)})
    checked = CrewAIChallengeVerifier(ScriptedInference([]), None, None, original, lean(snapshot))(
        snapshot, item, response(snapshot, item))
    assert checked.finding.state == "UNSUPPORTED"
    assert "outside this challenge's permitted set" in checked.finding.explanation


def test_coordinator_rejects_otherwise_valid_out_of_permission_citation(snapshot: ResearchSnapshot) -> None:
    original = reports(snapshot)
    audit = CIOAuditReport(snapshot_id=snapshot.snapshot_id, completed=True, active_specialists=("Technical Auditor",),
        findings=(AuditFinding(auditor="Technical Auditor", claim_id=original[0].claims[0].claim_id,
            state="UNSUPPORTED", explanation="Check this cited fact.", evidence_ids=("bar-24",)),))

    def reply(facts, own, item, number):
        return response(facts, item).model_copy(update={"evidence_ids": ("announcement",)})

    with pytest.raises(InvalidUpstreamReport, match="provenance"):
        run_cross_examination(snapshot, original, lean(snapshot), audit, first_pass_locked=True,
                              responders={"tradingagents": reply})


def test_respondent_cannot_receive_peer_report_or_revised_claim(snapshot: ResearchSnapshot) -> None:
    original = reports(snapshot)
    runner = NativeFirmChallengeRunner("tradingagents", ScriptedInference([]))
    with pytest.raises(InvalidUpstreamReport, match="own sealed report"):
        runner(snapshot, original[1], challenge(original[0]), 1)
    item = challenge(original[0]).model_copy(update={"original_claim": original[1].claims[0]})
    with pytest.raises(InvalidUpstreamReport, match="original claim"):
        runner(snapshot, original[0], item, 1)
    with pytest.raises(InvalidUpstreamReport, match="round"):
        runner(snapshot, original[0], challenge(original[0]), 3)


def test_deterministic_contradiction_cannot_be_cleared_by_model_enthusiasm(snapshot: ResearchSnapshot) -> None:
    original = reports(snapshot, statement="Relative volume is 9.0.")
    item = challenge(original[0])
    inference = ScriptedInference([])
    result = CrewAIChallengeVerifier(inference, None, None, original, lean(snapshot))(
        snapshot, item, response(snapshot, item))
    assert result.finding.state == "CONTRADICTED" and inference.prompts == []
    assert result.invocation is not None and result.invocation.calls == 0
    assert result.invocation.runtime == "deterministic" and result.invocation.usage.input_tokens == 0


def test_complete_numeric_assertion_is_independently_recomputed(snapshot: ResearchSnapshot) -> None:
    original = reports(snapshot)
    item = challenge(original[0])
    result = CrewAIChallengeVerifier(ScriptedInference([]), None, None, original, lean(snapshot))(
        snapshot, item, response(snapshot, item))
    assert result.finding.state == "VERIFIED"
    assert set(result.finding.evidence_ids) == {f"bar-{i}" for i in range(4, 25)}
    assert result.invocation is not None and result.invocation.calls == 0


def test_lean_missing_controls_remain_explicitly_unresolved(snapshot: ResearchSnapshot) -> None:
    validation = lean(snapshot)
    item = Challenge(challenge_id="lean-challenge", respondent="lean", claim_id=None,
        original_report_hash=content_hash(validation), question="Where are OOS controls?", evidence_ids=())
    result = DeterministicChallengeResponder(None, validation)(snapshot, None, item, 1)
    assert result.position == "INSUFFICIENT_EVIDENCE"
    assert "cannot supply absent" in result.explanation
    assert result.invocation is not None and result.invocation.calls == 0


def test_unqualified_quant_model_cannot_be_resolved(snapshot: ResearchSnapshot) -> None:
    own = reports(snapshot)[2]
    result = DeterministicChallengeResponder(None, lean(snapshot))(snapshot, own, challenge(own), 1)
    assert result.position == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize("statement,quotation,expected", [
    ("The company announced a new product.", "The company announced a new product.", "VERIFIED"),
    ("The new product will double revenue.", "The company announced a new product.", "UNSUPPORTED"),
    ("The company announced a new product.", "Fabricated source quotation.", "ERROR"),
    ("The company announced a new product.", "<br/><br/>", "ERROR"),
])
def test_real_crewai_independent_source_checks(snapshot: ResearchSnapshot, statement: str,
                                              quotation: str, expected: str) -> None:
    original = reports(snapshot, statement=statement, evidence="announcement")
    item = challenge(original[0])
    inference = ScriptedInference([json.dumps({"state": "VERIFIED", "explanation": "Compared original source fact.",
        "evidence_ids": ["announcement"], "source_checks": [{"evidence_id": "announcement", "quoted_fact": quotation}]})])
    verifier = BoundedNativeRunner(
        CrewAIChallengeVerifier(inference, NativeRunSettings(verify_source_pin=False), None, original, lean(snapshot)),
        ChallengeVerification, NativeProcessPolicy(timeout_seconds=30))
    if expected == "ERROR":
        with pytest.raises(InvalidUpstreamReport):
            verifier(snapshot, item, response(snapshot, item))
    else:
        result = verifier(snapshot, item, response(snapshot, item))
        assert result.finding.state == expected and result.invocation is not None
        assert result.invocation.runtime == "demo" and result.invocation.calls == 1
        assert result.evidence_checks[0].record_hash == snapshot.evidence[-1].hash


def test_verifier_transport_runs_in_separate_snapshot_only_process(snapshot: ResearchSnapshot) -> None:
    original = reports(snapshot)
    item = challenge(original[0])
    wrapped = BoundedNativeRunner(
        CrewAIChallengeVerifier(ScriptedInference([]), None, None, original, lean(snapshot)),
        ChallengeVerification, NativeProcessPolicy(timeout_seconds=30))
    result = wrapped(snapshot, item, response(snapshot, item))
    assert result.finding.state == "VERIFIED" and result.invocation.calls == 0


def test_coordinator_preserves_traces_and_legacy_absent_fields(snapshot: ResearchSnapshot) -> None:
    original = reports(snapshot)
    audit = CIOAuditReport(snapshot_id=snapshot.snapshot_id, completed=True, active_specialists=("Technical Auditor",),
        findings=(AuditFinding(auditor="Technical Auditor", claim_id=original[0].claims[0].claim_id,
            state="UNSUPPORTED", explanation="Recompute the numeric fact.", evidence_ids=tuple(f"bar-{i}" for i in range(4, 25))),))
    original_json = tuple(report.model_dump_json() for report in original)

    def reply(facts, own, item, number):
        return response(facts, item)

    packet = run_cross_examination(snapshot, original, lean(snapshot), audit, first_pass_locked=True,
        responders={"tradingagents": reply},
        verifier=CrewAIChallengeVerifier(ScriptedInference([]), None, None, original, lean(snapshot)))
    assert not packet.material_disagreement and len(packet.rounds) == 1
    assert packet.rounds[0].exchanges[0].verification_invocation.calls == 0
    assert tuple(report.model_dump_json() for report in original) == original_json
    assert CrossExaminationPacket.model_validate_json(packet.model_dump_json()) == packet
    # Old artifacts did not have original_claim/invocation; absent additions stay absent.
    old = challenge(original[0]).model_dump(exclude={"original_claim"})
    assert Challenge.model_validate(old).model_dump() == old


def test_coordinator_rejects_demo_native_response(snapshot: ResearchSnapshot) -> None:
    original = reports(snapshot)
    audit = CIOAuditReport(snapshot_id=snapshot.snapshot_id, completed=True, active_specialists=("Technical Auditor",),
        findings=(AuditFinding(auditor="Technical Auditor", claim_id=original[0].claims[0].claim_id,
            state="UNSUPPORTED", explanation="Recompute the numeric fact.", evidence_ids=tuple(f"bar-{i}" for i in range(4, 25))),))

    def reply(facts, own, item, number):
        return response(facts, item).model_copy(update={"invocation": ChallengeInvocation(
            component="tradingagents", provider="scripted", model="test", prompt_version="test",
            upstream_sha="a" * 40, calls=1, runtime="demo")})

    with pytest.raises(InvalidUpstreamReport, match="unqualified native"):
        run_cross_examination(snapshot, original, lean(snapshot), audit, first_pass_locked=True,
            responders={"tradingagents": reply})
