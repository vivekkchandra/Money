"""Full universe catalogues use synthetic temporary bytes, never live evidence."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from test_filing_documents import NOW
from test_qualification_bundle import catalog_bundle

from money.data.live_eligibility import EligibilityReview
from money.qualification import runner
from money.qualification.core import QualificationContext
from money.qualification.universe import credential_binding
from money.qualification.universe_catalog import eligibility_catalogs
from money.research import live


def synthetic_isin(index: int) -> str:
    stem = f"GB{index:09d}"
    for digit in "0123456789":
        value = stem + digit
        numbers = "".join(str(ord(c) - 55) if c.isalpha() else c for c in value)
        total = sum(
            (int(c) * (2 if i % 2 else 1)) // 10 + (int(c) * (2 if i % 2 else 1)) % 10
            for i, c in enumerate(reversed(numbers))
        )
        if total % 10 == 0:
            return value
    raise AssertionError("No ISIN checksum")


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[QualificationContext, Path, live.LiveManifest]:
    monkeypatch.setattr(live, "utc_now", lambda: NOW)
    path, digest = catalog_bundle(tmp_path)
    manifest = live.load_manifest(path, digest)
    return QualificationContext(tmp_path, tmp_path, {
        "TRADING212_API_KEY": "synthetic-catalogue-fixture-key",
        "TRADING212_API_SECRET": "synthetic-catalogue-fixture-secret",
    }, NOW), path, manifest


def additional(original: EligibilityReview, index: int = 1) -> EligibilityReview:
    ticker = f"SYNTHETIC{index}"
    metadata = original.metadata.model_copy(update={"ticker": ticker})
    identifiers = original.identifiers.model_copy(update={
        "ticker": ticker, "trading212_id": f"SYNTHETIC{index}l_EQ",
        "exchange_ticker": ticker, "isin": synthetic_isin(index),
    })
    return EligibilityReview(metadata=metadata, identifiers=identifiers,
        eligibility_proof_hash=original.eligibility_proof_hash,
        ethical_proof_hash=original.ethical_proof_hash)


def write_manifest(ctx: QualificationContext, data: dict[str, Any]) -> tuple[Path, str]:
    ctx.write_json("manifest.json", data)
    path = ctx.root / "manifest.json"
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def attach(ctx: QualificationContext, path: Path, reviews: tuple[EligibilityReview, ...]) -> tuple[Path, str]:
    data = json.loads(path.read_bytes())
    refs = eligibility_catalogs(ctx, reviews)
    data["eligibility_catalogs"] = refs
    data["qualification_artifacts"].extend(refs)
    attach_provenance(ctx, data)
    return write_manifest(ctx, data)


def attach_provenance(ctx: QualificationContext, data: dict[str, Any], **updates: Any) -> None:
    account_proof = ctx.artifact(b"Synthetic independently reviewed ISA account provenance")
    binding = credential_binding(ctx.environ)
    provenance = ctx.artifact({
        "credential_binding_sha256": binding, "retrieved_at": NOW.isoformat(),
        "account_context": "STOCKS_AND_SHARES_ISA", "retrieval_environment": "live",
        "account_review_hash": account_proof[0], **updates,
    })
    data["universe_account_binding_sha256"] = binding
    data["universe_provenance"] = provenance
    data["qualification_artifacts"].extend([account_proof, provenance])


def test_legacy_manifest_keeps_eligibility_property(bundle: tuple[QualificationContext, Path, live.LiveManifest]) -> None:
    _, _, manifest = bundle
    assert len(manifest.eligibility_reviews) == len(manifest.reviewed_instruments) == 1
    assert manifest.eligibility_reviews[0].metadata == manifest.reviewed_instruments[0].metadata


def test_full_catalog_retains_members_without_supplemental_inputs(bundle: tuple[QualificationContext, Path, live.LiveManifest]) -> None:
    ctx, path, manifest = bundle
    first = manifest.eligibility_reviews[0]
    path, digest = attach(ctx, path, (first, additional(first)))
    loaded = live.load_manifest(path, digest)
    assert len(loaded.reviewed_instruments) == 1
    assert len(loaded.eligibility_reviews) == 2
    assert {item.metadata.ticker for item in loaded.eligibility_reviews} == {"VOD.L", "SYNTHETIC1"}


def test_large_qualified_universe_is_chunked_and_fully_loaded(bundle: tuple[QualificationContext, Path, live.LiveManifest]) -> None:
    ctx, path, manifest = bundle
    first = manifest.eligibility_reviews[0]
    reviews = (first, *(additional(first, index) for index in range(1, 1002)))
    path, digest = attach(ctx, path, reviews)
    loaded = live.load_manifest(path, digest)
    assert len(loaded.eligibility_catalogs) == 4
    assert len(loaded.eligibility_reviews) == 1002
    for reference in loaded.eligibility_catalogs:
        raw = ctx.verify_artifact(*reference)
        assert len(raw) < 2_000_000
        assert 1 <= len(json.loads(raw)) <= 256


def test_eligibility_access_requires_hash_loader(bundle: tuple[QualificationContext, Path, live.LiveManifest]) -> None:
    ctx, path, manifest = bundle
    path, _ = attach(ctx, path, manifest.eligibility_reviews)
    unloaded = live.LiveManifest.model_validate_json(path.read_bytes())
    with pytest.raises(ValueError, match="LIVE_ELIGIBILITY_CATALOG_NOT_LOADED"):
        _ = unloaded.eligibility_reviews


def test_changed_chunk_and_uninventoried_chunk_fail_closed(bundle: tuple[QualificationContext, Path, live.LiveManifest]) -> None:
    ctx, path, manifest = bundle
    path, digest = attach(ctx, path, manifest.eligibility_reviews)
    data = json.loads(path.read_bytes())
    reference = data["eligibility_catalogs"][0]
    original = ctx.read_bytes(reference[1])
    ctx.write_bytes(reference[1], original + b" ")
    with pytest.raises(ValueError, match="QUALIFICATION_ARTIFACT_HASH_MISMATCH"):
        live.load_manifest(path, digest)
    ctx.write_bytes(reference[1], original)
    data["qualification_artifacts"] = [item for item in data["qualification_artifacts"] if item != reference]
    path, digest = write_manifest(ctx, data)
    with pytest.raises(ValueError, match="LIVE_ELIGIBILITY_CATALOG_NOT_LOADED"):
        live.load_manifest(path, digest)


@pytest.mark.parametrize("change", ["missing", "override", "duplicate", "stale", "missing-proof"])
def test_full_catalog_cannot_override_omit_or_requalify_reviews(bundle: tuple[QualificationContext, Path, live.LiveManifest], change: str) -> None:
    ctx, path, manifest = bundle
    first = manifest.eligibility_reviews[0]
    values = [first.model_dump(mode="json"), additional(first).model_dump(mode="json")]
    expected = "LIVE_ELIGIBILITY_CATALOG_INSTRUMENT_MISMATCH"
    if change == "missing":
        values = values[1:]
    elif change == "override":
        values[0]["metadata"]["company"] = "Changed synthetic company"
    elif change == "duplicate":
        values[1]["identifiers"]["trading212_id"] = values[0]["identifiers"]["trading212_id"]
        expected = "LIVE_ELIGIBILITY_CATALOG_DUPLICATE_IDENTITY"
    elif change == "stale":
        values[1]["identifiers"]["valid_until"] = NOW.isoformat()
        expected = "IDENTIFIER_MAPPING_STALE"
    else:
        values[1]["ethical_proof_hash"] = hashlib.sha256(b"unlisted synthetic proof bytes").hexdigest()
        expected = "QUALIFICATION_ARTIFACT_MISSING"
    # Bypass assembly validation only to exercise the independent hash loader.
    reference = ctx.artifact(values)
    data = json.loads(path.read_bytes())
    data["eligibility_catalogs"] = [reference]
    data["qualification_artifacts"].append(reference)
    attach_provenance(ctx, data)
    path, digest = write_manifest(ctx, data)
    with pytest.raises(ValueError, match=expected):
        live.load_manifest(path, digest)


def test_stale_metadata_rejected_even_with_current_identifiers(bundle: tuple[QualificationContext, Path, live.LiveManifest]) -> None:
    ctx, path, manifest = bundle
    value = manifest.eligibility_reviews[0].model_dump(mode="json")
    value["metadata"]["verified_at"] = (NOW - timedelta(days=2)).isoformat()
    reference = ctx.artifact([value])
    data = json.loads(path.read_bytes())
    data["eligibility_catalogs"] = [reference]
    data["qualification_artifacts"].append(reference)
    attach_provenance(ctx, data)
    path, digest = write_manifest(ctx, data)
    with pytest.raises(ValueError, match="LIVE_ELIGIBILITY_CATALOG_NOT_CURRENT"):
        live.load_manifest(path, digest)


def test_assembly_pins_full_catalog_before_independent_release_review(bundle: tuple[QualificationContext, Path, live.LiveManifest]) -> None:
    ctx, _, manifest = bundle
    first = manifest.eligibility_reviews[0]
    data: dict[str, Any] = {"qualification_artifacts": []}
    attach_provenance(ctx, data)
    providers = {
        "instruments": [item.model_dump(mode="json") for item in manifest.reviewed_instruments],
        "qualified_universe": [first.model_dump(mode="json"), additional(first).model_dump(mode="json")],
        "universe_account_binding_sha256": data["universe_account_binding_sha256"],
        "universe_provenance": data["universe_provenance"],
    }
    assert runner.assemble_manifest(ctx, [providers]) is None
    release = ctx.read_json("outputs/release-inputs.json")
    references = release["manifest_fields"]["eligibility_catalogs"]
    assert sum(len(json.loads(ctx.verify_artifact(*reference))) for reference in references) == 2
    assert release["manifest_fields"]["universe_account_binding_sha256"] == data["universe_account_binding_sha256"]
    assert release["manifest_fields"]["universe_provenance"] == list(data["universe_provenance"])
    assert any(item["code"] == "RELEASE_APPROVAL_REQUIRED" for item in ctx.blockers)


def test_empty_or_duplicate_bulk_universe_cannot_fall_back_to_seed(bundle: tuple[QualificationContext, Path, live.LiveManifest]) -> None:
    ctx, _, manifest = bundle
    with pytest.raises(ValueError, match="CATALOG_SIZE_INVALID"):
        eligibility_catalogs(ctx, [])
    first = manifest.eligibility_reviews[0]
    with pytest.raises(ValueError, match="CATALOG_DUPLICATE"):
        eligibility_catalogs(ctx, [first, first])


@pytest.mark.parametrize("case", ["valid", "unlisted-proof", "uk", "company-number"])
def test_foreign_issuer_exception_requires_real_proof_and_exclusive_identity(bundle: tuple[QualificationContext, Path, live.LiveManifest], case: str) -> None:
    ctx, path, manifest = bundle
    instrument = manifest.reviewed_instruments[0].model_dump(mode="json")
    instrument["filing_documents"] = []
    instrument["identifiers"]["companies_house_number"] = "01833679" if case == "company-number" else None
    instrument["issuer_jurisdiction"] = "GB" if case == "uk" else "US"
    proof = ctx.artifact(b"Synthetic reviewed issuer jurisdiction evidence")
    instrument["issuer_jurisdiction_proof_hash"] = proof[0]
    catalog = ctx.artifact([instrument])
    data = json.loads(path.read_bytes())
    data["instrument_catalog"] = catalog
    data["qualification_artifacts"].append(catalog)
    if case != "unlisted-proof":
        data["qualification_artifacts"].append(proof)
    path, digest = write_manifest(ctx, data)
    if case == "valid":
        assert live.load_manifest(path, digest).reviewed_instruments[0].issuer_jurisdiction == "US"
    else:
        code = "QUALIFICATION_ARTIFACT_MISSING" if case == "unlisted-proof" else "LIVE_FOREIGN_ISSUER_JURISDICTION_INVALID"
        with pytest.raises(ValueError, match=code):
            live.load_manifest(path, digest)


@pytest.mark.parametrize("case", ["missing-binding", "missing-reference", "unlisted-reference", "other-account", "invest", "demo", "expired", "future", "unlisted-account-review"])
def test_bulk_catalog_requires_current_hash_bound_isa_account_provenance(bundle: tuple[QualificationContext, Path, live.LiveManifest], case: str) -> None:
    ctx, path, manifest = bundle
    path, _ = attach(ctx, path, manifest.eligibility_reviews)
    data = json.loads(path.read_bytes())
    expected = "LIVE_UNIVERSE_ACCOUNT_PROVENANCE_INVALID"
    if case == "missing-binding":
        data["universe_account_binding_sha256"] = None
        expected = "LIVE_UNIVERSE_ACCOUNT_PROVENANCE_REQUIRED"
    elif case == "missing-reference":
        data["universe_provenance"] = None
        expected = "LIVE_UNIVERSE_ACCOUNT_PROVENANCE_REQUIRED"
    elif case == "unlisted-reference":
        data["qualification_artifacts"] = [reference for reference in data["qualification_artifacts"]
            if reference != data["universe_provenance"]]
        expected = "LIVE_UNIVERSE_ACCOUNT_PROVENANCE_NOT_LOADED"
    elif case == "other-account":
        data["universe_account_binding_sha256"] = credential_binding({
            "TRADING212_API_KEY": "different-synthetic-account",
            "TRADING212_API_SECRET": "different-synthetic-secret",
        })
    else:
        changes: dict[str, Any] = {}
        if case == "invest":
            changes["account_context"] = "INVEST"
        elif case == "demo":
            changes["retrieval_environment"] = "demo"
        elif case == "expired":
            changes["retrieved_at"] = (NOW - timedelta(hours=24)).isoformat()
        elif case == "future":
            changes["retrieved_at"] = (NOW + timedelta(seconds=1)).isoformat()
        else:
            changes["account_review_hash"] = hashlib.sha256(b"unlisted synthetic account review").hexdigest()
        attach_provenance(ctx, data, **changes)
    path, digest = write_manifest(ctx, data)
    with pytest.raises(ValueError, match=expected):
        live.load_manifest(path, digest)
