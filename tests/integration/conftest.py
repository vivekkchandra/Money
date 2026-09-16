from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from money.storage import ResearchStore


def migrate(database_url: str) -> None:
    configuration = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    configuration.attributes["database_url"] = database_url
    command.upgrade(configuration, "head")


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MONEY_ENV", "test")
    url = f"sqlite:///{tmp_path / 'research.db'}"
    migrate(url)
    repository = ResearchStore(url, allow_sqlite=True)
    yield repository
    repository.engine.dispose()
