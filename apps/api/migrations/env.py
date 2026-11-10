from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _alembic_url(url: str) -> str:
    """Force the psycopg3 dialect.

    Plain ``postgresql://`` resolves to psycopg2, which on Chinese/non-UTF8
    Windows often raises ``UnicodeDecodeError`` while decoding a connection
    failure message and hides the real cause (server down, bad password, …).
    The app itself already depends on ``psycopg`` (v3).
    """
    raw = (url or "").strip()
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    if raw.startswith("postgresql+psycopg2://"):
        raw = "postgresql+psycopg://" + raw[len("postgresql+psycopg2://") :]
    elif raw.startswith("postgresql://"):
        raw = "postgresql+psycopg://" + raw[len("postgresql://") :]
    return raw


database_url = _alembic_url(
    os.getenv("SA_DATABASE_URL")
    or config.get_main_option("sqlalchemy.url")
    or ""
)
config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
