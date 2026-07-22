"""Alembic environment for the CommerceVision MySQL schema."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from commercevision_contracts.config import load_settings
from commercevision_persistence.database import sync_mysql_url
from commercevision_persistence.models import Base
from commercevision_persistence.schema import compare_mysql_datetime_precision
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = load_settings("migration")
config.set_main_option(
    "sqlalchemy.url",
    sync_mysql_url(settings.mysql_dsn).render_as_string(hide_password=False).replace("%", "%%"),
)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=compare_mysql_datetime_precision,
        render_as_batch=False,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=compare_mysql_datetime_precision,
            render_as_batch=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
