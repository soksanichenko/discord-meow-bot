"""Main DB module"""

from asyncio import current_task
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    async_scoped_session,
)

from sources.config import config


async_engine = create_async_engine(url=config.async_db_url)
async_session_factory = async_sessionmaker(
    bind=async_engine,
    expire_on_commit=True,
)
AsyncSession = async_scoped_session(
    session_factory=async_session_factory,
    scopefunc=current_task,
)
