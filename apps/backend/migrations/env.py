import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from studgroup.main import Settings, database_engine_options
from studgroup.models import Base


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    settings = Settings()
    engine = create_async_engine(settings.database_url, **database_engine_options(settings))
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=Settings().database_url, target_metadata=Base.metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
