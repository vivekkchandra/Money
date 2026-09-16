"""Migrate durable application state without importing the research engine."""

import os

from alembic import context
from sqlalchemy import create_engine

from money.storage.models import metadata

config = context.config
database_url = config.attributes.get("database_url") or os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is required for migrations")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
if database_url.startswith("sqlite:") and os.environ.get("MONEY_ENV", "production") not in {
    "development",
    "test",
}:
    raise RuntimeError("SQLite migrations are permitted only in development/test")

if context.is_offline_mode():
    context.configure(url=database_url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(database_url).connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
