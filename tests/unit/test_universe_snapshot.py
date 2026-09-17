"""Synthetic mechanics only; these fixtures are never production evidence."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from money.data.identifiers import InstrumentIdentifiers
from money.data.live_eligibility import EligibilityReview
from money.data.security import ProviderFailure
from money.qualification.core import QualificationContext
from money.qualification.snapshot import run_snapshot_stage
from money.scanner.universe import (
    QualifiedUniverseSnapshot,
    UniverseResearchContext,
    UniverseScreenPolicy,
    UniverseSnapshotBuilder,
    bind_universe_snapshot,
    freeze_qualified_universe,
    screen_qualified_universe,
)
from money.schemas.contracts import (
    EvidenceRecord,
    FinancialFact,
    InstrumentMetadata,
    PriceBar,
    ResearchSnapshot,
    content_hash,
)

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)


def review(ticker="AAA", *, currency="GBX", **updates):
    metadata = InstrumentMetadata(
        ticker=ticker,
        company="Synthetic test equity " + ticker,
        instrument_type="STOCK",
        quote_currency=currency,
        isa_available=True,
        currently_available=True,
        business_activities=("retail",),
        activities_verified=True,
        verified_at=NOW - timedelta(hours=1),
        source="Unit-test review only",
        provider="fixture-provider",
        source_id=ticker,
    )
    metadata = InstrumentMetadata.model_validate(metadata.model_dump() | updates)
    return EligibilityReview(
        metadata=metadata,
        identifiers=InstrumentIdentifiers(
            ticker=ticker,
            company_name=metadata.company,
            trading212_id=ticker + "_EQ",
            exchange_ticker=ticker,
            exchange="XLON",
            isin="GB00BH4HKS39",
            quote_currency=currency,
            provider_symbols=(("eodhd", ticker + ".LSE"),),
            verified_at=NOW - timedelta(hours=1),
            valid_until=NOW + timedelta(hours=2),
            source="Synthetic explicit test mapping",
        ),
        eligibility_proof_hash=content_hash({"test": ticker, "kind": "eligibility"}),
        ethical_proof_hash=content_hash({"test": ticker, "kind": "ethical"}),
    )


def snapshot(item, *, volume=100_000, count=20):
    rows = []
    value = Decimal(1) if item.metadata.quote_currency == "GBP" else Decimal(100)
    for index in range(count):
        observation = NOW - timedelta(days=count - index)
        rows.append(
            EvidenceRecord(
                evidence_id=f"{item.metadata.ticker}-{index}",
                snapshot_id=item.metadata.ticker,
                source="Synthetic PIT-safe unit test bars",
                provider="fixture-provider",
                source_id=str(index),
                canonical_source_id=str(index),
                observation_time=observation,
                publication_time=observation,
                retrieval_time=NOW - timedelta(seconds=1),
                fresh_until=NOW + timedelta(hours=1),
                pit_safe=True,
                payload=PriceBar(
                    open=value,
                    high=value,
                    low=value,
                    close=value,
                    volume=volume,
                    currency=item.metadata.quote_currency,
                ),
            )
        )
    return ResearchSnapshot(
        snapshot_id=item.metadata.ticker,
        ticker=item.metadata.ticker,
        created_at=NOW,
        price_cutoff=NOW,
        news_cutoff=NOW,
        filing_cutoff=NOW,
        fundamental_cutoff=NOW,
        instrument=item.metadata,
        evidence=tuple(rows),
    )


def test_complete_membership_frozen_before_bounded_screen_and_missing_retained():
    first, second, missing = review("AAA"), review("BBB"), review("CCC")
    data = snapshot(first), snapshot(second, volume=200_000)
    frozen = freeze_qualified_universe((first, second, missing), data, NOW)
    assert [member.instrument.ticker for member in frozen.members] == ["AAA", "BBB", "CCC"]
    assert frozen.members[-1].evidence_status == "RESEARCH_EVIDENCE_REQUIRED"
    screened = screen_qualified_universe(
        frozen, data, at=NOW, policy=UniverseScreenPolicy(maximum_candidates=1)
    )
    assert screened.selected_tickers == ("BBB",)
    assert len(screened.entries) == 3
    assert screened.entries[-1].state == "UNRESOLVED_DATA"
    assert screened.entries[0].evidence_ids == tuple(row.evidence_id for row in data[1].evidence)
    assert screened.methodology_hash == content_hash(screened.policy)


def test_input_order_does_not_change_freeze_or_numeric_screen():
    first, second = review("AAA"), review("BBB")
    one, two = snapshot(first), snapshot(second)
    frozen = freeze_qualified_universe((first, second), (one, two), NOW)
    reversed_frozen = freeze_qualified_universe((second, first), (two, one), NOW)
    assert frozen.hash == reversed_frozen.hash
    screen = screen_qualified_universe(frozen, (one, two), at=NOW)
    reverse = screen_qualified_universe(reversed_frozen, (two, one), at=NOW)
    assert screen.hash == reverse.hash
    assert screen.selected_tickers == ("AAA", "BBB")


def test_gbp_gbx_liquidity_values_are_normalized_before_ranking():
    pounds, pence = review("AAA", currency="GBP"), review("BBB", currency="GBX")
    data = snapshot(pounds), snapshot(pence)
    frozen = freeze_qualified_universe((pounds, pence), data, NOW)
    result = screen_qualified_universe(frozen, data, at=NOW)
    assert {entry.average_daily_value_gbp for entry in result.entries} == {Decimal(100_000)}


@pytest.mark.parametrize(
    "updates",
    [
        {"isa_available": None},
        {"currently_available": False},
        {"activities_verified": False},
        {"business_activities": ("oil_services",)},
        {"instrument_type": "ETF"},
        {"verified_at": NOW - timedelta(hours=24)},
    ],
)
def test_unresolved_excluded_and_expired_members_cannot_enter_frozen_set(updates):
    item = review(**updates)
    with pytest.raises(ValueError, match="UNIVERSE_MEMBER"):
        freeze_qualified_universe((item,), (), NOW)


def test_duplicate_ticker_or_broker_identity_cannot_silently_win():
    item = review()
    with pytest.raises(ValueError, match="DUPLICATE_IDENTITY"):
        freeze_qualified_universe((item, item), (), NOW)


def test_expired_screen_and_mutated_artifacts_fail_closed():
    item = review()
    data = snapshot(item)
    frozen = freeze_qualified_universe((item,), (data,), NOW)
    with pytest.raises(ValueError, match="SCREEN_EXPIRED"):
        screen_qualified_universe(frozen, (data,), at=frozen.valid_until)
    changed = frozen.model_dump() | {"source_universe_hash": content_hash("tamper")}
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        QualifiedUniverseSnapshot.model_validate(changed)


def test_bound_snapshot_hash_includes_entire_universe_and_preserves_legacy_hashes():
    first, second = review("AAA"), review("BBB")
    data = snapshot(first)
    assert "universe_hash" not in data.model_dump(mode="json")
    assert data.hash == content_hash(data.model_dump(mode="json", exclude={"hash"}))
    one = freeze_qualified_universe((first,), (data,), NOW)
    two = freeze_qualified_universe((first, second), (data,), NOW)
    bound_one, bound_two = bind_universe_snapshot(data, one), bind_universe_snapshot(data, two)
    assert bound_one.universe_hash == one.hash
    assert bound_one.hash != data.hash != bound_two.hash
    assert bound_one.hash != bound_two.hash
    assert bound_one.evidence == data.evidence
    with pytest.raises(ValueError, match="BINDING_MISMATCH"):
        bind_universe_snapshot(snapshot(second), one)


def test_nonmember_or_postfreeze_substitution_cannot_enter_screen():
    first, second = review("AAA"), review("BBB")
    data = snapshot(first)
    frozen = freeze_qualified_universe((first,), (data,), NOW)
    with pytest.raises(ValueError, match="MEMBERSHIP_MISMATCH"):
        screen_qualified_universe(frozen, (data, snapshot(second)), at=NOW)
    with pytest.raises(ValueError, match="EVIDENCE_NOT_FROZEN"):
        screen_qualified_universe(frozen, (snapshot(first, volume=1),), at=NOW)


def test_insufficient_bars_never_blocks_healthy_member():
    first, second = review("AAA"), review("BBB")
    data = snapshot(first, count=1), snapshot(second)
    frozen = freeze_qualified_universe((first, second), data, NOW)
    screened = screen_qualified_universe(frozen, data, at=NOW)
    assert screened.selected_tickers == ("BBB",)
    assert screened.entries[-1].ticker == "AAA"
    assert screened.entries[-1].state == "UNRESOLVED_DATA"


def test_runner_freezes_all_members_even_when_provider_snapshots_unavailable(tmp_path):
    ctx = QualificationContext(tmp_path / "bundle", tmp_path, {}, NOW)
    reviews = (review("AAA"), review("BBB"))
    result = run_snapshot_stage(ctx, {"qualified_universe": reviews, "manifest_fields": {}})
    assert result["complete"] is False
    assert result["qualified_count"] == 2
    frozen = QualifiedUniverseSnapshot.model_validate(
        ctx.read_json("outputs/universe-snapshot.json")
    )
    assert len(frozen.members) == 2
    assert len(ctx.read_json("outputs/universe-screen.json")["entries"]) == 2
    assert not (ctx.root / "outputs/snapshot.json").exists()
    ctx.verify_artifact(*result["universe_artifact"])


def test_runner_does_not_fall_back_to_previous_stocks_after_explicit_empty_refresh(tmp_path):
    ctx = QualificationContext(tmp_path / "bundle", tmp_path, {}, NOW)
    result = run_snapshot_stage(ctx, {"qualified_universe": [], "manifest_fields": {}})
    assert result["complete"] is False
    assert result["qualified_count"] == 0
    assert not (ctx.root / "outputs/snapshot.json").exists()


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        (ValueError("MARKET_QUALITY_FAILED:test-only"), "MARKET_QUALITY_FAILED"),
        (
            ProviderFailure("FictionalSecretDoNotPersist", retryable=True),
            "RESEARCH_PROVIDER_UNAVAILABLE",
        ),
    ],
)
def test_runner_isolates_failed_stock_and_binds_candidate_to_full_membership(
    tmp_path, monkeypatch, failure, expected_reason
):
    from test_live_data_scanners import qualification

    from money.qualification import snapshot as stage
    from money.research.live import VerifiedInstrument

    ctx = QualificationContext(tmp_path / "bundle", tmp_path, {}, NOW)
    reviews = (review("AAA"), review("BBB"))
    spread = EvidenceRecord(
        snapshot_id="spread-test",
        source="Synthetic reviewed spread",
        provider="eodhd",
        source_id="test-spread",
        canonical_source_id="test-spread",
        observation_time=NOW,
        publication_time=NOW,
        retrieval_time=NOW,
        fresh_until=NOW + timedelta(hours=1),
        pit_safe=True,
        payload=FinancialFact(metric="spread_bps", value=10, unit="bps", period_end=NOW),
    )
    instruments = tuple(
        VerifiedInstrument(
            **item.model_dump(),
            spread_bps=10,
            spread_evidence=spread,
            corporate_action_coverage_hash=content_hash("synthetic coverage"),
            corporate_actions_complete=True,
            cost_applicability={"sdrt": "UNKNOWN", "evidence_source": "test only"},
        )
        for item in reviews
    )

    def acquire(context, sources, selected):
        if selected.metadata.ticker == "AAA":
            raise failure
        return snapshot(reviews[1])

    monkeypatch.setattr(stage, "_instrument_snapshot", acquire)
    result = run_snapshot_stage(
        ctx,
        {
            "qualified_universe": reviews,
            "manifest_fields": {
                "instruments": instruments,
                "provider_qualifications": [qualification(NOW)],
            },
        },
    )
    assert result["complete"] is True
    assert result["qualified_count"] == 2
    assert result["unresolved_data"] == {"AAA": expected_reason}
    assert "FictionalSecretDoNotPersist" not in str(result)
    assert [item["ticker"] for item in result["candidate_snapshots"]] == ["BBB"]
    bound = ResearchSnapshot.model_validate(ctx.read_json("outputs/snapshot.json"))
    assert bound.universe_hash == result["qualified_universe_hash"]
    frozen = ctx.read_json("outputs/universe-snapshot.json")
    assert [member["instrument"]["ticker"] for member in frozen["members"]] == ["AAA", "BBB"]


def test_live_wrapper_acquires_whole_universe_before_requested_ticker_selection():
    first, second = review("AAA"), review("BBB")
    acquired, checks = [], []

    def build(instrument):
        acquired.append(instrument.ticker)
        return snapshot(
            first if instrument.ticker == "AAA" else second,
            volume=100_000 if instrument.ticker == "AAA" else 200_000,
        )

    builder = UniverseSnapshotBuilder(
        lambda: (first.metadata, second.metadata),
        lambda: (first, second),
        build,
        clock=lambda: NOW,
        policy=UniverseScreenPolicy(maximum_candidates=1),
        validate_sources=lambda: checks.append("current-provider-check"),
    )
    with pytest.raises(ValueError, match="ATTENTION_BOUND_NOT_SELECTED"):
        builder(first.metadata)
    assert acquired == ["AAA", "BBB"]
    bound = builder(second.metadata)
    assert acquired == ["AAA", "BBB"]  # Only independent source check allows cache reuse.
    assert len(checks) == 2
    assert len(builder.last_universe.members) == 2
    assert builder.last_screen.selected_tickers == ("BBB",)
    assert len(builder.last_snapshots) == 2
    context = builder.context_for(bound)
    assert context.universe.hash == bound.universe_hash
    assert UniverseResearchContext.model_validate_json(context.model_dump_json()) == context


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        (ValueError("FictionalSecretDoNotPersist"), "RESEARCH_EVIDENCE_NOT_VERIFIED"),
        (
            ProviderFailure("FictionalSecretDoNotPersist", retryable=True),
            "RESEARCH_PROVIDER_UNAVAILABLE",
        ),
    ],
)
def test_live_wrapper_failed_stock_is_recorded_without_secret_and_does_not_veto_others(
    failure, expected_reason
):
    first, second = review("AAA"), review("BBB")

    def build(instrument):
        if instrument.ticker == "AAA":
            raise failure
        return snapshot(second)

    builder = UniverseSnapshotBuilder(
        lambda: (first.metadata, second.metadata), lambda: (first, second), build, clock=lambda: NOW
    )
    result = builder(second.metadata)
    context = builder.context_for(result)
    assert len(context.universe.members) == 2
    assert context.universe.members[0].evidence_status == expected_reason
    assert "FictionalSecretDoNotPersist" not in context.model_dump_json()
    assert context.screen.selected_tickers == ("BBB",)


def test_live_wrapper_rechecks_current_membership_and_revocations_before_cache_reuse():
    first, second = review("AAA"), review("BBB")
    current = [first.metadata, second.metadata]
    builder = UniverseSnapshotBuilder(
        lambda: tuple(current),
        lambda: (first, second),
        lambda item: snapshot(first if item.ticker == "AAA" else second),
        clock=lambda: NOW,
        validate_sources=lambda: None,
    )
    result = builder(first.metadata)
    current.pop(0)
    with pytest.raises(ValueError, match="ATTENTION_BOUND_NOT_SELECTED"):
        builder(first.metadata)
    assert tuple(item.instrument.ticker for item in builder.last_universe.members) == ("BBB",)
    with pytest.raises(ValueError, match="CONTEXT_UNAVAILABLE"):
        builder.context_for(result)


def test_live_wrapper_cache_never_bypasses_provider_expiry():
    item = review()
    allow = [True]

    def validate():
        if not allow[0]:
            raise ValueError("PROVIDER_QUALIFICATION_STALE")

    builder = UniverseSnapshotBuilder(
        lambda: (item.metadata,),
        lambda: (item,),
        lambda _: snapshot(item),
        clock=lambda: NOW,
        validate_sources=validate,
    )
    builder(item.metadata)
    allow[0] = False
    with pytest.raises(ValueError, match="PROVIDER_QUALIFICATION_STALE"):
        builder(item.metadata)


def test_live_wrapper_never_caches_without_independent_current_source_check():
    item = review()
    calls = []

    def build(_):
        calls.append("acquire")
        return snapshot(item)

    builder = UniverseSnapshotBuilder(
        lambda: (item.metadata,),
        lambda: (item,),
        build,
        clock=lambda: NOW,
    )
    builder(item.metadata)
    builder(item.metadata)
    assert len(calls) == 2


def test_live_wrapper_expired_evidence_cannot_reenter_candidate_research():
    item = review()
    clock = [NOW]
    builder = UniverseSnapshotBuilder(
        lambda: (item.metadata,),
        lambda: (item,),
        lambda _: snapshot(item),
        clock=lambda: clock[0],
        validate_sources=lambda: None,
    )
    builder(item.metadata)
    clock[0] = NOW + timedelta(hours=1)
    with pytest.raises(ValueError, match="ATTENTION_BOUND_NOT_SELECTED"):
        builder(item.metadata)
    assert builder.last_universe.members[0].evidence_status == "RESEARCH_CURRENT_EVIDENCE_REQUIRED"
