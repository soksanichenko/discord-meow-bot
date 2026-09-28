"""Operations with DB tables `message_stats`, `stats_import_progress`, `stats_import_jobs`"""

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sources.lib.db import AsyncSession
from sources.lib.db.models import MessageStats, StatsImportJob, StatsImportProgress


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


async def apply_counts(
    guild_id: int,
    channel_id: int,
    counts: dict[int, int],
    *,
    range_start: int,
    newest_id: int | None = None,
    oldest_id: int | None = None,
    is_completed: bool | None = None,
) -> None:
    """Add message counts and extend the channel's counted range in one transaction.

    Writing both together is what makes counting exact-once: after a crash the
    counts and the range have either both moved or neither has. The range only
    grows (GREATEST for newest_id, LEAST for oldest_id). A missing progress row is
    created as the empty range [range_start, range_start] before the bounds apply.

    Args:
        guild_id: Discord guild ID.
        channel_id: Discord channel ID.
        counts: Mapping of user_id to the number of messages to add.
        range_start: Bound for a progress row that does not exist yet.
        newest_id: New upper bound of the counted range, if it moves.
        oldest_id: New lower bound of the counted range, if it moves.
        is_completed: New value of the history-import-finished flag, if it changes.
    """
    async with AsyncSession() as session:
        for user_id, delta in counts.items():
            await session.execute(
                pg_insert(MessageStats)
                .values(guild_id=guild_id, user_id=user_id, message_count=delta)
                .on_conflict_do_update(
                    index_elements=['guild_id', 'user_id'],
                    set_={'message_count': MessageStats.message_count + delta},
                )
            )
        insert = pg_insert(StatsImportProgress).values(
            guild_id=guild_id,
            channel_id=channel_id,
            oldest_id=range_start if oldest_id is None else min(range_start, oldest_id),
            newest_id=range_start if newest_id is None else max(range_start, newest_id),
            is_completed=bool(is_completed),
        )
        updates = {}
        if newest_id is not None:
            updates['newest_id'] = func.greatest(
                StatsImportProgress.newest_id, newest_id
            )
        if oldest_id is not None:
            updates['oldest_id'] = func.least(StatsImportProgress.oldest_id, oldest_id)
        if is_completed is not None:
            updates['is_completed'] = is_completed
        index_elements = ['guild_id', 'channel_id']
        if updates:
            stmt = insert.on_conflict_do_update(
                index_elements=index_elements, set_=updates
            )
        else:
            stmt = insert.on_conflict_do_nothing(index_elements=index_elements)
        await session.execute(stmt)
        await session.commit()


async def upsert_import_job(guild_id: int, since: datetime | None) -> None:
    """Request a history import for a guild, replacing any pending request.

    Args:
        guild_id: Discord guild ID.
        since: Lower time bound for the import; None imports the whole history.
    """
    async with AsyncSession() as session:
        await session.execute(
            pg_insert(StatsImportJob)
            .values(guild_id=guild_id, since=since)
            .on_conflict_do_update(index_elements=['guild_id'], set_={'since': since})
        )
        await session.commit()


async def get_import_jobs() -> list[StatsImportJob]:
    """Return all pending history import requests.

    Returns:
        List of StatsImportJob rows.
    """
    async with AsyncSession() as session:
        result = await session.scalars(select(StatsImportJob))
        return list(result.all())


async def delete_import_job(guild_id: int) -> None:
    """Mark a guild's history import as finished.

    Args:
        guild_id: Discord guild ID.
    """
    async with AsyncSession() as session:
        await session.execute(
            delete(StatsImportJob).where(StatsImportJob.guild_id == guild_id)
        )
        await session.commit()


async def reset_guild_stats(guild_id: int) -> None:
    """Wipe a guild's statistics and request a full re-import, in one transaction.

    Args:
        guild_id: Discord guild ID.
    """
    async with AsyncSession() as session:
        await session.execute(
            delete(MessageStats).where(MessageStats.guild_id == guild_id)
        )
        await session.execute(
            delete(StatsImportProgress).where(StatsImportProgress.guild_id == guild_id)
        )
        await session.execute(
            pg_insert(StatsImportJob)
            .values(guild_id=guild_id, since=None)
            .on_conflict_do_update(index_elements=['guild_id'], set_={'since': None})
        )
        await session.commit()
