"""Dependency metadata may change explicitly; pinned application bytes may not."""

from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from money.qualification.core import QualificationContext, json_bytes
from money.research import native_compatibility as compat

# Exact non-secret upstream metadata at the pinned revision; no checkout/network
# dependency in this unit suite and no test artifact used for live qualification.
ORIGINAL = b'''[tool.poetry]
name = "aihf"
version = "2.2.0"
description = "An AI-powered hedge fund: LLM investor agents and quant alpha models you can build, backtest, and run from your terminal"
authors = ["Virat Singh <virat.dot@gmail.com>"]
license = "MIT"
readme = "README.md"
homepage = "https://github.com/virattt/ai-hedge-fund"
repository = "https://github.com/virattt/ai-hedge-fund"
keywords = ["ai", "hedge-fund", "trading", "llm", "backtesting", "agents"]
packages = [
    { include = "hedge_fund", from = "." }
]
exclude = [
    "hedge_fund/**/test_*.py",
    "hedge_fund/conftest.py",
]

[tool.poetry.dependencies]
python = "^3.11"
langchain-anthropic = "0.3.5"
langchain-openai = "^0.3.5"
langchain-deepseek = "^0.1.2"
langchain-google-genai = "^2.0.11"
langchain-xai = "^0.2.5"
pandas = "^2.1.0"
numpy = "^1.24.0"
python-dotenv = "1.0.0"
matplotlib = "^3.9.2"
requests = "^2.32.0"
rich = "^14.2.0"
pydantic = "^2.4.2"
scipy = "^1.11.0"
pyyaml = "^6.0.3"
textual = "^8.2.8"

[tool.poetry.scripts]
aihf = "hedge_fund.run:main"

[tool.poetry.group.dev.dependencies]
pytest = "^7.4.0"
black = "^23.7.0"
isort = "^5.12.0"
flake8 = "^6.1.0"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"

[tool.black]
line-length = 420
target-version = ['py311']
include = '\\.pyi?$'

[tool.isort]
profile = "black"
force_alphabetical_sort_within_sections = true
'''


@pytest.fixture
def ctx(tmp_path: Path) -> QualificationContext:
    repo = tmp_path / "repo"
    actual_repo = Path(__file__).resolve().parents[2]
    config = repo / compat._DEFINITION
    config.parent.mkdir(parents=True)
    shutil.copyfile(actual_repo / compat._DEFINITION, config)
    shutil.copyfile(actual_repo / "UPSTREAM_LOCK.txt", repo / "UPSTREAM_LOCK.txt")
    return QualificationContext(tmp_path / "qualification", repo,
        {"OPENAI_API_KEY": "never-persist-this-fixture-credential"},
        datetime(2026, 9, 18, tzinfo=UTC))


@pytest.fixture
def staged(ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = ctx.root / "state/native/ai_hedge_fund/source"
    package = source / "hedge_fund"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# unit fixture only\n")
    (source / "pyproject.toml").write_bytes(ORIGINAL)
    (source / "LICENSE").write_text("Unit fixture only; not upstream qualification.\n")
    (source / "README.md").write_text("Unit fixture.\n")

    def check_source(root: Path, name: str) -> str:
        assert name == "hedge_fund"
        if (root / "__init__.py").read_text() == "# unit fixture only\n":
            return compat.SOURCE_DIGESTS[name]
        return hashlib.sha256(b"invalid fixture bytes").hexdigest()

    monkeypatch.setattr(compat, "source_fingerprint", check_source)
    return source


def definition(ctx: QualificationContext) -> dict[str, Any]:
    value = compat.load_compatibility(ctx, "ai_hedge_fund")
    assert value is not None
    return value


def test_only_aihf_has_metadata_compatibility(ctx: QualificationContext) -> None:
    assert compat.load_compatibility(ctx, "tradingagents") is None
    with pytest.raises(ValueError, match="ROLE_INVALID"):
        compat.load_compatibility(ctx, "crewai")
    selected = definition(ctx)
    assert selected["revision"] == "fc1bf250ead209ae5f02c39c3d0062c4bb554505"
    assert hashlib.sha256(ORIGINAL).hexdigest() == selected["original_sha256"]
    assert selected["definition_sha256"] == hashlib.sha256(
        (ctx.repo / compat._DEFINITION).read_bytes()).hexdigest()


@pytest.mark.parametrize("field,value", [
    ("revision", "a-different-revision"), ("repository", "https://not-approved.invalid/fork"),
    ("target", "hedge_fund/run.py"), ("section", "build-system"), ("changes", []),
    ("source_sha256", "unverified"), ("advisories", []),
])
def test_definition_cannot_expand_patch_scope(
    ctx: QualificationContext, field: str, value: Any,
) -> None:
    path = ctx.repo / compat._DEFINITION
    current = json.loads(path.read_bytes())
    current[field] = value
    path.write_bytes(json_bytes(current))
    with pytest.raises(ValueError, match="DEFINITION_INVALID"):
        definition(ctx)


def test_changed_upstream_lock_rejected(ctx: QualificationContext) -> None:
    (ctx.repo / "UPSTREAM_LOCK.txt").write_text("ai-hedge-fund https://wrong.invalid other\n")
    with pytest.raises(ValueError, match="SOURCE_PIN_MISMATCH"):
        definition(ctx)


def test_only_five_dependency_lines_change(ctx: QualificationContext) -> None:
    selected = definition(ctx)
    patched = compat._patch_metadata(ORIGINAL, selected)
    before, after = tomllib.loads(ORIGINAL.decode()), tomllib.loads(patched.decode())
    original_dependencies = before["tool"]["poetry"]["dependencies"]
    patched_dependencies = after["tool"]["poetry"]["dependencies"]
    changed = {name for name in original_dependencies
               if original_dependencies[name] != patched_dependencies[name]}
    assert changed == {item["name"] for item in selected["changes"]}
    assert len(changed) == 5
    assert original_dependencies["langchain-google-genai"] == patched_dependencies["langchain-google-genai"]
    after["tool"]["poetry"]["dependencies"] = original_dependencies
    assert after == before
    assert sum(a != b for a, b in zip(ORIGINAL.splitlines(), patched.splitlines(), strict=True)) == 5


def test_metadata_original_hash_is_required(ctx: QualificationContext) -> None:
    with pytest.raises(ValueError, match="ORIGINAL_METADATA_CHANGED"):
        compat._patch_metadata(ORIGINAL + b"# unrelated change\n", definition(ctx))


def test_definition_changes_after_loading_are_rejected(
    ctx: QualificationContext, staged: Path,
) -> None:
    selected = definition(ctx)
    selected["changes"][0]["replacement"] = ">=0"
    with pytest.raises(ValueError, match="DEFINITION_CHANGED"):
        compat.prepare_compatibility_source(ctx, staged, staged.parent, selected)


def test_preparation_preserves_original_and_records_real_hashes(
    ctx: QualificationContext, staged: Path,
) -> None:
    selected = definition(ctx)
    before = compat._tree_manifest(staged)
    target, receipt = compat.prepare_compatibility_source(ctx, staged, staged.parent, selected)
    assert staged != target
    assert compat._tree_manifest(staged) == before
    assert (staged / "pyproject.toml").read_bytes() == ORIGINAL
    assert (target / "hedge_fund/__init__.py").read_bytes() == (staged / "hedge_fund/__init__.py").read_bytes()
    assert receipt["application_source_unchanged"] is True
    assert receipt["security_qualified"] is False
    assert receipt["scope"] == "DEPENDENCY_METADATA_ONLY"
    assert receipt["original_pyproject_sha256"] == hashlib.sha256(ORIGINAL).hexdigest()
    assert receipt["patched_pyproject_sha256"] == hashlib.sha256((target / "pyproject.toml").read_bytes()).hexdigest()
    assert receipt["definition_sha256"] == selected["definition_sha256"]
    assert len(receipt["artifacts"]) == 4
    for artifact in receipt["artifacts"]:
        ctx.verify_artifact(*artifact)
    assert b"--- upstream/pyproject.toml" in ctx.verify_artifact(*receipt["artifacts"][-1])
    assert compat.prepare_compatibility_source(ctx, staged, staged.parent, selected) == (target, receipt)


@pytest.mark.parametrize("relative", ["setup.py", "hedge_fund/injected.py", "extra.dist-info/METADATA"])
def test_staged_copy_rejects_any_added_build_or_package_file(
    ctx: QualificationContext, staged: Path, relative: str,
) -> None:
    selected = definition(ctx)
    target, _ = compat.prepare_compatibility_source(ctx, staged, staged.parent, selected)
    extra = target / relative
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("# unapproved added bytes\n")
    with pytest.raises(ValueError, match="STAGED_SOURCE_CHANGED"):
        compat.prepare_compatibility_source(ctx, staged, staged.parent, selected)


@pytest.mark.parametrize("relative", ["pyproject.toml", "hedge_fund/__init__.py", "LICENSE"])
def test_staged_copy_rejects_any_changed_file(
    ctx: QualificationContext, staged: Path, relative: str,
) -> None:
    selected = definition(ctx)
    target, _ = compat.prepare_compatibility_source(ctx, staged, staged.parent, selected)
    (target / relative).write_text("altered\n")
    with pytest.raises(ValueError, match="STAGED_SOURCE_CHANGED"):
        compat.prepare_compatibility_source(ctx, staged, staged.parent, selected)


def test_application_source_change_is_not_a_metadata_patch(
    ctx: QualificationContext, staged: Path,
) -> None:
    (staged / "hedge_fund/__init__.py").write_text("changed application semantics\n")
    with pytest.raises(ValueError, match="APPLICATION_SOURCE_CHANGED"):
        compat.prepare_compatibility_source(ctx, staged, staged.parent, definition(ctx))


@pytest.mark.parametrize("in_copy", [False, True])
def test_source_and_copy_symlinks_rejected(
    ctx: QualificationContext, staged: Path, in_copy: bool,
) -> None:
    selected = definition(ctx)
    target = staged
    if in_copy:
        target, _ = compat.prepare_compatibility_source(ctx, staged, staged.parent, selected)
    (target / "unsafe").symlink_to(staged / "LICENSE")
    with pytest.raises(ValueError, match="PATH_UNSAFE"):
        compat.prepare_compatibility_source(ctx, staged, staged.parent, selected)


def test_file_mode_changes_rejected(ctx: QualificationContext, staged: Path) -> None:
    selected = definition(ctx)
    target, _ = compat.prepare_compatibility_source(ctx, staged, staged.parent, selected)
    (target / "README.md").chmod(0o755)
    with pytest.raises(ValueError, match="STAGED_SOURCE_CHANGED"):
        compat.prepare_compatibility_source(ctx, staged, staged.parent, selected)


def test_normal_read_access_time_change_does_not_invalidate_source(
    staged: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_lstat = Path.lstat
    observed = 0
    keys = ("st_ino", "st_dev", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")

    def changing_atime(path: Path) -> Any:
        nonlocal observed
        result = original_lstat(path)
        if path == staged / "README.md":
            observed += 1
            return SimpleNamespace(**{key: getattr(result, key) for key in keys},
                                   st_atime_ns=result.st_atime_ns + observed)
        return result

    monkeypatch.setattr(Path, "lstat", changing_atime)
    manifest = compat._tree_manifest(staged)
    assert observed >= 2
    assert manifest["README.md"][1] == hashlib.sha256(b"Unit fixture.\n").hexdigest()


def test_secret_in_definition_never_persisted(ctx: QualificationContext) -> None:
    path = ctx.repo / compat._DEFINITION
    current = json.loads(path.read_bytes())
    current["reason"] = ctx.environ["OPENAI_API_KEY"]
    path.write_bytes(json_bytes(current))
    with pytest.raises(ValueError, match="QUALIFICATION_SECRET_DETECTED"):
        definition(ctx)
    assert not (ctx.root / "artifacts").exists()
