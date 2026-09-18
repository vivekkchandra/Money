"""Synthetic temporary-file tests only; no fixture is production evidence."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from money.data.identifiers import InstrumentIdentifiers
from money.data.provider_probes import DatasetProbe
from money.data.qualification import ProviderQualification
from money.qualification import universe
from money.qualification.core import QualificationContext
from money.schemas.contracts import EXCLUDED_ACTIVITIES

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)


def stamp(**changes: Any) -> dict[str, Any]:
    return {
        "status": "REVIEWED",
        "prepared_by": "synthetic-preparer",
        "reviewed_by": "synthetic-independent-reviewer",
        "reviewed_at": NOW.isoformat(),
        "valid_until": (NOW + timedelta(hours=2)).isoformat(),
        **changes,
    }


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QualificationContext:
    monkeypatch.setattr(universe, "utc_now", lambda: NOW)
    return QualificationContext(
        tmp_path / "bundle",
        tmp_path,
        {
            "TRADING212_API_KEY": "fixture-only-account-key-not-real",
            "TRADING212_API_SECRET": "fixture-only-account-secret-not-real",
        },
        NOW,
    )


def instrument(**changes: Any) -> dict[str, Any]:
    return {
        "ticker": "TESTl_EQ",
        "shortName": "TEST",
        "name": "Synthetic Example PLC",
        "isin": "GB00BH4HKS39",
        "currencyCode": "GBX",
        "type": "STOCK",
        "workingScheduleId": 11,
        **changes,
    }


class Broker:
    def __init__(self, instruments: list[Any] | None = None) -> None:
        self.instruments = [instrument()] if instruments is None else instruments
        self.exchanges = [
            {
                "id": 7,
                "name": "Synthetic UK venue",
                "mic": "XABC",
                "countryCode": "GB",
                "workingSchedules": [{"id": 11}],
            }
        ]
        self.raw = {
            "instruments": (" \n" + json.dumps(self.instruments) + "\n").encode(),
            "exchanges": (json.dumps(self.exchanges, indent=3) + " \n").encode(),
        }
        self.calls: list[str] = []

    def metadata_response(self, kind: str) -> tuple[bytes, tuple[Any, ...]]:
        assert kind in {"instruments", "exchanges"}
        self.calls.append(kind)
        return self.raw[kind], tuple(json.loads(self.raw[kind]))


class Enricher:
    def __init__(self, *, failing: str | None = None, same_ticker: bool = False) -> None:
        self.calls: list[str] = []
        self.failing, self.same_ticker = failing, same_ticker

    def enrich(self, source: dict[str, Any]) -> dict[str, Any]:
        row = deepcopy(source)
        self.calls.append(row["trading212_id"])
        if row["trading212_id"] == self.failing:
            raise ValueError("SYNTHETIC_PROVIDER_ROW_FAILURE")
        symbol = row["short_ticker"] + ".TESTVENUE"
        identifiers = InstrumentIdentifiers(
            ticker="TEST" if self.same_ticker else row["short_ticker"],
            company_name=row["name"],
            trading212_id=row["trading212_id"],
            exchange_ticker=row["short_ticker"],
            exchange="TESTVENUE",
            isin=row["isin"],
            quote_currency=row["quote_currency"],
            provider_symbols=(("eodhd", symbol),),
            verified_at=NOW,
            valid_until=NOW + timedelta(hours=2),
            source="Synthetic provider identity response",
        )
        row.update(
            identifiers=identifiers.model_dump(mode="json"),
            eodhd_symbol=symbol,
            eodhd_mapping_attempted=True,
            eodhd_lookup_origin="NETWORK",
            companies_house_state="NOT_APPLICABLE",
            provider_reasons=[],
            issuer_facts={"CountryISO": "US"},
            provider_evidence_observed_at=NOW.isoformat(),
            provider_evidence_valid_until=(NOW + timedelta(hours=2)).isoformat(),
            provider_datasets={
                "eodhd:" + name: DatasetProbe(
                    dataset=name,
                    ticker=identifiers.ticker,
                    status="RETRIEVED",
                    record_count=1,
                    earliest_observation=NOW,
                    latest_observation=NOW,
                ).model_dump(mode="json")
                for name in ("ohlcv", "corporate_action", "news")
            },
        )
        return row


def reviewed_scope(ctx: QualificationContext, **changes: Any) -> None:
    ctx.write_bytes(
        "inputs/test-account-evidence.txt", b"Synthetic ISA endpoint-scope and purchase evidence"
    )
    ctx.write_json(
        "inputs/universe/account-scope.json",
        {
            "review": stamp(),
            "account_context": "STOCKS_AND_SHARES_ISA",
            "retrieval_environment": "live",
            "credential_binding_sha256": universe.credential_binding(ctx.environ),
            "accessible_response_is_account_scoped": True,
            "accessible_response_confirms_current_buy_availability": True,
            "evidence_files": ["inputs/test-account-evidence.txt"],
            **changes,
        },
    )


def reviewed_ethics(
    ctx: QualificationContext,
    rows: list[dict[str, Any]],
    *,
    activities: tuple[str, ...] = ("food_retail",),
    **changes: Any,
) -> None:
    ctx.write_bytes(
        "inputs/test-material-exposure.txt", b"Synthetic complete material exposure test evidence"
    )
    ctx.write_bytes("inputs/test-source-rights.txt", b"Synthetic source use rights test evidence")
    ctx.write_json(
        "inputs/test-ethical-rights.json",
        {
            "review": stamp(),
            "permitted_use": "ethical-research",
            "evidence_files": ["inputs/test-source-rights.txt"],
        },
    )
    ctx.write_json(
        "inputs/universe/ethics.json",
        {
            "review": stamp(),
            "instruments": [
                {
                    "isin": item["isin"],
                    "company_name": item["name"],
                    "business_activities": activities,
                    "assessed_exclusions": EXCLUDED_ACTIVITIES,
                    "complete_material_exposure_review": True,
                    "evidence_files": ["inputs/test-material-exposure.txt"],
                    "source_rights_review_files": ["inputs/test-ethical-rights.json"],
                }
                for item in rows
            ],
            **changes,
        },
    )


def rights_fixture(ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch) -> None:
    proof = ctx.artifact(b"Synthetic provider qualification unit-test bytes")
    qualification = ProviderQualification(
        provider="eodhd",
        datasets=("ohlcv", "corporate_action", "news"),
        earliest_observation=NOW - timedelta(days=30),
        publication_times="AS_RETRIEVED",
        maximum_age_seconds=86400,
        production_qualified=True,
        qualified_by="synthetic-reviewer",
        qualification_report_hash=proof[0],
        verified_at=NOW,
        valid_until=NOW + timedelta(hours=2),
        redistribution="PROHIBITED",
        attribution="Synthetic test provider",
        source_documentation="https://example.test/synthetic-provider-contract",
    )
    # Provider probe/rights admission has its own non-mocked contract tests.
    # This unit isolates classification after that stage has returned a result.
    monkeypatch.setattr(
        universe,
        "_qualify_rights",
        lambda _ctx, _rows: {
            "eodhd": qualification.model_dump(mode="json"),
        },
    )


def test_unknown_incorporation_blocks_without_inventing_uk_filing_requirements(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [instrument()])
    rights_fixture(ctx, monkeypatch)

    class UnknownIssuer(Enricher):
        def enrich(self, source: dict[str, Any]) -> dict[str, Any]:
            row = super().enrich(source)
            row["issuer_facts"] = {}
            row["companies_house_state"] = "UNRESOLVED_APPLICABILITY"
            return row

    result = universe.finalize_universe(ctx, broker=Broker(), enricher=UnknownIssuer())
    row = result["stocks"][0]
    assert result["eligibility_reviews"] == []
    assert row["qualification_state"] == "UNRESOLVED_PROVIDER_MAPPING"
    assert "VERIFIED_ISSUER_JURISDICTION_REQUIRED" in row["reasons"]
    assert not any("CURRENT_COMPANIES-HOUSE" in reason for reason in row["reasons"])
    assert result["summary"]["companies_house_applicability_unknown"] == 1


def test_confirmed_uk_issuer_still_requires_real_company_and_filing_evidence(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [instrument()])
    rights_fixture(ctx, monkeypatch)

    class UKIssuer(Enricher):
        def enrich(self, source: dict[str, Any]) -> dict[str, Any]:
            row = super().enrich(source)
            row["issuer_facts"] = {"CountryISO": "GB"}
            row["companies_house_state"] = "UNRESOLVED"
            return row

    result = universe.finalize_universe(ctx, broker=Broker(), enricher=UKIssuer())
    row = result["stocks"][0]
    assert result["eligibility_reviews"] == []
    assert "CURRENT_COMPANIES-HOUSE_COMPANY_OBSERVATION_REQUIRED" in row["reasons"]
    assert "CURRENT_COMPANIES-HOUSE_FILING_OBSERVATION_REQUIRED" in row["reasons"]
    assert "PROVIDER_RIGHTS_AND_DATASET_QUALIFICATION_REQUIRED" in row["reasons"]


def test_bulk_work_prepares_facts_without_overwriting_or_signing_reviews(
    ctx: QualificationContext,
) -> None:
    universe.prepare_bulk_inputs(ctx, None)
    before = {
        path: ctx.read_bytes(path)
        for path in ("inputs/universe/account-scope.json", "inputs/universe/ethics.json")
    }
    result = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    for path, raw in before.items():
        assert ctx.read_bytes(path) == raw
    facts = ctx.read_json("outputs/universe-retrieval-facts.json")
    assert facts["provenance"]["credential_binding_sha256"] == universe.credential_binding(ctx.environ)
    assert "account_type_returned_by_api" not in facts
    assert "accessible_response_is_account_scoped" not in facts
    assert "accessible_response_confirms_current_buy_availability" not in facts
    work = ctx.read_json("outputs/universe-review-work.json")
    assert work["review_status"] == "UNREVIEWED_MACHINE_FACTS"
    assert work["instruments"][0]["eodhd_symbol"] == "TEST.TESTVENUE"
    assert work["instruments"][0]["human_approval_supplied"] is False
    queue = ctx.read_json("outputs/universe-review-queue.json")
    assert len(queue["provider_rights_reviews"]) == 2
    assert not any("PROVIDER_RIGHTS" in item for item in queue["instruments"][0]["reasons"])
    assert "PROVIDER_RIGHTS_AND_DATASET_QUALIFICATION_REQUIRED" in result["stocks"][0]["reasons"]
    assert result["eligibility_reviews"] == []


def test_network_budget_resumes_after_last_serviced_stock_not_start_of_list(
    ctx: QualificationContext,
) -> None:
    rows = [instrument(), instrument(ticker="ZZZl_EQ", shortName="ZZZ", isin="US0378331005")]

    class Budgeted(Enricher):
        requests_used = 0

        def enrich(self, source: dict[str, Any]) -> dict[str, Any]:
            if self.requests_used == 0:
                self.requests_used += 1
                self.serviced = source["trading212_id"]
                return super().enrich(source)
            self.calls.append(source["trading212_id"])
            return {**source, "provider_reasons": ["PROVIDER_REQUEST_BUDGET_EXHAUSTED"]}

    first, second = Budgeted(), Budgeted()
    initial = universe.finalize_universe(ctx, broker=Broker(rows), enricher=first)
    resumed = universe.finalize_universe(ctx, broker=Broker(rows), enricher=second)
    assert first.serviced == "TESTl_EQ"
    assert second.serviced == "ZZZl_EQ"
    assert len(first.calls) == len(second.calls) == 2
    assert initial["summary"]["provider_requests_this_run"] == 1
    assert resumed["summary"]["provider_deferred"] == 1
    assert initial["summary"]["provider_stage_input_count"] == 2
    assert resumed["eligibility_reviews"] == []


def test_dataset_probe_budget_failures_are_counted_once_per_stock(
    ctx: QualificationContext,
) -> None:
    class DeferredDatasets(Enricher):
        def enrich(self, source: dict[str, Any]) -> dict[str, Any]:
            row = super().enrich(source)
            row["provider_datasets"] = {}
            row["provider_reasons"] = []
            row["provider_request_diagnostics"] = [
                {"endpoint": name, "error_code": "PROVIDER_REQUEST_BUDGET_EXHAUSTED"}
                for name in ("eod", "splits", "news")
            ]
            return row

    result = universe.finalize_universe(ctx, broker=Broker(), enricher=DeferredDatasets())
    assert result["summary"]["provider_deferred"] == 1
    assert result["summary"]["provider_failure_counts"]["PROVIDER_REQUEST_BUDGET_EXHAUSTED"] == 1
    assert result["eligibility_reviews"] == []


def test_review_with_unknown_activities_never_displays_ethically_cleared(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [instrument()], activities=("unknown_material_exposure",))
    rights_fixture(ctx, monkeypatch)
    result = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    assert result["stocks"][0]["ethical_state"] == "ETHICAL_REVIEW_REQUIRED"
    assert result["stocks"][0]["qualification_state"] == "UNRESOLVED_ETHICAL"
    assert result["eligibility_reviews"] == []


def test_full_refresh_preserves_raw_hashes_and_only_bulk_templates(
    ctx: QualificationContext,
) -> None:
    broker = Broker(
        [
            instrument(),
            instrument(
                ticker="FOREIGNl_EQ", shortName="FOREIGN", isin="US0378331005", currencyCode="GBP"
            ),
            instrument(ticker="FUNDI_EQ", type="ETF"),
            None,
        ]
    )
    result = universe.finalize_universe(ctx, broker=broker, enricher=Enricher())
    assert broker.calls == ["instruments", "exchanges"]
    assert result["summary"]["raw_instruments"] == 4
    assert result["summary"]["gbx_stocks"] == 1
    assert result["summary"]["venue_resolved"] == 1
    assert result["stocks"][1]["qualification_state"] == "EXCLUDED_NON_GBX"
    assert result["summary"]["qualified"] == 0
    assert result["summary"]["unresolved"] == 1
    assert result["summary"]["states"] == {"UNRESOLVED_ETHICAL": 1}
    assert result["summary"]["raw_states"]["UNRESOLVED_IDENTITY"] == 1
    assert (
        result["provenance"]["instrument_response_hash"]
        == hashlib.sha256(broker.raw["instruments"]).hexdigest()
    )
    assert (
        result["provenance"]["exchange_response_hash"]
        == hashlib.sha256(broker.raw["exchanges"]).hexdigest()
    )
    assert "account_context" not in result["provenance"]
    for kind, reference in result["provenance"]["response_artifacts"].items():
        index = json.loads(ctx.verify_artifact(*reference))
        actual = b"".join(ctx.verify_artifact(*part) for part in index["chunks"])
        assert actual == broker.raw[kind]
    assert not list((ctx.root / "inputs/instruments").glob("*.json"))
    assert ctx.read_json("state/bulk-universe-mode.json") is not None
    assert ctx.read_json("inputs/universe/account-scope.json") is None
    assert result["production_qualified"] is False


@pytest.mark.parametrize(
    "exchanges",
    [
        [],
        [
            {
                "id": 7,
                "name": "Synthetic venue, geography unavailable",
                "workingSchedules": [{"id": 11}],
            }
        ],
        [
            {
                "id": 7,
                "name": "Synthetic non-UK venue",
                "mic": "XNAS",
                "countryCode": "US",
                "workingSchedules": [{"id": 11}],
            }
        ],
    ],
)
def test_every_gbx_stock_reaches_provider_lookup_without_uk_venue(
    ctx: QualificationContext, exchanges: list[dict[str, Any]]
) -> None:
    broker = Broker(
        [instrument(), instrument(ticker="FOREIGNl_EQ", shortName="FOREIGN", isin="US0378331005")]
    )
    broker.raw["exchanges"] = json.dumps(exchanges).encode()
    enricher = Enricher()
    result = universe.finalize_universe(ctx, broker=broker, enricher=enricher)
    assert sorted(enricher.calls) == ["FOREIGNl_EQ", "TESTl_EQ"]
    assert result["summary"]["gbx_stocks"] == 2
    assert result["summary"]["eodhd_mapping_attempted"] == 2
    assert result["summary"]["eodhd_mapped"] == 2
    assert result["summary"]["qualified"] == 0
    assert result["summary"]["unresolved"] == 2
    assert "uk_venue_stocks" not in result["summary"]
    for row in result["stocks"]:
        assert row["universe_member"] is True
        assert row["qualification_state"] == "UNRESOLVED_ETHICAL"
        assert row["ethical_state"] == "ETHICAL_REVIEW_REQUIRED"
        assert row["identifiers"]["exchange"] == "TESTVENUE"
        assert not any("VENUE" in reason for reason in row["reasons"])
    assert result["eligibility_reviews"] == []
    assert ctx.read_json("outputs/universe-review-queue.json")["venue_review_required"] is False


def test_only_gbx_stocks_are_enriched_and_counted(ctx: QualificationContext) -> None:
    broker = Broker(
        [
            instrument(),
            instrument(ticker="GBP_EQ", currencyCode="GBP", isin="US0378331005"),
            instrument(ticker="USD_EQ", currencyCode="USD"),
            instrument(ticker="EUR_EQ", currencyCode="EUR"),
            instrument(ticker="ETF_EQ", type="ETF"),
            instrument(ticker="FUND_EQ", type="FUND"),
        ]
    )
    enricher = Enricher()
    result = universe.finalize_universe(ctx, broker=broker, enricher=enricher)
    assert enricher.calls == ["TESTl_EQ"]
    assert result["summary"]["raw_instruments"] == 6
    assert result["summary"]["gbx_stocks"] == 1
    assert result["summary"]["eodhd_mapping_attempted"] == 1
    assert result["summary"]["excluded"] == 5
    assert result["initial_filter"] == {
        "instrument_type": "STOCK",
        "quote_currency": "GBX",
        "venue_required": False,
    }


def test_missing_venue_does_not_prevent_otherwise_fully_reviewed_qualification(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [instrument()])
    rights_fixture(ctx, monkeypatch)
    broker = Broker()
    broker.raw["exchanges"] = b"[]"
    result = universe.finalize_universe(ctx, broker=broker, enricher=Enricher())
    assert result["summary"]["qualified"] == 1
    assert result["summary"]["venue_resolved"] == 0
    assert result["stocks"][0]["qualification_state"] == "QUALIFIED"
    assert len(result["eligibility_reviews"]) == 1
    assert result["production_qualified"] is False


@pytest.mark.parametrize(
    "missing_gate", ["provider-rights", "ethics", "provider-identity"]
)
def test_removing_venue_requirement_does_not_remove_other_admission_gates(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch, missing_gate: str
) -> None:
    if missing_gate != "ethics":
        reviewed_ethics(ctx, [instrument()])
    if missing_gate != "provider-rights":
        rights_fixture(ctx, monkeypatch)
    broker = Broker()
    broker.raw["exchanges"] = b"[]"
    enricher = Enricher(failing="TESTl_EQ" if missing_gate == "provider-identity" else None)
    result = universe.finalize_universe(ctx, broker=broker, enricher=enricher)
    assert result["summary"]["gbx_stocks"] == 1
    assert result["summary"]["qualified"] == 0
    assert result["stocks"][0]["qualification_state"] != "UNRESOLVED_IDENTITY"
    assert result["eligibility_reviews"] == []
    assert result["production_qualified"] is False


def test_repeated_fresh_discovery_never_approves_ethics(
    ctx: QualificationContext,
) -> None:
    for _ in range(2):
        result = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
        row = result["stocks"][0]
        assert row["qualification_state"] == "UNRESOLVED_ETHICAL"
        assert row["ethical_state"] == "ETHICAL_REVIEW_REQUIRED"
        assert row["eligibility_review"] is None
        assert result["eligibility_reviews"] == []


def test_one_provider_failure_does_not_stop_other_rows(ctx: QualificationContext) -> None:
    broker = Broker(
        [
            instrument(ticker="BADl_EQ"),
            instrument(ticker="GOODl_EQ", shortName="GOOD", isin="US0378331005"),
        ]
    )
    enricher = Enricher(failing="BADl_EQ")
    result = universe.finalize_universe(ctx, broker=broker, enricher=enricher)
    assert enricher.calls == ["BADl_EQ", "GOODl_EQ"]
    assert result["stocks"][0]["provider_reasons"] == ["PROVIDER_ROW_FAILED"]
    assert result["stocks"][1]["identifiers"]["ticker"] == "GOOD"


@pytest.mark.parametrize("legacy_review", ["missing", "unresolved", "expired", "wrong-binding"])
def test_account_scope_is_not_required_and_legacy_review_is_ignored(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch, legacy_review: str
) -> None:
    if legacy_review == "unresolved":
        ctx.write_json("inputs/universe/account-scope.json", {"review": {"status": "UNRESOLVED"}})
    elif legacy_review == "expired":
        reviewed_scope(ctx, review=stamp(valid_until=(NOW - timedelta(hours=25)).isoformat()))
    elif legacy_review == "wrong-binding":
        reviewed_scope(ctx, credential_binding_sha256=hashlib.sha256(b"other key").hexdigest())
    prior = ctx.read_bytes("inputs/universe/account-scope.json")
    reviewed_ethics(ctx, [instrument()])
    rights_fixture(ctx, monkeypatch)
    result = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    assert result["summary"]["qualified"] == 1
    assert ctx.read_bytes("inputs/universe/account-scope.json") == prior
    review = result["eligibility_reviews"][0]
    assert review["metadata"].get("isa_available") is not True
    assert "ACCOUNT_ISA_SCOPE_REVIEW_REQUIRED" not in json.dumps(result)
    assert "ACCOUNT_AND_CURRENT_BUY_PROVENANCE_REQUIRED" not in json.dumps(result)
    assert "UNRESOLVED_ISA_SCOPE" not in json.dumps(result)


def test_only_fully_reviewed_peer_qualifies_without_entire_universe_approval(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = instrument()
    unknown = instrument(ticker="OTHERl_EQ", shortName="OTHER", isin="US0378331005")
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [good])
    rights_fixture(ctx, monkeypatch)
    result = universe.finalize_universe(ctx, broker=Broker([good, unknown]), enricher=Enricher())
    assert [row["qualification_state"] for row in result["stocks"]] == [
        "QUALIFIED",
        "UNRESOLVED_ETHICAL",
    ]
    assert result["summary"]["qualified"] == 1
    assert len(result["eligibility_reviews"]) == 1
    assert result["eligibility_reviews"][0]["metadata"]["ticker"] == "TEST"
    assert result["production_qualified"] is False
    for digest, path in result["artifact_refs"]:
        assert hashlib.sha256(ctx.verify_artifact(digest, path)).hexdigest() == digest


@pytest.mark.parametrize("activity", EXCLUDED_ACTIVITIES)
def test_every_existing_ethical_exclusion_still_rejects(
    ctx: QualificationContext, activity: str
) -> None:
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [instrument()], activities=(activity,))
    result = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    assert result["stocks"][0]["qualification_state"] == "EXCLUDED_ETHICAL"
    assert result["stocks"][0]["ethical_state"] == "ETHICALLY_EXCLUDED"
    assert result["eligibility_reviews"] == []


def test_partial_exposure_and_unapproved_sources_never_clear_ethics(
    ctx: QualificationContext,
) -> None:
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [instrument()])
    value = ctx.read_json("inputs/universe/ethics.json")
    value["instruments"][0]["assessed_exclusions"] = ["defence"]
    ctx.write_json("inputs/universe/ethics.json", value)
    assert universe._ethics(ctx) == {}
    reviewed_ethics(ctx, [instrument()])
    rights = ctx.read_json("inputs/test-ethical-rights.json")
    rights["review"]["status"] = "UNRESOLVED"
    ctx.write_json("inputs/test-ethical-rights.json", rights)
    assert universe._ethics(ctx) == {}


def test_empty_required_dataset_is_not_a_qualifying_observation(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [instrument()])
    rights_fixture(ctx, monkeypatch)

    class EmptyNews(Enricher):
        def enrich(self, source: dict[str, Any]) -> dict[str, Any]:
            row = super().enrich(source)
            row["provider_datasets"]["eodhd:news"] = {"status": "EMPTY", "record_count": 0}
            return row

    result = universe.finalize_universe(ctx, broker=Broker(), enricher=EmptyNews())
    row = result["stocks"][0]
    assert row["qualification_state"] == "UNRESOLVED_PROVIDER_MAPPING"
    assert "CURRENT_EODHD_NEWS_OBSERVATION_REQUIRED" in row["reasons"]


def test_expired_membership_is_not_admitted(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [instrument()])
    rights_fixture(ctx, monkeypatch)

    class Expired(Enricher):
        def enrich(self, source: dict[str, Any]) -> dict[str, Any]:
            row = super().enrich(source)
            row["observed_at"] = (NOW - timedelta(days=2)).isoformat()
            row["valid_until"] = NOW.isoformat()
            return row

    result = universe.finalize_universe(ctx, broker=Broker(), enricher=Expired())
    assert result["stocks"][0]["qualification_state"] == "EXPIRED"
    assert result["eligibility_reviews"] == []


def test_provider_echoed_credential_is_never_written(ctx: QualificationContext) -> None:
    broker = Broker([instrument(name=ctx.environ["TRADING212_API_KEY"])])
    result = universe.finalize_universe(ctx, broker=broker, enricher=Enricher())
    assert result["status"] == "REFRESH_FAILED"
    for path in ctx.root.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            assert ctx.environ["TRADING212_API_KEY"].encode() not in raw
            assert ctx.environ["TRADING212_API_SECRET"].encode() not in raw
    assert not list((ctx.root / "artifacts").glob("*"))


def test_failed_refresh_invalidates_prior_master_not_stale_membership(
    ctx: QualificationContext,
) -> None:
    result = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    assert len(result["stocks"]) == 1

    class FailedBroker(Broker):
        def metadata_response(self, kind: str) -> tuple[bytes, tuple[Any, ...]]:
            raise OSError("Synthetic connection failure")

    result = universe.finalize_universe(ctx, broker=FailedBroker(), enricher=Enricher())
    assert result["status"] == "REFRESH_FAILED"
    saved = ctx.read_json(universe.MASTER)
    assert saved["stocks"] == []
    assert saved["summary"] is None
    assert len((ctx.root / universe.MASTER_CSV).read_text().splitlines()) == 1


def test_csv_formula_injection_is_escaped_without_changing_json() -> None:
    raw = universe._csv([{"name": '=HYPERLINK("https://example.test")', "reasons": []}])
    assert b"'=HYPERLINK" in raw


def test_conflicting_canonical_tickers_never_admit_two_economic_identities(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [instrument(), instrument(ticker="OTHERl_EQ", shortName="OTHER", isin="US0378331005")]
    reviewed_scope(ctx)
    reviewed_ethics(ctx, rows)
    rights_fixture(ctx, monkeypatch)
    result = universe.finalize_universe(
        ctx, broker=Broker(rows), enricher=Enricher(same_ticker=True)
    )
    assert result["summary"]["qualified"] == 0
    assert all(row["qualification_state"] == "UNRESOLVED_IDENTITY" for row in result["stocks"])
    assert result["eligibility_reviews"] == []


def test_stale_provider_evidence_never_requalifies_from_new_broker_metadata(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [instrument()])
    rights_fixture(ctx, monkeypatch)

    class StaleProvider(Enricher):
        def enrich(self, source: dict[str, Any]) -> dict[str, Any]:
            row = super().enrich(source)
            row["provider_evidence_observed_at"] = (NOW - timedelta(days=2)).isoformat()
            row["provider_evidence_valid_until"] = NOW.isoformat()
            return row

    result = universe.finalize_universe(ctx, broker=Broker(), enricher=StaleProvider())
    assert result["stocks"][0]["qualification_state"] == "STALE_EVIDENCE"
    assert result["eligibility_reviews"] == []


def test_expired_membership_remains_expired_even_without_account_review(
    ctx: QualificationContext,
) -> None:
    class Expired(Enricher):
        def enrich(self, source: dict[str, Any]) -> dict[str, Any]:
            row = super().enrich(source)
            row["observed_at"] = (NOW - timedelta(days=2)).isoformat()
            row["valid_until"] = NOW.isoformat()
            return row

    result = universe.finalize_universe(ctx, broker=Broker(), enricher=Expired())
    assert result["stocks"][0]["qualification_state"] == "EXPIRED"
    assert result["eligibility_reviews"] == []


def test_ethical_review_expiring_during_fetch_cannot_create_expired_qualification(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_ethics(
        ctx, [instrument()], review=stamp(valid_until=(NOW + timedelta(minutes=1)).isoformat())
    )
    rights_fixture(ctx, monkeypatch)

    class SlowProvider(Enricher):
        def enrich(self, source: dict[str, Any]) -> dict[str, Any]:
            row = super().enrich(source)
            monkeypatch.setattr(universe, "utc_now", lambda: NOW + timedelta(minutes=2))
            return row

    result = universe.finalize_universe(ctx, broker=Broker(), enricher=SlowProvider())
    assert result["summary"]["qualified"] == 0
    assert result["eligibility_reviews"] == []


def test_unresolved_company_identity_is_not_fixed_by_other_stocks_provider_rights(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [instrument()])
    rights_fixture(ctx, monkeypatch)
    qualified = universe._qualify_rights(ctx, [])
    qualified["companies-house"] = {
        **qualified["eodhd"],
        "provider": "companies-house",
        "datasets": ["company", "filing"],
    }
    monkeypatch.setattr(universe, "_qualify_rights", lambda _ctx, _rows: qualified)

    class AmbiguousCompany(Enricher):
        def enrich(self, source: dict[str, Any]) -> dict[str, Any]:
            row = super().enrich(source)
            row["companies_house_state"] = "UNRESOLVED"
            row["issuer_facts"] = {"CountryISO": "GB"}
            row["provider_datasets"].update(
                {
                    "companies-house:" + name: DatasetProbe(
                        dataset=name,
                        ticker="TEST",
                        status="RETRIEVED",
                        record_count=1,
                        earliest_observation=NOW,
                        latest_observation=NOW,
                    ).model_dump(mode="json")
                    for name in ("company", "filing")
                }
            )
            return row

    result = universe.finalize_universe(ctx, broker=Broker(), enricher=AmbiguousCompany())
    assert result["stocks"][0]["qualification_state"] == "UNRESOLVED_PROVIDER_MAPPING"
    assert result["eligibility_reviews"] == []


def test_successful_broker_cache_reuses_exact_bytes_for_less_than_ten_minutes(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = Broker()
    monkeypatch.setattr(universe, "Trading212MetadataProvider", lambda *_: broker)
    binding = universe.credential_binding(ctx.environ)
    first = universe._broker_metadata(ctx, binding)
    ctx.now = NOW + timedelta(minutes=10, microseconds=-1)
    assert universe._broker_metadata(ctx, binding) == first
    assert broker.calls == ["instruments", "exchanges"]
    ctx.now = NOW + timedelta(minutes=10)
    monkeypatch.setattr(universe, "utc_now", lambda: ctx.now)
    refreshed = universe._broker_metadata(ctx, binding)
    assert broker.calls == ["instruments", "exchanges", "instruments", "exchanges"]
    assert refreshed[-1] == ctx.now
    assert refreshed[0] == first[0]


@pytest.mark.parametrize("cached", [False, True])
def test_optional_exchange_outage_does_not_stop_gbx_pipeline(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch, cached: bool
) -> None:
    class ExchangeOutage(Broker):
        def metadata_response(self, kind: str) -> tuple[bytes, tuple[Any, ...]]:
            if kind == "exchanges":
                self.calls.append(kind)
                raise OSError("Synthetic optional enrichment outage")
            return super().metadata_response(kind)

    broker = ExchangeOutage()
    monkeypatch.setattr(universe, "Trading212MetadataProvider", lambda *_: broker)
    for _ in range(2 if cached else 1):
        enricher = Enricher()
        result = universe.finalize_universe(
            ctx, broker=None if cached else broker, enricher=enricher
        )
        assert result["status"] == "REFRESHED"
        assert result["summary"]["gbx_stocks"] == 1
        assert result["summary"]["eodhd_mapping_attempted"] == 1
        assert enricher.calls == ["TESTl_EQ"]
        assert result["stocks"][0]["qualification_state"] == "UNRESOLVED_ETHICAL"
        assert result["provenance"]["exchange_response_hash"] is None
        assert result["provenance"]["exchange_enrichment_status"] == "UNAVAILABLE_OR_INVALID"
        assert "exchanges" not in result["provenance"]["response_artifacts"]
    assert broker.calls == ["instruments", "exchanges"]


def test_invalid_optional_exchange_cache_is_discarded_not_used_as_identity(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = Broker()
    monkeypatch.setattr(universe, "Trading212MetadataProvider", lambda *_: broker)
    binding = universe.credential_binding(ctx.environ)
    original = universe._broker_metadata(ctx, binding)
    state = ctx.read_json("state/bulk-broker-metadata.json")
    ctx.write_bytes(state["responses"]["exchanges"][1], b"Synthetic corrupted enrichment")
    restored = universe._broker_metadata(ctx, binding)
    assert restored[:2] == original[:2]
    assert restored[2:4] == (None, ())
    assert broker.calls == ["instruments", "exchanges"]


def test_finalizer_summary_uses_gbx_denominator_and_no_uk_venue_gate(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    broker = Broker(
        [instrument(), instrument(ticker="OTHER_EQ", currencyCode="GBP", isin="US0378331005")]
    )
    broker.raw["exchanges"] = b"[]"
    result = universe.finalize_universe(ctx, broker=broker, enricher=Enricher())
    monkeypatch.setattr(universe, "load_inference_selections", lambda *_: {})
    monkeypatch.setattr(universe, "finalize_universe", lambda *_args, **_kwargs: result)
    assert universe.main(["--output", str(ctx.root)]) == 2
    output = capsys.readouterr().out
    assert "Raw instruments: 2\nGBX stocks: 1" in output
    assert "Venue resolved (informational only): 0/1" in output
    assert "EODHD mappings attempted: 1/1" in output
    assert "EODHD mapped: 1/1" in output
    assert "ISA scope" not in output
    assert "UK venue stocks" not in output
    assert "Stocks GBP/GBX" not in output


def test_broker_cache_never_crosses_credential_account_context(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = Broker()
    monkeypatch.setattr(universe, "Trading212MetadataProvider", lambda *_: broker)
    universe._broker_metadata(ctx, universe.credential_binding(ctx.environ))
    ctx.environ = {**ctx.environ, "TRADING212_API_KEY": "other-fixture-account-key-not-real"}
    universe._broker_metadata(ctx, universe.credential_binding(ctx.environ))
    assert broker.calls == ["instruments", "exchanges", "instruments", "exchanges"]
    state = ctx.read_json("state/bulk-broker-metadata.json")
    assert state["credential_binding_sha256"] == universe.credential_binding(ctx.environ)


def test_expired_cache_and_failed_refresh_clear_live_membership(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = Broker()
    monkeypatch.setattr(universe, "Trading212MetadataProvider", lambda *_: broker)
    first = universe.finalize_universe(ctx, enricher=Enricher())
    assert first["status"] == "REFRESHED"

    class Broken(Broker):
        def metadata_response(self, kind: str) -> tuple[bytes, tuple[Any, ...]]:
            raise OSError("Synthetic broker outage")

    monkeypatch.setattr(universe, "Trading212MetadataProvider", lambda *_: Broken())
    ctx.now = NOW + timedelta(minutes=11)
    monkeypatch.setattr(universe, "utc_now", lambda: ctx.now)
    failed = universe.finalize_universe(ctx, enricher=Enricher())
    assert failed["status"] == "REFRESH_FAILED"
    assert ctx.read_json(universe.MASTER)["stocks"] == []
    assert ctx.read_json("state/bulk-broker-metadata.json")["status"] == "ATTEMPTED"
    saved = universe._saved_broker_metadata(ctx)
    assert saved[0] == broker.raw["instruments"]
    assert saved[4].isoformat() == first["observed_at"]


def test_broker_failure_backoff_prevents_retries_within_fifty_seconds(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Failing(Broker):
        def metadata_response(self, kind: str) -> tuple[bytes, tuple[Any, ...]]:
            self.calls.append(kind)
            raise OSError("Synthetic rate-limited provider")

    broker = Failing()
    monkeypatch.setattr(universe, "Trading212MetadataProvider", lambda *_: broker)
    binding = universe.credential_binding(ctx.environ)
    with pytest.raises(OSError):
        universe._broker_metadata(ctx, binding)
    ctx.now = NOW + timedelta(seconds=49)
    with pytest.raises(ValueError, match="BROKER_METADATA_RETRY_BACKOFF"):
        universe._broker_metadata(ctx, binding)
    assert broker.calls == ["instruments"]
    ctx.now = NOW + timedelta(seconds=50)
    monkeypatch.setattr(universe, "utc_now", lambda: ctx.now)
    with pytest.raises(OSError):
        universe._broker_metadata(ctx, binding)
    assert broker.calls == ["instruments", "instruments"]


@pytest.mark.parametrize(
    "problem", ["tampered-artifact", "invalid-descriptor", "non-array-response"]
)
def test_malformed_broker_cache_fails_closed(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    broker = Broker()
    monkeypatch.setattr(universe, "Trading212MetadataProvider", lambda *_: broker)
    binding = universe.credential_binding(ctx.environ)
    universe._broker_metadata(ctx, binding)
    path = "state/bulk-broker-metadata.json"
    state = ctx.read_json(path)
    if problem == "tampered-artifact":
        ctx.write_bytes(state["responses"]["instruments"][1], b"tampered synthetic bytes")
    elif problem == "invalid-descriptor":
        state["responses"]["instruments"] = ctx.artifact(
            {"bytes": 100, "sha256": hashlib.sha256(b"x").hexdigest(), "chunks": []}
        )
    else:
        state["responses"]["instruments"] = universe._store_response(ctx, b'{"not": "an array"}')
    ctx.write_json(path, state)
    with pytest.raises(ValueError):
        universe._broker_metadata(ctx, binding)
    assert broker.calls == ["instruments", "exchanges"]


def test_provider_currency_qualification_cannot_be_reused_for_other_quote_currency(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [instrument()])
    rights_fixture(ctx, monkeypatch)
    qualification = universe._qualify_rights(ctx, [])
    qualification["eodhd"]["currencies"] = ["GBP"]
    monkeypatch.setattr(universe, "_qualify_rights", lambda _ctx, _rows: qualification)
    result = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    assert result["stocks"][0]["qualification_state"] == "UNRESOLVED_PROVIDER_MAPPING"
    assert (
        "PROVIDER_CURRENCY_DATASET_OR_FRESHNESS_COVERAGE_REQUIRED" in result["stocks"][0]["reasons"]
    )
    assert result["eligibility_reviews"] == []


def test_recent_provider_retrieval_does_not_make_old_ohlcv_current(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [instrument()])
    rights_fixture(ctx, monkeypatch)

    class OldBars(Enricher):
        def enrich(self, source: dict[str, Any]) -> dict[str, Any]:
            row = super().enrich(source)
            row["provider_datasets"]["eodhd:ohlcv"]["latest_observation"] = (
                NOW - timedelta(hours=96, seconds=1)
            ).isoformat()
            return row

    result = universe.finalize_universe(ctx, broker=Broker(), enricher=OldBars())
    assert result["stocks"][0]["qualification_state"] == "STALE_EVIDENCE"
    assert result["eligibility_reviews"] == []


@pytest.mark.parametrize(
    "limiting_evidence", ["ethical-source-rights", "provider-qualification"]
)
def test_shortest_evidence_deadline_propagates_into_eligibility_identifiers(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch, limiting_evidence: str
) -> None:
    reviewed_scope(ctx)
    reviewed_ethics(ctx, [instrument()])
    rights_fixture(ctx, monkeypatch)
    deadline = NOW + timedelta(hours=1)
    if limiting_evidence == "ethical-source-rights":
        source = ctx.read_json("inputs/test-ethical-rights.json")
        source["review"]["valid_until"] = deadline.isoformat()
        ctx.write_json("inputs/test-ethical-rights.json", source)
    else:
        qualification = universe._qualify_rights(ctx, [])
        qualification["eodhd"]["valid_until"] = deadline.isoformat()
        monkeypatch.setattr(universe, "_qualify_rights", lambda _ctx, _rows: qualification)
    result = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    row = result["stocks"][0]
    assert row["qualification_state"] == "QUALIFIED"
    assert row["valid_until"] == deadline.isoformat()
    assert datetime.fromisoformat(row["identifiers"]["valid_until"]) == deadline
    review = result["eligibility_reviews"][0]
    assert datetime.fromisoformat(review["identifiers"]["valid_until"]) == deadline
    identifiers = InstrumentIdentifiers.model_validate(review["identifiers"])
    identifiers.require_current(deadline - timedelta(microseconds=1))
    with pytest.raises(ValueError, match="IDENTIFIER_MAPPING_STALE"):
        identifiers.require_current(deadline)
    proof_path = next(
        path for digest, path in ctx.artifact_refs if digest == review["eligibility_proof_hash"]
    )
    proof = json.loads(ctx.verify_artifact(review["eligibility_proof_hash"], proof_path))
    assert datetime.fromisoformat(proof["identifiers"]["valid_until"]) == deadline
