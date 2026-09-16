"""Frozen offboarding/metering revisions match both supported SQL dialects."""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

from money.accounts import models as accounts
from money.product.metering import call_metering

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("revision", ["0006_offboarding", "0007_call_metering"])
def test_new_release_migrations_do_not_import_mutable_application_code(revision):
    path = ROOT / "migrations/versions" / f"{revision}.py"
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("money")
        elif isinstance(node, ast.Import):
            assert all(not item.name.startswith("money") for item in node.names)


def constraint_key(constraint):
    if isinstance(constraint, sa.CheckConstraint):
        return constraint.name, str(constraint.sqltext)
    return constraint.name, tuple(column.name for column in constraint.columns)


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
@pytest.mark.parametrize("revision", ["0006_offboarding", "0007_call_metering"])
def test_release_migration_matches_metadata(revision, dialect, monkeypatch):
    path = ROOT / "migrations/versions" / f"{revision}.py"
    spec = importlib.util.spec_from_file_location(f"test_money_{revision}", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == revision[:4]
    assert migration.down_revision == ("0005" if revision.startswith("0006") else "0006")
    metadata = sa.MetaData()
    for name, size in (
        ("users", 36),
        ("organisations", 80),
        ("research_jobs", 36),
        ("budget_reservations", 128),
    ):
        sa.Table(name, metadata, sa.Column("id", sa.String(size), primary_key=True))
    captured, added, indexes, triggers = {}, {}, set(), []

    def create_table(name, *arguments):
        captured[name] = sa.Table(name, metadata, *arguments)

    operations = SimpleNamespace(
        create_table=create_table,
        add_column=lambda table, column: added.update({(table, column.name): column}),
        create_index=lambda name, table, columns: indexes.add((name, table, tuple(columns))),
        get_bind=lambda: SimpleNamespace(dialect=dialect),
        execute=lambda sql: triggers.append(str(sql)),
    )
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    expected_tables = (
        (accounts.deletion_requests, accounts.workspace_closures)
        if revision.startswith("0006")
        else (call_metering,)
    )
    assert set(captured) == {table.name for table in expected_tables}
    expected_indexes = set()
    for table in expected_tables:
        actual = captured[table.name]
        assert list(actual.c.keys()) == list(table.c.keys())
        for expected in table.c:
            column = actual.c[expected.name]
            assert column.type.compile(dialect) == expected.type.compile(dialect)
            assert column.nullable == expected.nullable
            assert column.primary_key == expected.primary_key
            assert column.unique == expected.unique
            actual_default = (
                str(column.server_default.arg) if column.server_default is not None else None
            )
            expected_default = (
                str(expected.server_default.arg) if expected.server_default is not None else None
            )
            assert actual_default == expected_default
            assert {key.target_fullname for key in column.foreign_keys} == {
                key.target_fullname for key in expected.foreign_keys
            }
        for constraint_type in (sa.CheckConstraint, sa.UniqueConstraint):
            assert {
                constraint_key(c) for c in actual.constraints if isinstance(c, constraint_type)
            } == {constraint_key(c) for c in table.constraints if isinstance(c, constraint_type)}
        expected_indexes.update(
            (index.name, table.name, tuple(column.name for column in index.columns))
            for index in table.indexes
        )
    if revision.startswith("0006"):
        assert list(added) == [("transactional_email_outbox", "recipient_hash")]
        actual_column = added[("transactional_email_outbox", "recipient_hash")]
        assert actual_column.type.compile(
            dialect
        ) == accounts.email_outbox.c.recipient_hash.type.compile(dialect)
        assert actual_column.nullable == accounts.email_outbox.c.recipient_hash.nullable
        expected_indexes.add(
            ("ix_transactional_email_recipient", "transactional_email_outbox", ("recipient_hash",))
        )
        assert not triggers
    else:
        assert not added
        assert len(triggers) == (1 if dialect.name == "postgresql" else 2)
        assert all("immutable_call_metering" in sql and "BEFORE" in sql for sql in triggers)
    assert indexes == expected_indexes
    with pytest.raises(RuntimeError, match="forward migration"):
        migration.downgrade()
