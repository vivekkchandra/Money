"""Synthetic local bytes test admission mechanics, never production qualification."""

import hashlib
import json
import os

import pytest
from test_filing_documents import reviewed_manifest

from money.research.live import LiveManifest, load_manifest, read_qualification_bytes


def catalog_bundle(tmp_path, *, missing_proof=False, duplicate=False, financial=True):
    """Synthetic admission fixture; never written under data/qualified."""
    data = reviewed_manifest().model_dump(mode="json")
    proof = b"synthetic test proof, not production evidence"
    digest = hashlib.sha256(proof).hexdigest()
    (tmp_path / "proof").write_bytes(proof)
    proof_keys = {
        "eligibility_proof_hash", "ethical_proof_hash", "corporate_action_coverage_hash",
        "evidence_hash", "review_evidence_hash", "qualification_report_hash",
        "native_egress_verification_hash", "historical_eligibility_hash",
        "survivorship_audit_hash", "corporate_action_audit_hash",
    }

    def replace_proofs(value):
        if isinstance(value, dict):
            return {key: digest if key in proof_keys else replace_proofs(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [replace_proofs(item) for item in value]
        return value

    data = replace_proofs(data)
    items = data["instruments"]
    if missing_proof:
        items[0]["ethical_proof_hash"] = hashlib.sha256(b"unlisted fixture proof").hexdigest()
    catalog = json.dumps(items).encode()
    (tmp_path / "catalog.json").write_bytes(catalog)
    reference = (hashlib.sha256(catalog).hexdigest(), "catalog.json")
    data["instrument_catalog"] = reference
    data["qualification_artifacts"] = [(digest, "proof"), reference]
    data["instruments"] = items if duplicate else []
    if not financial:
        data["provider_qualifications"][0]["datasets"] = ["filing"]
    content = json.dumps(data).encode()
    path = tmp_path / "manifest.json"
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest()


def test_external_catalog_is_admitted_without_embedded_seed_instruments(tmp_path):
    path, digest = catalog_bundle(tmp_path)
    manifest = load_manifest(path, digest)
    assert manifest.instruments == ()
    assert len(manifest.reviewed_instruments) == 1
    assert manifest.reviewed_instruments[0].metadata.ticker == "VOD.L"
    from money.data.instruments import InstrumentCatalogue

    assert len(InstrumentCatalogue.from_manifest(manifest).entries) == 1


@pytest.mark.parametrize("option,code", [
    ("missing_proof", "QUALIFICATION_ARTIFACT_MISSING"),
    ("duplicate", "LIVE_MANIFEST_DUPLICATE_IDENTITY"),
])
def test_external_catalog_keeps_proof_and_identity_gates(tmp_path, option, code):
    path, digest = catalog_bundle(tmp_path, **{option: True})
    with pytest.raises(ValueError, match=code):
        load_manifest(path, digest)


def test_external_catalog_cannot_omit_financial_qualification(tmp_path):
    path, digest = catalog_bundle(tmp_path, financial=False)
    with pytest.raises(ValueError, match="LIVE_FILING_DOCUMENT_COVERAGE_MISSING"):
        load_manifest(path, digest)


def test_external_catalog_mutation_and_unresolved_access_fail_closed(tmp_path):
    path, digest = catalog_bundle(tmp_path)
    manifest = LiveManifest.model_validate_json(path.read_bytes())
    with pytest.raises(ValueError, match="LIVE_INSTRUMENT_CATALOG_NOT_LOADED"):
        _ = manifest.reviewed_instruments
    (tmp_path / "catalog.json").write_bytes(b"[]")
    with pytest.raises(ValueError, match="QUALIFICATION_ARTIFACT_HASH_MISMATCH"):
        load_manifest(path, digest)


@pytest.mark.parametrize("path", ["", ".", "..", "../proof", "/proof", "a/../proof", "a//proof", "./proof", "a\\proof"])
def test_bundle_paths_are_strictly_relative_and_canonical(tmp_path, path):
    with pytest.raises(ValueError, match="QUALIFICATION_ARTIFACT_INVALID"):
        read_qualification_bytes(tmp_path, path, 100)


def test_nested_artifact_reads_real_bytes_with_exact_bound(tmp_path):
    directory = tmp_path / "artifacts"
    directory.mkdir()
    (directory / "proof").write_bytes(b"synthetic fixture bytes")
    assert read_qualification_bytes(tmp_path, "artifacts/proof", 23) == b"synthetic fixture bytes"
    with pytest.raises(ValueError, match="QUALIFICATION_ARTIFACT_INVALID"):
        read_qualification_bytes(tmp_path, "artifacts/proof", 5)


@pytest.mark.parametrize("external", [True, False])
def test_parent_symlink_is_rejected_even_when_target_is_inside_bundle(tmp_path, external):
    root = tmp_path / "bundle"
    root.mkdir()
    directory = (tmp_path if external else root) / "real"
    directory.mkdir()
    (directory / "proof").write_bytes(b"fixture")
    (root / "artifacts").symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError, match="QUALIFICATION_ARTIFACT_INVALID"):
        read_qualification_bytes(root, "artifacts/proof", 100)


def test_file_symlink_special_file_directory_and_absent_file_rejected(tmp_path):
    (tmp_path / "real").write_bytes(b"fixture")
    (tmp_path / "link").symlink_to(tmp_path / "real")
    (tmp_path / "directory").mkdir()
    os.mkfifo(tmp_path / "fifo")
    for path in ("link", "directory", "missing", "fifo"):
        with pytest.raises(ValueError, match="QUALIFICATION_ARTIFACT_INVALID"):
            read_qualification_bytes(tmp_path, path, 100)


def test_manifest_symlink_and_nonregular_manifest_rejected(tmp_path):
    (tmp_path / "real").write_bytes(b"fixture")
    (tmp_path / "manifest.json").symlink_to(tmp_path / "real")
    with pytest.raises(ValueError, match="LIVE_MANIFEST_INVALID"):
        load_manifest(tmp_path / "manifest.json", "a" * 64)
    os.mkfifo(tmp_path / "pipe.json")
    with pytest.raises(ValueError, match="LIVE_MANIFEST_INVALID"):
        load_manifest(tmp_path / "pipe.json", "a" * 64)


def test_manifest_parent_symlink_loop_is_safe_validation_failure(tmp_path):
    (tmp_path / "first").symlink_to(tmp_path / "second", target_is_directory=True)
    (tmp_path / "second").symlink_to(tmp_path / "first", target_is_directory=True)
    with pytest.raises(ValueError, match="^LIVE_MANIFEST_INVALID$"):
        load_manifest(tmp_path / "first" / "manifest.json", "a" * 64)


@pytest.mark.parametrize("provider,datasets", [
    ("eodhd", ()), ("eodhd", ("ohlcv",)), ("eodhd", ("ohlcv", "news")),
    ("eodhd", ("ohlcv", "corporate_action")), ("companies-house", ("financial",)),
])
def test_manifest_cannot_omit_datasets_used_by_live_builder(provider, datasets):
    data = reviewed_manifest().model_dump(mode="json")
    for record in data["provider_qualifications"]:
        if record["provider"] == provider:
            record["datasets"] = datasets
    with pytest.raises(ValueError, match="LIVE_MANIFEST_REQUIRED_DATASET_MISSING"):
        LiveManifest.model_validate(data)


def test_reader_releases_all_descriptors_on_error_and_success(tmp_path, monkeypatch):
    import money.research.live as module

    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "proof").write_bytes(b"fixture")
    opened = []
    original = module.os.open

    def capture(*args, **kwargs):
        descriptor = original(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(module.os, "open", capture)
    assert read_qualification_bytes(tmp_path, "nested/proof", 100) == b"fixture"
    with pytest.raises(ValueError):
        read_qualification_bytes(tmp_path, "nested/proof", 1)
    for descriptor in set(opened):
        with pytest.raises(OSError):
            os.fstat(descriptor)
