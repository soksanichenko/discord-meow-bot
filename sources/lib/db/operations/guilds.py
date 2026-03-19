"""Operations with DB table `guilds`"""

from sources.lib.db import AsyncSession
from sources.lib.db.crud.base import update_db_entity_or_create
from sources.lib.db.models import Guild


async def upsert_guild(guild_id: int, guild_name: str) -> None:
    """Create or update a guild record in DB."""
    async with AsyncSession() as db_session:
        await update_db_entity_or_create(
            db_session=db_session,
            table_class=Guild,
            filters={'id': guild_id},
            updates={'name': guild_name},
        )
