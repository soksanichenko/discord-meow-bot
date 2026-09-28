"""Main DB module"""

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from sources.config import config

async_engine = create_async_engine(url=config.async_db_url)
async_session_factory = async_sessionmaker(
    bind=async_engine,
    expire_on_commit=False,
)
# A fresh session per call. A task-scoped registry would keep a reference to
# every task that ever touched the DB, and discord.py spawns one per event.
AsyncSession = async_session_factory
