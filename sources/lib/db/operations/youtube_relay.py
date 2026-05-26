"""Operations with DB table `youtube_relays`"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sources.lib.db import AsyncSession
from sources.lib.db.models import YouTubeRelay


async def get_guild_relays(guild_id: int) -> list[YouTubeRelay]:
    """Return all YouTube relays configured for a guild.

    Args:
        guild_id: Discord guild ID.

    Returns:
        List of YouTubeRelay rows for the guild.
    """
    async with AsyncSession() as session:
        result = await session.scalars(
            select(YouTubeRelay).where(YouTubeRelay.guild_id == guild_id)
        )
        return list(result.all())


async def get_all_relays() -> list[YouTubeRelay]:
    """Return all YouTube relays across all guilds.

    Returns:
        List of all YouTubeRelay rows.
    """
    async with AsyncSession() as session:
        result = await session.scalars(select(YouTubeRelay))
        return list(result.all())


async def add_relay(
    guild_id: int,
    yt_channel_id: str,
    yt_channel_title: str,
    discord_channel_id: int,
    last_video_id: str | None,
    post_videos: bool = True,
    post_shorts: bool = True,
    post_lives: bool = True,
) -> bool:
    """Add a new YouTube relay. No-op if the same relay already exists.

    Args:
        guild_id: Discord guild ID.
        yt_channel_id: YouTube channel ID (UCxxx).
        yt_channel_title: Display name of the YouTube channel.
        discord_channel_id: Discord channel to post new videos to.
        last_video_id: Video ID of the latest upload (to avoid flooding history).
        post_videos: Whether to post regular videos.
        post_shorts: Whether to post Shorts.
        post_lives: Whether to post live streams.

    Returns:
        True if a new row was inserted, False if it already existed.
    """
    async with AsyncSession() as session:
        stmt = (
            pg_insert(YouTubeRelay)
            .values(
                guild_id=guild_id,
                yt_channel_id=yt_channel_id,
                yt_channel_title=yt_channel_title,
                discord_channel_id=discord_channel_id,
                last_video_id=last_video_id,
                post_videos=post_videos,
                post_shorts=post_shorts,
                post_lives=post_lives,
            )
            .on_conflict_do_nothing(
                index_elements=['guild_id', 'yt_channel_id', 'discord_channel_id']
            )
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount == 1


async def remove_relay(guild_id: int, yt_channel_id: str) -> bool:
    """Remove a YouTube relay for a guild.

    Args:
        guild_id: Discord guild ID.
        yt_channel_id: YouTube channel ID to stop relaying.

    Returns:
        True if a row was deleted, False if not found.
    """
    async with AsyncSession() as session:
        relay = await session.scalar(
            select(YouTubeRelay).where(
                YouTubeRelay.guild_id == guild_id,
                YouTubeRelay.yt_channel_id == yt_channel_id,
            )
        )
        if relay is None:
            return False
        await session.delete(relay)
        await session.commit()
        return True


async def set_relay_message(
    guild_id: int,
    yt_channel_id: str,
    content_type: str,
    message: str | None,
) -> str | None:
    """Set or clear the custom notification message for one content type.

    Args:
        guild_id: Discord guild ID.
        yt_channel_id: YouTube channel ID (UCxxx).
        content_type: One of 'video', 'short', 'live'.
        message: Custom text, or None to reset to the built-in default.

    Returns:
        The channel title if the relay was found and updated, None if not found.
    """
    field_map = {
        'video': 'message_video',
        'short': 'message_short',
        'live': 'message_live',
    }
    async with AsyncSession() as session:
        relay = await session.scalar(
            select(YouTubeRelay).where(
                YouTubeRelay.guild_id == guild_id,
                YouTubeRelay.yt_channel_id == yt_channel_id,
            )
        )
        if relay is None:
            return None
        setattr(relay, field_map[content_type], message)
        await session.commit()
        return relay.yt_channel_title


async def update_last_video_id(relay_id: int, last_video_id: str) -> None:
    """Persist the ID of the last relayed video.

    Args:
        relay_id: Primary key of the YouTubeRelay row.
        last_video_id: Video ID of the most recently posted video.
    """
    async with AsyncSession() as session:
        relay = await session.get(YouTubeRelay, relay_id)
        if relay is not None:
            relay.last_video_id = last_video_id
            await session.commit()
