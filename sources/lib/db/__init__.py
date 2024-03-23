"""Main DB module"""

from asyncio import current_task
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    async_scoped_session,
)
from sqlalchemy_utils import (
    database_exists,
    create_database,
)

from sources.config import config
from sources.lib.db.models import Base


async_engine = create_async_engine(url=config.async_db_url)
async_session_factory = async_sessionmaker(
    bind=async_engine,
    expire_on_commit=True,
)
AsyncSession = async_scoped_session(
    session_factory=async_session_factory,
    scopefunc=current_task,
)


async def create_db_if_not_exists():
    """Creates database if it doesn't exist"""
    async with AsyncSession() as db_session:
        if not database_exists(config.sync_db_url):
            create_database(config.sync_db_url)
        async with db_session.bind.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
