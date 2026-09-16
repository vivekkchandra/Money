from datetime import timedelta

import pytest

from money.data.qualification import ProviderQualification
from money.flows.research import build_runtime
from money.research.live import merge_archived_market
from money.schemas.contracts import EvidenceRecord


def inputs():
    runtime = build_runtime("demo")
    base = runtime.snapshot_builder(runtime.eligibility.get_instrument_metadata("DEMO.L"))
    bar = base.evidence[0]
    qualification = ProviderQualification(
        provider=bar.provider,
        datasets=("ohlcv",),
        earliest_observation=base.created_at - timedelta(days=365),
        publication_times="ORIGINAL_PUBLICATION_VERIFIED",
        maximum_age_seconds=3600,
        production_qualified=True,
        qualified_by="test-reviewer",
        qualification_report_hash="a" * 64,
        verified_at=base.created_at - timedelta(days=1),
        valid_until=base.created_at + timedelta(days=1),
        attribution="Test",
        source_documentation="https://example.invalid/test",
    )
    return base, bar, {bar.provider: qualification}


def test_original_publication_is_retained_not_inferred_from_current_retrieval():
    base, bar, providers = inputs()
    recent = EvidenceRecord.model_validate(
        bar.model_dump() | {"publication_time": base.created_at, "hash": ""}
    )
    result = merge_archived_market([recent], (bar,), providers, "new-snapshot", base.instrument)
    assert len(result) == 1
    assert result[0].snapshot_id == "new-snapshot"
    assert result[0].publication_time == bar.publication_time < recent.publication_time
    assert result[0].retrieval_time == bar.retrieval_time


def test_current_archive_conflict_is_not_a_silent_provider_fallback():
    base, bar, providers = inputs()
    conflicting = EvidenceRecord.model_validate(
        bar.model_dump() | {"payload": bar.payload.model_dump() | {"volume": 1}, "hash": ""}
    )
    with pytest.raises(ValueError, match="PROVIDER_CONFLICT"):
        merge_archived_market([conflicting], (bar,), providers, "new", base.instrument)


def test_unqualified_historical_availability_cannot_be_backdated():
    base, bar, providers = inputs()
    providers[bar.provider] = providers[bar.provider].model_copy(
        update={"publication_times": "AS_RETRIEVED"}
    )
    with pytest.raises(ValueError, match="HISTORICAL_AVAILABILITY_UNKNOWN"):
        merge_archived_market([bar], (bar,), providers, "new", base.instrument)


def test_archive_duplicate_sessions_and_stale_review_fail_closed():
    base, bar, providers = inputs()
    with pytest.raises(ValueError, match="DUPLICATE"):
        merge_archived_market([], (bar, bar), providers, "new", base.instrument)
    stale = EvidenceRecord.model_validate(
        bar.model_dump() | {"fresh_until": base.created_at - timedelta(seconds=1), "hash": ""}
    )
    with pytest.raises(ValueError, match="PIT_OR_FRESHNESS"):
        merge_archived_market([], (stale,), providers, "new", base.instrument)
