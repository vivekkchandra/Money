"""Commercial migration definitions are frozen and match both database dialects."""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

from money.product import models

MIGRATION = Path(__file__).resolve().parents[2] / "migrations/versions/0005_commercial_product.py"


def test_historical_migration_does_not_import_mutable_application_models():
    tree = ast.parse(MIGRATION.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("money")
        elif isinstance(node, ast.Import):
            assert all(not alias.name.startswith("money") for alias in node.names)


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_frozen_migration_types_columns_indexes_and_immutable_triggers_match(dialect, monkeypatch):
    spec = importlib.util.spec_from_file_location("money_migration_0005_test", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metadata = sa.MetaData()
    sa.Table("research_jobs", metadata, sa.Column("id", sa.String(36), primary_key=True))
    captured = {}
    indexes = set()
    triggers = []

    def create_table(name, *columns):
        captured[name] = sa.Table(name, metadata, *columns)

    def create_index(name, table, columns):
        indexes.add((name, table, tuple(columns)))

    operations = SimpleNamespace(
        create_table=create_table,
        create_index=create_index,
        get_bind=lambda: SimpleNamespace(dialect=dialect),
        execute=lambda sql: triggers.append(str(sql)),
    )
    monkeypatch.setattr(module, "op", operations)
    module.upgrade()
    expected_tables = (
        models.subscriptions,
        models.usage_records,
        models.billing_events,
        models.watchlist,
        models.notification_reads,
        models.product_events,
        models.preferences,
    )
    assert set(captured) == {table.name for table in expected_tables}
    for table in expected_tables:
        actual = captured[table.name]
        assert list(actual.c.keys()) == list(table.c.keys())
        for expected in table.c:
            column = actual.c[expected.name]
            assert column.type.compile(dialect) == expected.type.compile(dialect)
            assert column.nullable == expected.nullable
            assert column.primary_key == expected.primary_key
            assert column.unique == expected.unique
            assert {foreign.target_fullname for foreign in column.foreign_keys} == {
                foreign.target_fullname for foreign in expected.foreign_keys
            }
        assert {
            (index.name, table.name, tuple(column.name for column in index.columns))
            for index in table.indexes
        } <= indexes
    assert indexes == {
        ("ix_usage_workspace_period", "usage_records", ("workspace_id", "period_start")),
        ("ix_usage_user_time", "usage_records", ("user_id", "created_at")),
        ("ix_billing_event_queue", "billing_events", ("state", "available_at")),
        ("ix_product_events_workspace_time", "product_events", ("workspace_id", "created_at")),
    }
    assert len(triggers) == (2 if dialect.name == "postgresql" else 4)
    assert all("BEFORE" in trigger and "immutable_" in trigger for trigger in triggers)
