"""Corruption, compatibility and fail-closed commercial-rights regression tests."""

import hashlib
import json
import shutil
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from money.flows.research import build_runtime
from money.reference import (
    CommercialLicenceError,
    ReferenceTableError,
    compare_reference_releases,
    load_reference_catalog,
    require_manifest_commercial_rights,
    require_snapshot_commercial_rights,
)
from money.reference import catalog as catalog_module
from money.reference.catalog import MAX_FILE_BYTES, default_data_root
from money.schemas.contracts import ResearchSnapshot


@pytest.fixture
def assets(tmp_path):
    target = tmp_path / "assets"
    shutil.copytree(default_data_root(), target)
    return target


def replace_json(path, mutate):
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


def rehash(assets, name, *, schema=False):
    path = assets / "metadata" / f"{name}.json"
    metadata = json.loads(path.read_text())
    if schema:
        target = assets / "schemas" / f"{name}.schema.json"
        key = "schema_checksum"
    else:
        suffix = ".json" if name == "provider_licences" else ".csv"
        target = assets / "reference" / f"{name}{suffix}"
        key = "checksum"
    metadata[key] = hashlib.sha256(target.read_bytes()).hexdigest()
    path.write_text(json.dumps(metadata))


def test_release_catalog_validates_and_is_immutable():
    catalog = load_reference_catalog()
    assert len(catalog.diagnostics()) == 4
    assert catalog.plan("FREE").monthly_tokens == 0
    assert catalog.plan("PRO").max_job_tokens == 10000
    assert catalog.plan("TEAM").seats == 5
    assert all(item["data_version"] == "1.0.0" for item in catalog.diagnostics())
    with pytest.raises(TypeError):
        catalog.tables["plans"] = None
    with pytest.raises(ValidationError):
        catalog.plan("FREE").seats = 99
    with pytest.raises(ReferenceTableError, match="UNKNOWN_PLAN"):
        catalog.plan("ATTACKER")


def test_loading_is_deterministic_and_independent_of_working_directory(tmp_path, monkeypatch):
    first = load_reference_catalog().diagnostics()
    monkeypatch.chdir(tmp_path)
    assert load_reference_catalog().diagnostics() == first


def test_checksum_drift_fails_without_repair(assets):
    target = assets / "reference" / "plans.csv"
    target.write_bytes(target.read_bytes() + b"\n")
    before = target.read_bytes()
    with pytest.raises(ReferenceTableError, match="checksum mismatch"):
        load_reference_catalog(assets)
    assert target.read_bytes() == before


def test_schema_checksum_drift_is_rejected(assets):
    target = assets / "schemas" / "plans.schema.json"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(ReferenceTableError, match="schema checksum"):
        load_reference_catalog(assets)


def test_changed_schema_cannot_relax_python_contract(assets):
    replace_json(
        assets / "schemas" / "plans.schema.json",
        lambda data: data["properties"]["seats"].update({"minimum": 0}),
    )
    rehash(assets, "plans", schema=True)
    with pytest.raises(ReferenceTableError, match="schema incompatible"):
        load_reference_catalog(assets)


@pytest.mark.parametrize("version", [0, 2, "1", True])
def test_manifest_schema_version_is_strict(assets, version):
    replace_json(assets / "manifest.json", lambda data: data.update(schema_version=version))
    with pytest.raises(ReferenceTableError):
        load_reference_catalog(assets)


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown"])
def test_manifest_requires_exact_supported_tables(assets, change):
    def mutate(data):
        if change == "missing":
            data["tables"].pop()
        elif change == "duplicate":
            data["tables"].append(data["tables"][0])
        else:
            data["tables"][0]["table_name"] = "secrets"

    replace_json(assets / "manifest.json", mutate)
    with pytest.raises(ReferenceTableError):
        load_reference_catalog(assets)


@pytest.mark.parametrize("path", ["../plans.csv", "/etc/passwd", "reference\\plans.csv"])
def test_paths_cannot_escape_data_directory(assets, path):
    replace_json(
        assets / "manifest.json",
        lambda data: data["tables"][0].update(path=path),
    )
    with pytest.raises(ReferenceTableError, match="unsafe reference asset path"):
        load_reference_catalog(assets)


def test_symlink_is_not_a_versioned_asset(assets):
    target = assets / "reference" / "plans.csv"
    saved = target.read_bytes()
    target.unlink()
    destination = assets / "copied.csv"
    destination.write_bytes(saved)
    target.symlink_to(destination)
    with pytest.raises(ReferenceTableError, match="symlinks"):
        load_reference_catalog(assets)


@pytest.mark.parametrize("size", [0, MAX_FILE_BYTES + 1])
def test_bounded_file_reads(assets, size):
    (assets / "reference" / "plans.csv").write_bytes(b"a" * size)
    with pytest.raises(ReferenceTableError, match="size invalid"):
        load_reference_catalog(assets)


def test_missing_metadata_cannot_silently_default(assets):
    (assets / "metadata" / "plans.json").unlink()
    with pytest.raises(ReferenceTableError, match="unreadable"):
        load_reference_catalog(assets)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_date", "2026-02-30"),
        ("source_date", "20260916"),
        ("source_date", "2027-01-01"),
        ("updated_at", "2026-09-16T00:00:00"),
        ("updated_at", "2026-09-16T00:00:00+01:00"),
        ("owner", ""),
        ("source", ""),
        ("provenance", ""),
        ("licence", ""),
        ("data_version", "latest"),
        ("schema_version", 2),
        ("schema_version", True),
        ("table_name", "wrong_table"),
    ],
)
def test_metadata_required_provenance_and_versions(assets, field, value):
    replace_json(assets / "metadata" / "plans.json", lambda data: data.update({field: value}))
    with pytest.raises(ReferenceTableError):
        load_reference_catalog(assets)


@pytest.mark.parametrize(
    "old,new",
    [
        ("FREE,3,1", "FREE,3,0"),
        ("FREE,3,1", "FREE,3,-1"),
        ("FREE,3,1", "FREE,3,1.0"),
        ("FREE,3,1", "FREE,3,01"),
        ("FREE,3,1", "FREE,3,true"),
        ("FREE,3,1", "FREE,3,"),
        ("false,false,false", "yes,false,false"),
        ("false,false,false", "TRUE,false,false"),
        ("FREE,3,1,7,false", "FREE,3,1,7,true"),
        ("100000,10000,1800", "100000,200000,1800"),
        ("FREE,3", "ADMIN,3"),
    ],
)
def test_malformed_or_impossible_plan_rows(assets, old, new):
    target = assets / "reference" / "plans.csv"
    target.write_text(target.read_text().replace(old, new))
    rehash(assets, "plans")
    with pytest.raises(ReferenceTableError):
        load_reference_catalog(assets)


def test_duplicate_plan_primary_key(assets):
    target = assets / "reference" / "plans.csv"
    target.write_text(target.read_text() + target.read_text().splitlines()[1] + "\n")
    rehash(assets, "plans")
    with pytest.raises(ReferenceTableError, match="duplicate primary key"):
        load_reference_catalog(assets)


@pytest.mark.parametrize("change", ["missing", "unknown", "duplicate", "reordered"])
def test_csv_headers_are_exact_and_versioned(assets, change):
    target = assets / "reference" / "plans.csv"
    lines = target.read_text().splitlines()
    headers = lines[0].split(",")
    if change == "missing":
        headers.pop()
    elif change == "unknown":
        headers.append("secret")
    elif change == "duplicate":
        headers[1] = headers[0]
    else:
        headers[1], headers[2] = headers[2], headers[1]
    lines[0] = ",".join(headers)
    target.write_text("\n".join(lines) + "\n")
    rehash(assets, "plans")
    with pytest.raises(ReferenceTableError, match="CSV columns"):
        load_reference_catalog(assets)


def test_provider_mapping_requires_foreign_key(assets):
    target = assets / "reference" / "provider_datasets.csv"
    target.write_text(target.read_text().replace(",eodhd,", ",nonexistent,"))
    rehash(assets, "provider_datasets")
    with pytest.raises(ReferenceTableError, match="missing licence"):
        load_reference_catalog(assets)


def test_provider_dataset_pair_is_unique(assets):
    target = assets / "reference" / "provider_datasets.csv"
    row = target.read_text().splitlines()[1].replace("trading212_instrument", "duplicate_pair")
    target.write_text(target.read_text() + row + "\n")
    rehash(assets, "provider_datasets")
    with pytest.raises(ReferenceTableError, match="duplicate provider/dataset"):
        load_reference_catalog(assets)


def test_ethical_table_cannot_weaken_mandate(assets):
    target = assets / "reference" / "exclusions.csv"
    target.write_text("\n".join(target.read_text().splitlines()[:-1]) + "\n")
    rehash(assets, "exclusions")
    with pytest.raises(ReferenceTableError, match="cannot remove mandate exclusions"):
        load_reference_catalog(assets)


def test_all_external_sources_fail_closed_without_commercial_review():
    catalog = load_reference_catalog()
    for provider in ["trading212", "eodhd", "companies-house", "yfinance", "unlisted"]:
        for redistribution in (False, True):
            with pytest.raises(CommercialLicenceError, match="MISSING_LICENCE"):
                catalog.require_commercial_use(provider, redistribution=redistribution)


def test_approval_requires_reference_and_expiry(assets):
    replace_json(
        assets / "reference" / "provider_licences.json",
        lambda data: data[0].update(commercial_use="APPROVED"),
    )
    rehash(assets, "provider_licences")
    with pytest.raises(ReferenceTableError):
        load_reference_catalog(assets)


def test_approved_commercial_use_does_not_imply_redistribution_or_eternal_rights(assets):
    # A fabricated approval exists ONLY inside this temporary test directory.
    replace_json(
        assets / "reference" / "provider_licences.json",
        lambda data: data[0].update(
            commercial_use="APPROVED",
            review_reference="TEST ONLY fictional approval",
            review_expires_on="2026-12-31",
        ),
    )
    rehash(assets, "provider_licences")
    catalog = load_reference_catalog(assets)
    assert catalog.require_commercial_use("trading212", as_of=date(2026, 9, 16))
    with pytest.raises(CommercialLicenceError):
        catalog.require_commercial_use("trading212", redistribution=True, as_of=date(2026, 9, 16))
    with pytest.raises(CommercialLicenceError):
        catalog.require_commercial_use("trading212", as_of=date(2027, 1, 1))


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":1,"schema_version":1,"tables":[]}',
        b'{"schema_version":NaN,"tables":[]}',
        b"\xff",
        b"\xef\xbb\xbf{}",
        b'{"null":"\x00"}',
    ],
)
def test_ambiguous_json_encoding_and_non_finite_constants(assets, payload):
    (assets / "manifest.json").write_bytes(payload)
    with pytest.raises(ReferenceTableError):
        load_reference_catalog(assets)


def test_unsupported_format_fails_closed(assets):
    target = assets / "reference" / "plans.parquet"
    target.write_bytes((assets / "reference" / "plans.csv").read_bytes())
    replace_json(
        assets / "manifest.json",
        lambda data: data["tables"][0].update(path="reference/plans.parquet"),
    )
    with pytest.raises(ReferenceTableError, match="unsupported reference table format"):
        load_reference_catalog(assets)


def test_extra_json_fields_cannot_carry_runtime_customer_state(assets):
    replace_json(
        assets / "reference" / "provider_licences.json",
        lambda data: data[0].update(customer_email="forbidden@example.invalid"),
    )
    rehash(assets, "provider_licences")
    with pytest.raises(ReferenceTableError):
        load_reference_catalog(assets)


def test_release_compare_allows_unchanged_assets():
    compare_reference_releases(load_reference_catalog(), load_reference_catalog())


def test_content_changes_cannot_reuse_version(assets):
    target = assets / "reference" / "plans.csv"
    target.write_text(target.read_text().replace("PRO,30,1", "PRO,31,1"))
    rehash(assets, "plans")
    with pytest.raises(ReferenceTableError, match="strictly newer data version"):
        compare_reference_releases(load_reference_catalog(), load_reference_catalog(assets))


@pytest.mark.parametrize("newer_timestamp", [True, False])
def test_version_increment_also_requires_update_timestamp(assets, newer_timestamp):
    target = assets / "reference" / "plans.csv"
    target.write_text(target.read_text().replace("PRO,30,1", "PRO,31,1"))
    rehash(assets, "plans")
    replace_json(
        assets / "metadata" / "plans.json",
        lambda data: data.update(
            data_version="1.1.0",
            updated_at="2099-01-01T00:00:00Z" if newer_timestamp else data["updated_at"],
        ),
    )
    if newer_timestamp:
        compare_reference_releases(load_reference_catalog(), load_reference_catalog(assets))
    else:
        with pytest.raises(ReferenceTableError, match="later update timestamp"):
            compare_reference_releases(load_reference_catalog(), load_reference_catalog(assets))


def synthetic_snapshot():
    runtime = build_runtime("demo")
    instrument = runtime.eligibility.get_instrument_metadata("DEMO.L")
    return runtime.snapshot_builder(instrument)


def test_synthetic_exception_requires_explicit_permission():
    snapshot = synthetic_snapshot()
    catalog = load_reference_catalog()
    assert require_snapshot_commercial_rights(catalog, snapshot, allow_synthetic=True) == (
        "money-demo",
    )
    with pytest.raises(CommercialLicenceError):
        require_snapshot_commercial_rights(catalog, snapshot)


@pytest.mark.parametrize("change", ["mixed_provider", "ticker", "canonical_source", "empty"])
def test_demo_label_cannot_launder_live_sources(change):
    raw = synthetic_snapshot().model_dump(mode="json")
    raw["hash"] = ""
    if change == "mixed_provider":
        raw["evidence"][0]["provider"] = "eodhd"
        raw["evidence"][0]["hash"] = ""
    elif change == "ticker":
        raw["ticker"] = raw["instrument"]["ticker"] = "LIVE.L"
    elif change == "canonical_source":
        raw["evidence"][0]["canonical_source_id"] = "commercial:market-data"
        raw["evidence"][0]["hash"] = ""
    else:
        raw["evidence"] = []
    with pytest.raises(CommercialLicenceError):
        require_snapshot_commercial_rights(
            load_reference_catalog(),
            ResearchSnapshot.model_validate(raw),
            allow_synthetic=True,
        )


def approved_test_catalog(assets):
    # Approval is fictional and exists only inside an isolated test fixture.
    def approve(rows):
        for row in rows:
            if row["provider"] in {"eodhd", "companies-house"}:
                row.update(
                    commercial_use="APPROVED",
                    redistribution="APPROVED",
                    review_reference="TEST ONLY invented licence",
                    review_expires_on="2026-12-31",
                )

    replace_json(assets / "reference" / "provider_licences.json", approve)
    rehash(assets, "provider_licences")
    return load_reference_catalog(assets)


def test_customer_read_checks_current_not_original_research_date(assets):
    raw = synthetic_snapshot().model_dump(mode="json")
    raw["hash"] = ""
    raw["ticker"] = raw["instrument"]["ticker"] = "EXAMPLE.L"
    raw["instrument"]["provider"] = "eodhd"
    for evidence in raw["evidence"]:
        evidence["provider"] = "eodhd"
        evidence["hash"] = ""
    snapshot = ResearchSnapshot.model_validate(raw)
    catalog = approved_test_catalog(assets)
    assert require_snapshot_commercial_rights(catalog, snapshot, as_of=date(2026, 12, 31)) == (
        "eodhd",
    )
    with pytest.raises(CommercialLicenceError):
        require_snapshot_commercial_rights(catalog, snapshot, as_of=date(2027, 1, 1))
    # A later rights revocation (the actual unapproved release) also blocks old records.
    with pytest.raises(CommercialLicenceError):
        require_snapshot_commercial_rights(load_reference_catalog(), snapshot)


@pytest.mark.parametrize(
    "source", ["qualification", "metadata", "spread", "supplemental", "archive"]
)
@pytest.mark.parametrize("embedded", [True, False])
def test_live_manifest_checks_every_embedded_provider(assets, source, embedded):
    def provider(name):
        return SimpleNamespace(provider=name)

    instrument = SimpleNamespace(
        metadata=provider("eodhd"),
        spread_evidence=provider("eodhd"),
        supplemental_evidence=(),
        archived_market_evidence=(),
    )
    manifest = SimpleNamespace(
        provider_qualifications=(provider("eodhd"), provider("companies-house")),
        instruments=(instrument,) if embedded else (),
        reviewed_instruments=(instrument,),
    )
    catalog = approved_test_catalog(assets)
    assert require_manifest_commercial_rights(catalog, manifest, as_of=date(2026, 9, 16)) == (
        "companies-house",
        "eodhd",
    )
    if source == "qualification":
        manifest.provider_qualifications += (provider("unlisted"),)
    elif source == "metadata":
        instrument.metadata = provider("unlisted")
    elif source == "spread":
        instrument.spread_evidence = provider("unlisted")
    elif source == "supplemental":
        instrument.supplemental_evidence = (provider("unlisted"),)
    else:
        instrument.archived_market_evidence = (provider("unlisted"),)
    with pytest.raises(CommercialLicenceError):
        require_manifest_commercial_rights(catalog, manifest, as_of=date(2026, 9, 16))


def test_installed_wheel_requires_explicit_release_mount(assets, monkeypatch):
    monkeypatch.setattr(
        catalog_module, "__file__", "/venv/lib/python3.12/site-packages/money/reference/catalog.py"
    )
    monkeypatch.delenv("MONEY_REFERENCE_DATA_DIR", raising=False)
    with pytest.raises(ReferenceTableError, match="wheel installations require"):
        load_reference_catalog()
    monkeypatch.setenv("MONEY_REFERENCE_DATA_DIR", str(assets))
    assert load_reference_catalog().plan("PRO").research_jobs_per_period == 30


def test_relative_data_mount_is_rejected(assets, monkeypatch):
    monkeypatch.setenv("MONEY_REFERENCE_DATA_DIR", "data")
    with pytest.raises(ReferenceTableError, match="mount configuration"):
        load_reference_catalog()
    # Explicit safe caller-owned Path overrides environment resolution.
    assert load_reference_catalog(assets).plan("FREE").seats == 1
