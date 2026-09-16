from datetime import timedelta
from decimal import Decimal

import pytest

from money.flows.research import build_runtime
from money.scanner.discovery import DiscoveryPolicy, discover_snapshot, documentary_discovery
from money.schemas.contracts import EvidenceRecord, FinancialFact, ResearchMandate, ResearchSnapshot


def snapshot():
    runtime = build_runtime("demo")
    return runtime.snapshot_builder(runtime.eligibility.get_instrument_metadata("DEMO.L"))


def changed(base, records):
    return ResearchSnapshot.model_validate(base.model_dump() | {"evidence": records, "hash": ""})


def test_catalyst_is_recent_explicit_and_not_a_bullish_opinion():
    base = snapshot()
    result = documentary_discovery(base, DiscoveryPolicy())
    assert len(result) == 1 and result[0].channel == "catalyst"
    assert "results" in result[0].reason and "no sentiment" in result[0].reason
    news = base.evidence[-1]
    old = EvidenceRecord.model_validate(
        news.model_dump() | {"publication_time": base.created_at - timedelta(days=8), "hash": ""}
    )
    assert documentary_discovery(changed(base, (*base.evidence[:-1], old)), DiscoveryPolicy()) == ()


def test_single_financial_fact_is_not_a_change_signal():
    base = snapshot()
    assert not any(
        item.channel == "fundamental" for item in documentary_discovery(base, DiscoveryPolicy())
    )
    current = base.evidence[1]
    assert isinstance(current.payload, FinancialFact)
    prior = EvidenceRecord.model_validate(
        current.model_dump()
        | {
            "evidence_id": "prior",
            "source_id": "prior-filing",
            "canonical_source_id": "prior-filing",
            "payload": current.payload.model_dump()
            | {
                "value": Decimal("800000"),
                "period_end": current.payload.period_end - timedelta(days=365),
            },
            "hash": "",
        }
    )
    findings = documentary_discovery(changed(base, (*base.evidence, prior)), DiscoveryPolicy())
    assert any(
        item.channel == "fundamental"
        and "25.00%" in item.reason
        and set(item.evidence_ids) == {"prior", current.evidence_id}
        for item in findings
    )


def test_conflicted_or_same_period_facts_do_not_trigger_change():
    base = snapshot()
    current = base.evidence[1]
    prior = EvidenceRecord.model_validate(
        current.model_dump()
        | {
            "evidence_id": "restated",
            "payload": current.payload.model_dump() | {"value": Decimal("1")},
            "hash": "",
        }
    )
    findings = documentary_discovery(changed(base, (*base.evidence, prior)), DiscoveryPolicy())
    assert not any(item.channel == "fundamental" for item in findings)


def test_quant_discovery_cannot_borrow_another_snapshot():
    base = snapshot()
    runtime = build_runtime("demo")
    report = runtime.firms[-1].research(ResearchMandate(), base)
    with pytest.raises(ValueError, match="SNAPSHOT_MISMATCH"):
        discover_snapshot(base, quant=report.model_copy(update={"snapshot_id": "other"}))
