"""DB operations for music links channel allowlist."""

from sqlalchemy import select

from sources.lib.db import AsyncSession
from sources.lib.db.models import MusicLinksChannel


async def get_allowed_channels(guild_id: int) -> list[int]:
    """Return the list of allowed channel IDs for a guild.

    An empty list means all channels are allowed.

    Args:
        guild_id: Discord guild ID.
    """
    async with AsyncSession() as session:
        rows = await session.scalars(
            select(MusicLinksChannel).where(MusicLinksChannel.guild_id == guild_id)
        )
        return [row.channel_id for row in rows]


async def add_allowed_channel(guild_id: int, channel_id: int) -> bool:
    """Add a channel to the allowlist. Returns False if it was already present.

    Args:
        guild_id: Discord guild ID.
        channel_id: Discord channel ID to add.
    """
    async with AsyncSession() as session:
        existing = await session.get(MusicLinksChannel, (guild_id, channel_id))
        if existing is not None:
            return False
        session.add(MusicLinksChannel(guild_id=guild_id, channel_id=channel_id))
        await session.commit()
        return True


async def remove_allowed_channel(guild_id: int, channel_id: int) -> bool:
    """Remove a channel from the allowlist. Returns False if it was not present.

    Args:
        guild_id: Discord guild ID.
        channel_id: Discord channel ID to remove.
    """
    async with AsyncSession() as session:
        existing = await session.get(MusicLinksChannel, (guild_id, channel_id))
        if existing is None:
            return False
        await session.delete(existing)
        await session.commit()
        return True
