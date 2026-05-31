"""Operations with DB table `guilds`"""

from sources.lib.db import AsyncSession
from sources.lib.db.crud.base import CRUDBase
from sources.lib.db.models import Guild


async def upsert_guild(guild_id: int, guild_name: str) -> None:
    """Create or update a guild record in DB."""
    async with AsyncSession() as session:
        await CRUDBase(session).upsert(
            Guild,
            filters={'id': guild_id},
            updates={'name': guild_name},
        )


async def delete_guild(guild_id: int) -> None:
    """Delete a guild record from DB. Cascades to guild_domain_fixers."""
    async with AsyncSession() as session:
        await CRUDBase(session).delete_if_exists(Guild, id=guild_id)
