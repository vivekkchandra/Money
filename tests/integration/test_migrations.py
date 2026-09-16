from alembic import command
from alembic.config import Config

from money.storage import ResearchStore


def test_migration_is_idempotent_and_matches_declared_metadata(store: ResearchStore):
    configuration = Config("alembic.ini")
    configuration.attributes["database_url"] = store.database_url
    command.upgrade(configuration, "head")
    command.check(configuration)
