"""Operations with DB tables `message_stats` and `stats_import_progress`"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sources.lib.db import AsyncSession
from sources.lib.db.models import MessageStats, StatsImportProgress


async def increment_message_counts(guild_id: int, counts: dict[int, int]) -> None:
    """Atomically increment message counts for one or more users in a guild.

    Args:
        guild_id: Discord guild ID.
        counts: Mapping of user_id to the number of messages to add.
    """
    async with AsyncSession() as session:
        for user_id, delta in counts.items():
            stmt = (
                pg_insert(MessageStats)
                .values(guild_id=guild_id, user_id=user_id, message_count=delta)
                .on_conflict_do_update(
                    index_elements=['guild_id', 'user_id'],
                    set_={'message_count': MessageStats.message_count + delta},
                )
            )
            await session.execute(stmt)
        await session.commit()


async def get_leaderboard(guild_id: int, limit: int = 10) -> list[MessageStats]:
    """Return the top users by message count for a guild.

    Args:
        guild_id: Discord guild ID.
        limit: Maximum number of rows to return.

    Returns:
        List of MessageStats rows ordered by message_count descending.
    """
    async with AsyncSession() as session:
        result = await session.scalars(
            select(MessageStats)
            .where(MessageStats.guild_id == guild_id)
            .order_by(MessageStats.message_count.desc())
            .limit(limit)
        )
        return list(result.all())


async def get_channel_progress(
    guild_id: int, channel_id: int
) -> StatsImportProgress | None:
    """Fetch the import progress record for a single channel.

    Args:
        guild_id: Discord guild ID.
        channel_id: Discord channel ID.

    Returns:
        The progress row or None if this channel has not been touched yet.
    """
    async with AsyncSession() as session:
        return await session.get(StatsImportProgress, (guild_id, channel_id))


async def get_guilds_with_incomplete_import() -> list[int]:
    """Return guild IDs that have at least one channel not yet fully imported.

    Returns:
        List of guild IDs with incomplete import progress.
    """
    async with AsyncSession() as session:
        result = await session.scalars(
            select(StatsImportProgress.guild_id)
            .where(StatsImportProgress.is_completed.is_(False))
            .distinct()
        )
        return list(result.all())


async def get_all_channel_progress(guild_id: int) -> list[StatsImportProgress]:
    """Return all import progress rows for a guild.

    Args:
        guild_id: Discord guild ID.

    Returns:
        List of StatsImportProgress rows for the guild.
    """
    async with AsyncSession() as session:
        result = await session.scalars(
            select(StatsImportProgress).where(StatsImportProgress.guild_id == guild_id)
        )
        return list(result.all())


async def save_channel_progress(
    guild_id: int,
    channel_id: int,
    last_message_id: int | None,
    is_completed: bool,
) -> None:
    """Upsert the import progress for a channel.

    Args:
        guild_id: Discord guild ID.
        channel_id: Discord channel ID.
        last_message_id: Snowflake ID of the last processed message.
        is_completed: Whether this channel's history has been fully processed.
    """
    async with AsyncSession() as session:
        stmt = (
            pg_insert(StatsImportProgress)
            .values(
                guild_id=guild_id,
                channel_id=channel_id,
                last_message_id=last_message_id,
                is_completed=is_completed,
            )
            .on_conflict_do_update(
                index_elements=['guild_id', 'channel_id'],
                set_={
                    'last_message_id': last_message_id,
                    'is_completed': is_completed,
                },
            )
        )
        await session.execute(stmt)
        await session.commit()
