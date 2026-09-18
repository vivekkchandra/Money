"""Synthetic scheduler state is an optimization, never real qualification evidence."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from money.data.security import ProviderFailure
from money.qualification.core import QualificationContext
from money.qualification.universe_policy import UNIVERSE_POLICY_VERSION
from money.qualification.universe_progress import (
    CURSOR,
    PROGRESS,
    build_enrichment_progress,
    enrichment_order,
    record_network_progress,
    write_enrichment_progress,
)
from money.qualification.universe_providers import BulkProviderEnricher


@pytest.fixture
def ctx(tmp_path: Path) -> QualificationContext:
    return QualificationContext(tmp_path / "bundle", tmp_path, {}, datetime.now(UTC))


def rows() -> list[dict[str, Any]]:
    return [
        {"trading212_id": name, "isin": name, "provider_enrichment_input": True}
        for name in ("A", "B", "C")
    ]


def test_rotation_and_membership_churn(ctx: QualificationContext) -> None:
    source = rows()
    record_network_progress(ctx, source[1], None)
    assert enrichment_order(ctx, source, None, replay=False) == [2, 0, 1]
    del source[1]
    assert enrichment_order(ctx, source, None, replay=False) == [1, 0]


@pytest.mark.parametrize(
    "mismatch", ["version", "universe_policy_version", "credential_binding_sha256"]
)
def test_mismatched_cursor_ignored(ctx: QualificationContext, mismatch: str) -> None:
    source = rows()
    record_network_progress(ctx, source[0], None)
    state = ctx.read_json(CURSOR)
    assert state["universe_policy_version"] == UNIVERSE_POLICY_VERSION
    state[mismatch] = "different"
    ctx.write_json(CURSOR, state)
    assert enrichment_order(ctx, source, None, replay=False) == [0, 1, 2]


def test_replay_preserves_live_cursor_and_never_excludes_candidates(
    ctx: QualificationContext,
) -> None:
    source = rows()
    record_network_progress(ctx, source[0], None)
    before = ctx.read_bytes(CURSOR)
    assert enrichment_order(ctx, source, None, replay=True) == [0, 1, 2]
    assert ctx.read_bytes(CURSOR) == before
    source[1]["provider_enrichment_input"] = False
    assert enrichment_order(ctx, source, None, replay=False) == [2, 0]


def test_corrupt_cursor_does_not_omit_stocks(ctx: QualificationContext) -> None:
    ctx.write_bytes(CURSOR, b"{")
    assert enrichment_order(ctx, rows(), None, replay=False) == [0, 1, 2]


def instrument(index: int) -> dict[str, Any]:
    stem = f"GB{index:09d}"
    digits = "".join(str(int(char, 36)) for char in stem) + "0"
    total = sum(
        sum(divmod(int(char) * (2 if offset % 2 else 1), 10))
        for offset, char in enumerate(reversed(digits))
    )
    return {
        "trading212_id": f"FIX{index:04d}l_EQ",
        "short_ticker": f"FIX{index:04d}",
        "name": f"Fixture {index} PLC",
        "isin": stem + str((-total) % 10),
        "quote_currency": "GBX",
        "instrument_type": "STOCK",
        "universe_member": True,
        "identity_valid": True,
        "provider_enrichment_input": True,
        "qualification_state": "UNRESOLVED_ISA_SCOPE",
    }


class IdentityFetcher:
    """Only the HTTP seam is synthetic; mapping, cache and scheduling are real."""

    def __init__(self, *, denied: bool = False, empty_index: int | None = None):
        self.calls: list[str] = []
        self.denied = denied
        self.empty_index = empty_index

    def json(self, url: str, *, headers: Any = None) -> Any:
        path = urlsplit(url).path
        assert path.startswith("/api/search/")
        isin = path.rsplit("/", 1)[-1]
        self.calls.append(isin)
        if self.denied:
            raise ProviderFailure("PROVIDER_UNAVAILABLE", http_status=402)
        index = int(isin[2:-1])
        if index == self.empty_index:
            return []
        row = instrument(index)
        return [
            {
                "Code": row["short_ticker"],
                "Exchange": "LSE",
                "ISIN": isin,
                "Name": row["name"],
                "Currency": "GBX",
                "Type": "Common Stock",
            }
        ]


def identity_pass(ctx, source, fetcher, budget):
    worker = BulkProviderEnricher(
        ctx,
        fetcher=fetcher,
        max_requests=budget,
        sleep=lambda _: None,
        clock=lambda: ctx.now,
    )
    output = [dict(row) for row in source]
    for index in enrichment_order(ctx, source, "test-binding", replay=False):
        row = output[index]
        row["eodhd_symbol"] = None
        row["eodhd_mapping_state"] = "UNRESOLVED"
        before = worker.requests_used
        try:
            worker._mapping(row)
        except (ProviderFailure, ValueError):
            pass  # A non-match/backoff remains unresolved; this is not a qualification pass.
        if worker.requests_used > before:
            record_network_progress(ctx, row, "test-binding")
    return output, worker


def test_repeated_bounded_runs_converge_for_1400_stable_gbx_identity_candidates(ctx):
    ctx.environ = {"EODHD_API_KEY": "synthetic-progress-test-secret"}
    source = [instrument(index) for index in range(1400)]
    fetcher = IdentityFetcher()
    for run in range(7):
        output, worker = identity_pass(ctx, source, fetcher, 200)
        assert worker.requests_used == 200
        assert sum(row["eodhd_mapping_state"] == "MAPPED" for row in output) == (run + 1) * 200
        assert all(row["qualification_state"] == "UNRESOLVED_ISA_SCOPE" for row in output)
    assert len(fetcher.calls) == len(set(fetcher.calls)) == 1400
    cursor_before = ctx.read_bytes(CURSOR)
    output, worker = identity_pass(ctx, source, fetcher, 200)
    assert worker.requests_used == 0
    assert all(row["eodhd_lookup_origin"] == "CACHE" for row in output)
    assert ctx.read_bytes(CURSOR) == cursor_before
    assert len(fetcher.calls) == 1400


def test_one_empty_identity_response_does_not_starve_remaining_candidates(ctx):
    ctx.environ = {"EODHD_API_KEY": "synthetic-progress-test-secret"}
    source = [instrument(index) for index in range(12)]
    fetcher = IdentityFetcher(empty_index=0)
    for _ in range(3):
        output, worker = identity_pass(ctx, source, fetcher, 4)
        assert worker.requests_used == 4
    assert output[0]["eodhd_mapping_state"] == "UNRESOLVED"
    assert output[0]["eodhd_mapping_diagnostics"]["unresolved_reason"] == "EMPTY_SEARCH_RESPONSE"
    assert all(row["eodhd_mapping_state"] == "MAPPED" for row in output[1:])
    assert len(fetcher.calls) == 12


def test_access_blocked_run_does_not_burn_1400_requests_or_claim_convergence(ctx):
    ctx.environ = {"EODHD_API_KEY": "synthetic-progress-test-secret"}
    source = [instrument(index) for index in range(1400)]
    fetcher = IdentityFetcher(denied=True)
    output, worker = identity_pass(ctx, source, fetcher, 300)
    assert worker.requests_used == len(fetcher.calls) == 1
    assert sum(row["eodhd_mapping_attempted"] for row in output) == 1
    assert all(row["eodhd_mapping_state"] == "UNRESOLVED" for row in output)
    resumed, worker = identity_pass(ctx, source, fetcher, 300)
    assert worker.requests_used == 0
    assert not any(row["eodhd_mapping_attempted"] for row in resumed)


def master(source):
    return {
        "status": "REPLAYED",
        "scope": "SAVED_RESPONSE_REPLAY_ONLY",
        "observed_at": "2026-09-17T18:15:48+00:00",
        "valid_until": "2026-09-18T18:15:48+00:00",
        "universe_policy_version": UNIVERSE_POLICY_VERSION,
        "stocks": source,
    }


def test_progress_explains_legacy_402_as_access_not_promised_convergence(ctx):
    source = [instrument(index) for index in range(5)]
    source[0].update(eodhd_mapping_attempted=True, eodhd_lookup_origin="NETWORK")
    source[0]["provider_request_diagnostics"] = [
        {
            "provider": "eodhd",
            "endpoint": "search",
            "outcome": "FAILED",
            "http_status": 402,
            "error_code": "PROVIDER_UNAVAILABLE",
        }
    ]
    source[1].update(eodhd_mapping_attempted=True, eodhd_lookup_origin="CACHE")
    source[1]["provider_reasons"] = ["EODHD_MAPPING_NOT_FOUND"]
    source[1]["eodhd_mapping_diagnostics"] = {
        "response_count": 2,
        "exact_isin_count": 2,
        "exact_quote_count": 0,
    }
    source[2]["provider_request_diagnostics"] = [
        {
            "provider": "eodhd",
            "endpoint": "search",
            "outcome": "NOT_REQUESTED",
            "error_code": "PROVIDER_REQUEST_BUDGET_EXHAUSTED",
            "http_status": None,
        }
    ]
    record_network_progress(ctx, source[0], "test-binding")
    old_cursor = ctx.read_bytes(CURSOR)
    report = write_enrichment_progress(ctx, master(source))
    assert ctx.read_bytes(CURSOR) == old_cursor
    assert ctx.read_json(PROGRESS) == report
    assert report["source_scope"] == "SAVED_RESPONSE_REPLAY_ONLY"
    assert report["source_observed_at"] == master(source)["observed_at"]
    assert report["counts"]["eodhd_attempted"] == 2
    assert report["counts"]["eodhd_unattempted"] == 3
    assert report["counts"]["deferred_by_request_budget"] == 1
    assert report["counts"]["qualified"] == 0
    assert report["mapping_failure_counts"] == {"GBX_LINE_NOT_RETURNED": 1}
    assert report["blocked_by_access"]
    assert report["estimated_additional_runs"] is None
    assert report["estimate_status"] == "PROVIDER_ACCESS_REVIEW_REQUIRED"
    assert report["minimum_requests_for_unattempted_identity_lookups"] == 3
    assert report["provider_endpoint_outcomes"][0]["error_code"] == "PROVIDER_PAYMENT_REQUIRED"
    assert "credential_binding_sha256" not in json.dumps(report)
    assert not report["production_qualified"]


def test_progress_failed_refresh_has_no_invented_zero_universe_counts():
    report = build_enrichment_progress({"status": "REFRESH_FAILED", "stocks": []})
    assert not report["source_counts_available"]
    assert report["counts"] is None
    assert report["minimum_requests_for_unattempted_identity_lookups"] is None
    assert report["estimated_additional_runs"] is None


def test_progress_non_access_failure_does_not_infer_subscription_and_bounds_examples():
    source = [instrument(index) for index in range(20)]
    for row in source:
        row["provider_request_diagnostics"] = [
            {
                "provider": "eodhd",
                "endpoint": "search",
                "outcome": "FAILED",
                "http_status": None,
                "error_code": "PROVIDER_UNAVAILABLE",
            }
        ]
    report = build_enrichment_progress(master(source))
    assert not report["blocked_by_access"]
    assert all(len(examples) == 3 for examples in report["failure_examples"].values())
    assert report["estimated_additional_runs"] is None


def test_progress_secret_guard_rejects_known_secret_before_write(ctx):
    ctx.environ = {"EODHD_API_KEY": "secret-progress-test-value"}
    source = [instrument(0)]
    source[0]["trading212_id"] = ctx.environ["EODHD_API_KEY"]
    source[0]["provider_reasons"] = ["EODHD_MAPPING_NOT_FOUND"]
    with pytest.raises(ValueError, match="QUALIFICATION_SECRET_DETECTED"):
        write_enrichment_progress(ctx, master(source))
    assert not (ctx.root / PROGRESS).exists()


def test_progress_current_counts_expire_without_rewriting_saved_observation():
    source = [instrument(0), instrument(1)]
    observed = datetime(2026, 9, 17, 18, 15, 48, tzinfo=UTC)
    source[0].update(
        eodhd_mapping_attempted=True,
        eodhd_mapping_state="MAPPED",
        provider_evidence_observed_at=observed.isoformat(),
        provider_evidence_valid_until=(observed + timedelta(days=1)).isoformat(),
        provider_network_requests_used=1,
    )
    view = master(source)
    view["provider_request_budget"] = 300
    fresh = build_enrichment_progress(view, now=observed + timedelta(minutes=1))
    assert fresh["source_evidence_current"]
    assert fresh["total_provider_inputs"] == 2
    assert fresh["serviced_current"] == fresh["mapped_current"] == 1
    assert fresh["remaining_unserviced"] == 1
    assert fresh["network_requests_this_run"] == 1
    assert fresh["minimum_additional_bounded_runs_for_unattempted_identity_lookups"] == 1
    expired = build_enrichment_progress(view, now=observed + timedelta(days=1))
    assert not expired["source_evidence_current"]
    assert expired["serviced_current"] == expired["mapped_current"] == 0
    assert expired["remaining_unserviced"] == 2
    assert expired["counts"]["eodhd_mapped"] == 1  # Historical saved-view count, not current.
    assert expired["source_observed_at"] == view["observed_at"]


def test_progress_provider_evidence_expires_before_newer_broker_observation():
    observed = datetime(2026, 9, 17, 18, 15, 48, tzinfo=UTC)
    source = [instrument(0)]
    source[0].update(
        eodhd_mapping_attempted=True,
        eodhd_mapping_state="MAPPED",
        provider_evidence_observed_at=(observed - timedelta(days=1)).isoformat(),
        provider_evidence_valid_until=observed.isoformat(),
    )
    report = build_enrichment_progress(master(source), now=observed + timedelta(minutes=1))
    assert report["source_evidence_current"]
    assert report["mapped_current"] == report["serviced_current"] == 0
    assert report["remaining_unserviced"] == 1


def test_progress_access_failure_vs_transient_retry_and_no_default_budget():
    source = [instrument(index) for index in range(2)]
    for row, status, code in zip(
        source, [402, 429], ["PROVIDER_UNAVAILABLE", "PROVIDER_RATE_LIMITED"], strict=True
    ):
        row["provider_request_diagnostics"] = [
            {
                "provider": "eodhd",
                "endpoint": "search",
                "outcome": "FAILED",
                "http_status": status,
                "error_code": code,
            }
        ]
    report = build_enrichment_progress(master(source))
    assert report["permanent_failures"] == report["retryable_failures"] == 1
    assert report["configured_request_budget"] is None
    assert report["estimated_additional_bounded_runs"] is None
    assert report["minimum_additional_bounded_runs_for_unattempted_identity_lookups"] is None
