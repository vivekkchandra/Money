"""Offline deployment contracts; these do not claim Railway acceptance."""

import ast
import importlib.util
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from money.storage import models as db

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect()])
def test_rnd_migration_is_frozen_and_matches_queue_column(dialect, monkeypatch):
    path = ROOT / "migrations/versions/0008_personal_rnd.py"
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("money")
    spec = importlib.util.spec_from_file_location("rnd_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    columns, indexes = [], []
    monkeypatch.setattr(migration, "op", SimpleNamespace(
        add_column=lambda table, column: columns.append((table, column)),
        create_index=lambda *args: indexes.append(args),
    ))
    migration.upgrade()
    assert (migration.revision, migration.down_revision) == ("0008", "0007")
    assert len(columns) == 1 and columns[0][0] == "research_jobs"
    column = columns[0][1]
    assert column.type.compile(dialect) == db.jobs.c.research_kind.type.compile(dialect)
    assert not column.nullable
    assert str(column.server_default.arg) == "standard"
    assert indexes == [("ix_jobs_kind_queue", "research_jobs", [
        "research_kind", "status", "created_at",
    ])]
    with pytest.raises(RuntimeError, match="forward migration"):
        migration.downgrade()


def test_railway_selects_research_image_only_for_private_worker():
    api, worker = [tomllib.loads((ROOT / f"deploy/railway/{service}.toml").read_text())
                   for service in ("api", "worker")]
    assert api["build"] == {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile"}
    assert worker["build"] == {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile.research"}
    assert api["deploy"]["preDeployCommand"] == ["alembic upgrade head"]
    assert "exec uvicorn money.api.app:app" in api["deploy"]["startCommand"]
    assert "${PORT:-8000}" in api["deploy"]["startCommand"]
    assert api["deploy"]["healthcheckPath"] == "/health/ready"
    assert worker["deploy"]["startCommand"] == "python -m money.worker"
    assert "healthcheckPath" not in worker["deploy"]  # Private worker is not an HTTP service.
    assert "preDeployCommand" not in worker["deploy"]


def _docker_instructions(path: Path) -> list[str]:
    """Compare checked-in instructions, not comments or whitespace-only changes."""
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def test_railway_research_image_matches_existing_research_target():
    root = _docker_instructions(ROOT / "Dockerfile")
    research = _docker_instructions(ROOT / "Dockerfile.research")
    # Railway's custom Dockerfile path must select exactly the target already
    # used by Compose. Keep dependency pins/settings single-source via parity.
    assert root[-1] == "FROM control-plane AS runtime"
    assert research == root[:-1]
    assert research[-1] == 'CMD ["python", "-m", "money.worker"]'
    assert "CMD python -m money.worker --healthcheck" in research
    assert research[-4] == "USER money"


def test_default_api_image_stays_minimal_and_builds_stay_locked():
    root = _docker_instructions(ROOT / "Dockerfile")
    control = root[:root.index("FROM control-plane AS research")]
    assert "FROM control-plane AS runtime" == root[-1]
    assert not any("--extra" in line for line in control)
    assert "COPY pyproject.toml uv.lock README.md ./" in control
    assert "USER money" in control
    assert "STOPSIGNAL SIGTERM" in control
    assert any("useradd --system --uid 10001" in line for line in control)
    for filename in ("Dockerfile", "Dockerfile.research"):
        instructions = _docker_instructions(ROOT / filename)
        installs = [line for line in instructions if line.startswith("RUN uv sync")]
        assert len(installs) == 2
        assert all("--locked --no-dev --no-editable" in line for line in installs)
        assert installs[-1] == "RUN uv sync --locked --no-dev --no-editable --extra research"
        assert not any("pip install" in line or "--no-deps" in line for line in instructions)


def test_worker_image_does_not_include_qualification_or_change_research_mode():
    instructions = _docker_instructions(ROOT / "Dockerfile.research")
    joined = "\n".join(instructions)
    assert "MONEY_ENV=production" in joined
    for bypass in ("MONEY_RESEARCH_MODE", "MONEY_LIVE_MANIFEST", "live_rnd", "demo"):
        assert bypass not in joined
    for line in instructions:
        if line.startswith(("COPY", "ADD")):
            assert not any(item in line for item in ("upstreams", ".env", "fixtures", "qualification"))
