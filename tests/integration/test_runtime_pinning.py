from dataclasses import replace
from datetime import timedelta

import pytest

from money.data.security import ProviderFailure
from money.flows.research import build_runtime, run_research
from money.research.live import DiscoveryQuantFirm
from money.schemas.contracts import (
    Candidate,
    Contract,
    DiscoveryReason,
    ResearchMandate,
    content_hash,
    utc_now,
)
from money.storage import models as db


class Proof(Contract):
    provider: str = "fixture-provider"
    model: str = "fixture-model-v1"
    policy: str = "fixture-policy-v1"


def retry(store, owned, job_id):
    assert owned.retry_job(job_id, "PROVIDER_TIMEOUT")
    with store.engine.begin() as connection:
        connection.execute(
            db.jobs.update()
            .where(db.jobs.c.id == job_id)
            .values(available_at=utc_now() - timedelta(seconds=1))
        )
    claim = store.claim_job("retry-worker")
    return store.for_claim(claim)


@pytest.mark.parametrize("changed", ["provider", "model", "policy", "missing"])
def test_manifest_is_pinned_before_provider_calls_and_retry_rejects_drift(store, changed):
    job = store.create_job("DEMO.L", ResearchMandate())
    claim = store.claim_job("first-worker")
    owned = store.for_claim(claim)
    original = Proof()
    calls = []

    class InterruptedProvider:
        def get_instrument_metadata(self, ticker):
            calls.append(ticker)
            # The provenance must be durable before the first provider is called.
            assert (
                owned.get_checkpoint(job["id"])["artifacts"]["source_manifest"]
                == original.model_dump()
            )
            raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True)

    runtime = replace(build_runtime("demo"), provenance=original, eligibility=InterruptedProvider())
    with pytest.raises(ProviderFailure):
        run_research(job["id"], owned, runtime)
    assert len(calls) == 1
    recovered = retry(store, owned, job["id"])
    new_proof = None if changed == "missing" else original.model_copy(update={changed: "changed"})
    with pytest.raises(ValueError, match="RESEARCH_CONFIGURATION_CHANGED"):
        run_research(job["id"], recovered, replace(runtime, provenance=new_proof))
    assert len(calls) == 1
    checkpoint = recovered.get_checkpoint(job["id"])
    assert checkpoint["artifacts"]["source_manifest"] == original.model_dump()
    assert checkpoint["snapshot"] is None
    assert checkpoint["sealed_firms"] == []


def test_same_pinned_configuration_resumes_and_packet_references_original_proof(store):
    job = store.create_job("DEMO.L", ResearchMandate())
    owned = store.for_claim(store.claim_job("first-worker"))
    proof = Proof()
    owned.save_artifact(job["id"], "source_manifest", proof)
    recovered = retry(store, owned, job["id"])
    run_research(job["id"], recovered, replace(build_runtime("demo"), provenance=Proof()))
    result = store.get_job(job["id"])
    assert result["status"] == "COMPLETE"
    assert result["packet"]["source_manifest_hash"] == content_hash(proof)


def test_quant_discovery_cache_never_enters_qualitative_first_pass_capabilities(store):
    marker = "PRIVATE_QUANT_CONCLUSION_NOT_FOR_QUALITATIVE_FIRMS"
    runtime = build_runtime("demo")
    quant_delegate = next(firm for firm in runtime.firms if firm.firm == "qlib")
    quant_calls = []
    qualitative_calls = []

    class QuantAdapter:
        def research(self, mandate, snapshot):
            quant_calls.append(snapshot.hash)
            assert marker not in snapshot.model_dump_json()
            return quant_delegate.research(mandate, snapshot).model_copy(
                update={"conclusion": marker}
            )

    quant = DiscoveryQuantFirm(QuantAdapter())

    class QualitativeFirm:
        def __init__(self, delegate):
            self.firm, self.delegate = delegate.firm, delegate

        def research(self, mandate, snapshot):
            qualitative_calls.append(self.firm)
            assert marker not in mandate.model_dump_json() + snapshot.model_dump_json()
            for capability in ("reports", "consensus", "cio", "discovery", "_report"):
                assert not hasattr(snapshot, capability)
                assert not hasattr(mandate, capability)
            return self.delegate.research(mandate, snapshot)

    def discover(mandate, snapshot):
        result = quant.research(mandate, snapshot)
        return Candidate(
            ticker=snapshot.ticker,
            discovery=(
                DiscoveryReason(
                    channel="quantitative",
                    reason=result.conclusion,
                    evidence_ids=result.claims[0].evidence_ids,
                ),
            ),
        )

    runtime = replace(
        runtime,
        discover=discover,
        firms=tuple(
            quant if firm.firm == "qlib" else QualitativeFirm(firm) for firm in runtime.firms
        ),
    )
    job = store.create_job("DEMO.L", ResearchMandate())
    owned = store.for_claim(store.claim_job("isolation-worker"))
    run_research(job["id"], owned, runtime)
    result = store.get_job(job["id"])
    assert result["status"] == "COMPLETE"
    assert len(quant_calls) == 1  # Discovery and sealed Qlib report reuse the identical result.
    assert set(qualitative_calls) == {"tradingagents", "ai_hedge_fund"}
    assert result["packet"]["candidate"]["discovery"][0]["reason"] == marker
    assert store.get_reports(job["id"])["qlib"]["conclusion"] == marker
    assert marker not in str(store.get_evidence(job["id"]))


def test_quant_cache_key_includes_entire_mandate_and_snapshot_hash():
    runtime = build_runtime("demo")
    instrument = runtime.eligibility.get_instrument_metadata("DEMO.L")
    snapshot = runtime.snapshot_builder(instrument)
    delegate = next(firm for firm in runtime.firms if firm.firm == "qlib")
    calls = []

    class QuantAdapter:
        def research(self, mandate, snapshot):
            calls.append((content_hash(mandate), snapshot.hash))
            return delegate.research(mandate, snapshot)

    quant = DiscoveryQuantFirm(QuantAdapter())
    first = quant.research(ResearchMandate(), snapshot)
    assert quant.research(ResearchMandate(), snapshot) is first
    mandate = ResearchMandate(maximum_capital_gbp=199)
    second = quant.research(mandate, snapshot)
    assert second is not first
    new_snapshot = runtime.snapshot_builder(instrument)
    third = quant.research(mandate, new_snapshot)
    assert third.snapshot_hash == new_snapshot.hash != first.snapshot_hash
    assert len(calls) == 3
