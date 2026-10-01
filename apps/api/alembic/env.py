"""Alembic environment. DATABASE_URL comes from app settings."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401  — register metadata for autogenerate

config = context.config

if config.config_file_name is not None:
    # In-process migration checks must not disable the application's audit
    # loggers for later requests/tests. The Alembic CLI still configures its
    # own root/sqlalchemy/alembic handlers from this file.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
# ConfigParser treats percent signs in URL-encoded credentials as interpolation.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
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
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
