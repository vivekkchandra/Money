"""Synthetic frozen-universe recovery tests against real durable storage.

No fixture here is provider qualification or hosted production evidence.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from test_universe_snapshot import NOW, review, snapshot

from money.adapters.eligibility import Trading212EligibilityAdapter
from money.flows import research
from money.qualification.snapshot import QualificationSources
from money.research.live import LiveSnapshotBuilder, VerifiedInstrument
from money.scanner.universe import (
    UniverseResearchContext,
    UniverseScreenPolicy,
    UniverseSnapshotBuilder,
    bind_universe_snapshot,
)
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    Candidate,
    EvidenceRecord,
    QlibQuantResearchReport,
    ResearchMandate,
    ResearchSnapshot,
    TradingAgentsResearchReport,
    content_hash,
)
from money.storage import store as storage
from money.storage.store import ResearchStore, StoreError


class FirstPassReached(BaseException):
    """Simulate process interruption before any independent report is produced."""


@pytest.fixture
def durable_store(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'universe-recovery.db'}"
    monkeypatch.setenv("MONEY_ENV", "test")
    monkeypatch.setattr(storage, "now_utc", lambda: NOW)
    monkeypatch.setattr(research, "utc_now", lambda: NOW)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")
    store = ResearchStore(database_url, allow_sqlite=True)
    yield store
    store.engine.dispose()


def prepared(durable_store, monkeypatch, ticker="BBB"):
    first, second = review("AAA"), review("BBB")

    def build(instrument):
        base = snapshot(
            first if instrument.ticker == "AAA" else second,
            volume=100_000 if instrument.ticker == "AAA" else 200_000,
        )
        evidence = tuple(
            EvidenceRecord.model_validate(record.model_dump() | {"critical": True, "hash": ""})
            for record in base.evidence
        )
        return ResearchSnapshot.model_validate(
            base.model_dump() | {"evidence": evidence, "hash": ""}
        )

    builder = UniverseSnapshotBuilder(
        lambda: (first.metadata, second.metadata),
        lambda: (first, second),
        build,
        clock=lambda: NOW,
        policy=UniverseScreenPolicy(maximum_candidates=1),
    )
    runtime = replace(
        research.build_runtime("demo"),
        eligibility=Trading212EligibilityAdapter(
            (first.metadata, second.metadata), clock=lambda: NOW
        ),
        snapshot_builder=builder,
        universe_context=builder.context_for,
        discover=lambda mandate, facts: Candidate(ticker=facts.ticker, discovery=()),
    )
    monkeypatch.setattr(research, "utc_now", lambda: NOW)
    created = durable_store.create_job(ticker, ResearchMandate())
    # Keep the synthetic lease valid while a dedicated test advances both
    # clocks to exercise evidence expiry, rather than unrelated lease expiry.
    claim = durable_store.claim_job(
        "synthetic-universe-worker", lease_seconds=7200, job_timeout_seconds=7200
    )
    assert claim is not None and claim.job_id == created["id"]
    writer = durable_store.for_claim(claim)
    return created["id"], writer, runtime, builder


def stop_before_reports(monkeypatch):
    def stop(*args, **kwargs):
        raise FirstPassReached

    monkeypatch.setattr(research, "run_first_pass", stop)


def test_full_context_persists_before_candidate_snapshot_and_first_pass(durable_store, monkeypatch):
    job_id, writer, runtime, _ = prepared(durable_store, monkeypatch)
    save_snapshot = writer.save_snapshot

    def observe_snapshot(job, facts):
        artifacts = writer.get_checkpoint(job)["artifacts"]
        context = UniverseResearchContext.model_validate(artifacts["universe_context"])
        assert context.universe.hash == facts.universe_hash
        assert len(context.universe.members) == 2
        assert len(context.snapshots) == 2
        assert context.screen.selected_tickers == ("BBB",)
        save_snapshot(job, facts)

    monkeypatch.setattr(writer, "save_snapshot", observe_snapshot)
    stop_before_reports(monkeypatch)
    with pytest.raises(FirstPassReached):
        research.run_research(job_id, writer, runtime)
    reader = ResearchStore(durable_store.database_url, allow_sqlite=True)
    try:
        # A separate connection sees durable evidence before any report exists.
        assert writer.claim is not None
        checkpoint = reader.for_claim(writer.claim).get_checkpoint(job_id)
        assert checkpoint["sealed_firms"] == []
        assert (
            checkpoint["snapshot"]["universe_hash"]
            == checkpoint["artifacts"]["universe_context"]["universe"]["hash"]
        )
        assert reader.get_job(job_id)["locked_at"] is None
    finally:
        reader.engine.dispose()


def test_resume_uses_durable_full_context_without_reinvoking_live_builder(
    durable_store, monkeypatch
):
    job_id, writer, runtime, _ = prepared(durable_store, monkeypatch)
    stop_before_reports(monkeypatch)
    with pytest.raises(FirstPassReached):
        research.run_research(job_id, writer, runtime)

    def forbidden(*args):
        pytest.fail("resume must validate saved context, not reconstruct universe or evidence")

    resumed = replace(runtime, snapshot_builder=forbidden, universe_context=None)
    with pytest.raises(FirstPassReached):
        research.run_research(job_id, writer, resumed)
    assert writer.get_checkpoint(job_id)["snapshot"]["universe_hash"]


def test_resume_recovers_after_context_commit_before_candidate_snapshot(durable_store, monkeypatch):
    job_id, writer, runtime, _ = prepared(durable_store, monkeypatch)
    save_snapshot = writer.save_snapshot

    def interrupt_before_snapshot(*args):
        raise FirstPassReached

    monkeypatch.setattr(writer, "save_snapshot", interrupt_before_snapshot)
    with pytest.raises(FirstPassReached):
        research.run_research(job_id, writer, runtime)
    checkpoint = writer.get_checkpoint(job_id)
    assert checkpoint["snapshot"] is None
    assert "universe_context" in checkpoint["artifacts"]

    def forbidden(*args):
        pytest.fail("durable frozen context must survive without live reconstruction")

    monkeypatch.setattr(writer, "save_snapshot", save_snapshot)
    stop_before_reports(monkeypatch)
    with pytest.raises(FirstPassReached):
        research.run_research(
            job_id, writer, replace(runtime, snapshot_builder=forbidden, universe_context=None)
        )
    assert writer.get_checkpoint(job_id)["snapshot"]["universe_hash"]


def test_resume_never_renews_expired_frozen_universe(durable_store, monkeypatch):
    job_id, writer, runtime, _ = prepared(durable_store, monkeypatch)
    stop_before_reports(monkeypatch)
    with pytest.raises(FirstPassReached):
        research.run_research(job_id, writer, runtime)
    checkpoint = writer.get_checkpoint(job_id)
    original_hash = checkpoint["snapshot"]["hash"]
    monkeypatch.setattr(research, "utc_now", lambda: NOW + timedelta(hours=1))
    monkeypatch.setattr(storage, "now_utc", lambda: NOW + timedelta(hours=1))
    with pytest.raises(ValueError, match="(?i)universe|context"):
        research.run_research(job_id, writer, replace(runtime, universe_context=None))
    assert writer.get_checkpoint(job_id)["snapshot"]["hash"] == original_hash


@pytest.mark.parametrize(
    "corruption", ["missing", "universe-hash", "missing-evidence", "selected-tickers"]
)
def test_resume_rejects_missing_or_tampered_context_even_without_callback(
    durable_store, monkeypatch, corruption
):
    job_id, writer, runtime, _ = prepared(durable_store, monkeypatch)
    stop_before_reports(monkeypatch)
    with pytest.raises(FirstPassReached):
        research.run_research(job_id, writer, runtime)
    get_checkpoint = writer.get_checkpoint

    def corrupt(job):
        state = deepcopy(get_checkpoint(job))
        context = state["artifacts"]["universe_context"]
        if corruption == "missing":
            del state["artifacts"]["universe_context"]
        elif corruption == "universe-hash":
            context["universe"]["hash"] = content_hash("synthetic tamper")
        elif corruption == "missing-evidence":
            context["snapshots"] = context["snapshots"][:1]
        else:
            context["screen"]["selected_tickers"] = ["AAA"]
        return state

    monkeypatch.setattr(writer, "get_checkpoint", corrupt)
    with pytest.raises(ValueError, match="(?i)universe|screen|context|hash"):
        research.run_research(job_id, writer, replace(runtime, universe_context=None))


def test_bound_snapshot_cannot_start_without_full_context_callback(durable_store, monkeypatch):
    job_id, writer, runtime, builder = prepared(durable_store, monkeypatch)
    selected = builder(runtime.eligibility.get_instrument_metadata("BBB"))
    stop_before_reports(monkeypatch)
    with pytest.raises(ValueError, match="(?i)universe|context"):
        research.run_research(
            job_id,
            writer,
            replace(runtime, snapshot_builder=lambda _: selected, universe_context=None),
        )
    assert writer.get_checkpoint(job_id)["sealed_firms"] == []


def test_unscreened_member_cannot_bypass_attention_bound_with_valid_universe_hash(
    durable_store, monkeypatch
):
    job_id, writer, runtime, builder = prepared(durable_store, monkeypatch, ticker="AAA")
    selected = builder(runtime.eligibility.get_instrument_metadata("BBB"))
    context = builder.context_for(selected)
    original = next(item for item in context.snapshots if item.ticker == "AAA")
    bound_but_not_selected = bind_universe_snapshot(original, context.universe)
    stop_before_reports(monkeypatch)
    with pytest.raises(ValueError, match="UNIVERSE|CONTEXT|SELECTED"):
        research.run_research(
            job_id,
            writer,
            replace(
                runtime,
                snapshot_builder=lambda _: bound_but_not_selected,
                universe_context=lambda _: context,
            ),
        )
    assert writer.get_checkpoint(job_id)["sealed_firms"] == []


def test_live_snapshot_builder_missing_supplemental_identity_has_stable_failure(durable_store):
    source = QualificationSources(reviewed_instruments=(), provider_qualifications=())
    builder = LiveSnapshotBuilder(source, durable_store)
    with pytest.raises(ValueError, match="^RESEARCH_INSTRUMENT_EVIDENCE_REQUIRED$"):
        builder(review("AAA").metadata)


def test_full_eligibility_catalogue_with_partial_supplemental_data_preserves_all_members(
    durable_store,
):
    from test_filing_documents import reviewed_manifest

    first, second = review("AAA"), review("BBB")
    existing = reviewed_manifest().reviewed_instruments[0]
    reviewed = VerifiedInstrument.model_validate(
        existing.model_dump()
        | {
            "metadata": second.metadata,
            "identifiers": second.identifiers,
            "eligibility_proof_hash": second.eligibility_proof_hash,
            "ethical_proof_hash": second.ethical_proof_hash,
            "filing_documents": (),
        }
    )
    source = QualificationSources(reviewed_instruments=(reviewed,), provider_qualifications=())
    live = LiveSnapshotBuilder(source, durable_store)

    def acquire(instrument):
        # Exercise the real missing-supplemental branch; the other stock's
        # immutable evidence is a clearly synthetic offline test fixture.
        return live(instrument) if instrument.ticker == "AAA" else snapshot(second)

    wrapper = UniverseSnapshotBuilder(
        lambda: (first.metadata, second.metadata),
        lambda: (first, second),
        acquire,
        clock=lambda: NOW,
    )
    bound = wrapper(second.metadata)
    context = wrapper.context_for(bound)
    assert len(context.universe.members) == 2
    assert context.universe.members[0].evidence_status == "RESEARCH_EVIDENCE_NOT_VERIFIED"
    assert context.screen.selected_tickers == ("BBB",)
    assert tuple(item.ticker for item in context.snapshots) == ("BBB",)


@pytest.mark.parametrize("failure", ["missing-context", "unselected-member"])
def test_direct_snapshot_storage_rejects_missing_or_unselected_universe_context(
    durable_store, monkeypatch, failure
):
    job_id, writer, runtime, builder = prepared(
        durable_store, monkeypatch, ticker="AAA" if failure == "unselected-member" else "BBB"
    )
    selected = builder(runtime.eligibility.get_instrument_metadata("BBB"))
    context = builder.context_for(selected)
    writer.update_stage(job_id, "SNAPSHOT_BUILD")
    if failure == "unselected-member":
        writer.save_artifact(job_id, "universe_context", context)
        candidate = bind_universe_snapshot(
            next(item for item in context.snapshots if item.ticker == "AAA"), context.universe
        )
    else:
        candidate = selected
    with pytest.raises(ValueError, match="(?i)universe|context"):
        writer.save_snapshot(job_id, candidate)
    assert writer.get_checkpoint(job_id)["snapshot"] is None


@pytest.mark.parametrize(
    "stage", ["ELIGIBILITY_CHECK", "DISCOVERY", "FIRST_PASS_RESEARCH", "FIRST_PASS_LOCKED"]
)
def test_universe_context_storage_requires_snapshot_stage_before_first_pass_lock(
    durable_store, monkeypatch, stage
):
    job_id, writer, runtime, builder = prepared(durable_store, monkeypatch)
    selected = builder(runtime.eligibility.get_instrument_metadata("BBB"))
    context = builder.context_for(selected)
    if stage in {"ELIGIBILITY_CHECK", "DISCOVERY"}:
        writer.update_stage(job_id, stage)
    else:
        writer.update_stage(job_id, "SNAPSHOT_BUILD")
        writer.save_artifact(job_id, "universe_context", context)
        writer.save_snapshot(job_id, selected)
        writer.update_stage(job_id, "FIRST_PASS_RESEARCH")
        if stage == "FIRST_PASS_LOCKED":
            synthetic = research.DemoFirm("tradingagents").research(ResearchMandate(), selected)
            for schema, firm in (
                (TradingAgentsResearchReport, "tradingagents"),
                (AIHedgeFundResearchReport, "ai_hedge_fund"),
                (QlibQuantResearchReport, "qlib"),
            ):
                writer.save_report(
                    job_id, firm, schema.model_validate(synthetic.model_dump() | {"firm": firm})
                )
            writer.lock_first_pass(job_id)
    with pytest.raises(StoreError, match="Universe context must be frozen before first-pass"):
        writer.save_artifact(job_id, "universe_context", context)
