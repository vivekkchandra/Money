"""Standalone source pinning does not scan or import the site-packages tree."""

import hashlib
from datetime import UTC, datetime
from importlib.util import spec_from_file_location
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from money.adapters import native_attestation as attestation
from money.adapters.upstream import UpstreamUnavailable
from money.data.uk import xbrl


def archive():
    buffer = BytesIO()
    with ZipFile(buffer, "w") as bundle:
        bundle.writestr("accounts.xhtml", b"<html/>")
    return buffer.getvalue()


def convert():
    now = datetime.now(UTC)
    return xbrl.parse_company_archive(
        archive(),
        company_number="01833679",
        source_id="synthetic",
        snapshot_id="synthetic",
        publication_time=now,
        retrieved_at=now,
        verified_currency="GBP",
    )


@pytest.mark.parametrize(
    "state",
    [
        "absent",
        "mismatch",
        "symlink",
        "parent_symlink",
        "oversized",
        "package",
        "missing_origin",
        "non_source_loader",
    ],
)
def test_unqualified_single_module_is_rejected_before_import(tmp_path, monkeypatch, state):
    path = tmp_path.resolve() / "stream_read_xbrl.py"
    path.write_text("raise AssertionError('never execute unqualified code')\n")
    if state == "symlink":
        target = tmp_path / "target.py"
        path.rename(target)
        path.symlink_to(target)
    elif state == "parent_symlink":
        target = tmp_path / "actual"
        target.mkdir()
        path.rename(target / path.name)
        link = tmp_path / "linked"
        link.symlink_to(target, target_is_directory=True)
        path = link / path.name
    elif state == "oversized":
        path.write_bytes(b"x" * 5_000_001)
    spec = spec_from_file_location("stream_read_xbrl", path)
    if state == "absent":
        spec = None
    elif state == "package":
        spec.submodule_search_locations = []
    elif state == "missing_origin":
        spec.origin = None
    elif state == "non_source_loader":
        spec.loader = object()
    monkeypatch.setattr(attestation, "find_spec", lambda name: spec)
    monkeypatch.setattr(xbrl, "import_module", lambda *args: pytest.fail("unqualified import"))
    monkeypatch.setattr(Path, "rglob", lambda *args: pytest.fail("site-packages traversal"))
    with pytest.raises(UpstreamUnavailable):
        convert()


def test_pinned_upstream_single_file_is_accepted_without_import_or_recursive_scan(monkeypatch):
    path = Path("upstreams/stream-read-xbrl/stream_read_xbrl.py").resolve()
    if not path.is_file():
        pytest.skip("Read-only pinned upstream checkout is not present in ordinary CI")
    expected = attestation.SINGLE_MODULE_SOURCE_DIGESTS["stream_read_xbrl"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    monkeypatch.setattr(attestation, "find_spec", lambda name: spec_from_file_location(name, path))
    monkeypatch.setattr(Path, "rglob", lambda *args: pytest.fail("site-packages traversal"))
    assert attestation.require_pinned_module("stream_read_xbrl") == expected


def test_unknown_module_is_rejected_before_module_resolution(monkeypatch):
    monkeypatch.setattr(
        attestation, "find_spec", lambda *args: pytest.fail("unapproved module lookup")
    )
    with pytest.raises(UpstreamUnavailable, match="not allowlisted"):
        attestation.require_pinned_module("another.module")


def test_standalone_success_path_uses_only_declared_source_file(tmp_path, monkeypatch):
    # Deterministic admission mechanics fixture; not a new production source pin.
    path = tmp_path.resolve() / "stream_read_xbrl.py"
    source = b"fixture_only = True\n"
    path.write_bytes(source)
    expected = hashlib.sha256(source).hexdigest()
    monkeypatch.setitem(attestation.SINGLE_MODULE_SOURCE_DIGESTS, "stream_read_xbrl", expected)
    monkeypatch.setattr(attestation, "find_spec", lambda name: spec_from_file_location(name, path))
    monkeypatch.setattr(Path, "rglob", lambda *args: pytest.fail("site-packages traversal"))
    assert attestation.require_pinned_module("stream_read_xbrl") == expected


def test_default_converter_attests_before_loading_native_module(monkeypatch):
    calls = []
    monkeypatch.setattr(xbrl, "require_pinned_module", lambda name: calls.append(("attest", name)))

    def imported(name):
        calls.append(("import", name))
        assert calls[0] == ("attest", name)
        return SimpleNamespace(_COLUMNS=(), _xbrl_to_rows=lambda item: ())

    monkeypatch.setattr(xbrl, "import_module", imported)
    assert convert() == ()
    assert calls == [("attest", "stream_read_xbrl"), ("import", "stream_read_xbrl")]


def test_existing_package_pin_map_is_unchanged():
    assert attestation.SOURCE_DIGESTS == {
        "tradingagents": "0eec12d7ccff614c8124522ee5c9df2a5ec03187a693607bcd15221636d599f6",
        "hedge_fund": "4323005f036350ed3bb3b959a10215e4d5c7ecbc26d179751c15d8c4507a77e6",
        "qlib": "11c4744c882a42171df754d70a6cc9d3b17c7b7973aa277ae5358e2a90cd152b",
        "crewai": "a94c3acee11475019649c8be91f2caad28ab8d41023b9b993d5481a7a6c301cb",
    }
