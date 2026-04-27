"""DB operations for music player per-guild settings."""

from sources.lib.db import AsyncSession
from sources.lib.db.models import GuildMusicPlayerSettings


async def get_music_player_settings(guild_id: int) -> tuple[int, bool, bool]:
    """Return (volume 0-100, autoplay, random_order) for a guild, with defaults if not set.

    Args:
        guild_id: Discord guild ID.
    """
    async with AsyncSession() as session:
        row = await session.get(GuildMusicPlayerSettings, guild_id)
        if row is None:
            return 100, False, False
        return row.volume, row.autoplay, row.random_order


async def upsert_music_player_settings(
    guild_id: int,
    volume: int,
    autoplay: bool,
    random_order: bool,
) -> None:
    """Persist music player settings for a guild, creating the row if absent.

    Args:
        guild_id: Discord guild ID.
        volume: Volume level 0–100.
        autoplay: Whether autoplay is enabled.
        random_order: Whether random queue order is enabled.
    """
    async with AsyncSession() as session:
        row = await session.get(GuildMusicPlayerSettings, guild_id)
        if row is None:
            session.add(GuildMusicPlayerSettings(
                guild_id=guild_id, volume=volume, autoplay=autoplay, random_order=random_order,
            ))
        else:
            row.volume = volume
            row.autoplay = autoplay
            row.random_order = random_order
        await session.commit()
