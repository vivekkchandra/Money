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


def test_railway_uses_existing_image_separate_worker_and_release_migration():
    api, worker = [tomllib.loads((ROOT / f"deploy/railway/{service}.toml").read_text())
                   for service in ("api", "worker")]
    assert api["build"] == worker["build"] == {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile"}
    assert api["deploy"]["preDeployCommand"] == ["alembic upgrade head"]
    assert "exec uvicorn money.api.app:app" in api["deploy"]["startCommand"]
    assert "${PORT:-8000}" in api["deploy"]["startCommand"]
    assert api["deploy"]["healthcheckPath"] == "/health/ready"
    assert worker["deploy"]["startCommand"] == "python -m money.worker"
    assert "healthcheckPath" not in worker["deploy"]  # Private worker is not an HTTP service.
    assert "preDeployCommand" not in worker["deploy"]
