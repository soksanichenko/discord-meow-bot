"""Operations with DB table `telegram_relays`"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sources.lib.db import AsyncSession
from sources.lib.db.models import TelegramRelay


async def get_guild_relays(guild_id: int) -> list[TelegramRelay]:
    """Return all Telegram relays configured for a guild.

    Args:
        guild_id: Discord guild ID.

    Returns:
        List of TelegramRelay rows for the guild.
    """
    async with AsyncSession() as session:
        result = await session.scalars(
            select(TelegramRelay).where(TelegramRelay.guild_id == guild_id)
        )
        return list(result.all())


async def get_all_relays() -> list[TelegramRelay]:
    """Return all Telegram relays across all guilds.

    Returns:
        List of all TelegramRelay rows.
    """
    async with AsyncSession() as session:
        result = await session.scalars(select(TelegramRelay))
        return list(result.all())


async def add_relay(
    guild_id: int,
    tg_username: str,
    discord_channel_id: int,
    last_entry_id: str | None,
) -> bool:
    """Add a new Telegram relay. No-op if the same relay already exists.

    Args:
        guild_id: Discord guild ID.
        tg_username: Telegram channel username (without @).
        discord_channel_id: Discord channel to post new entries to.
        last_entry_id: RSS entry ID of the latest post (to avoid flooding history).

    Returns:
        True if a new row was inserted, False if it already existed.
    """
    async with AsyncSession() as session:
        stmt = (
            pg_insert(TelegramRelay)
            .values(
                guild_id=guild_id,
                tg_username=tg_username,
                discord_channel_id=discord_channel_id,
                last_entry_id=last_entry_id,
            )
            .on_conflict_do_nothing(
                index_elements=['guild_id', 'tg_username', 'discord_channel_id']
            )
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount == 1


async def remove_relay(guild_id: int, tg_username: str) -> bool:
    """Remove a Telegram relay for a guild.

    Args:
        guild_id: Discord guild ID.
        tg_username: Telegram channel username to remove.

    Returns:
        True if a row was deleted, False if not found.
    """
    async with AsyncSession() as session:
        relay = await session.scalar(
            select(TelegramRelay).where(
                TelegramRelay.guild_id == guild_id,
                TelegramRelay.tg_username == tg_username,
            )
        )
        if relay is None:
            return False
        await session.delete(relay)
        await session.commit()
        return True


async def remove_relay_by_id(relay_id: int, guild_id: int) -> str | None:
    """Remove a Telegram relay by primary key.

    Args:
        relay_id: Primary key of the TelegramRelay row.
        guild_id: Discord guild ID (scope guard — prevents removing another guild's relay).

    Returns:
        tg_username of the deleted relay, or None if not found.
    """
    async with AsyncSession() as session:
        relay = await session.scalar(
            select(TelegramRelay).where(
                TelegramRelay.id == relay_id,
                TelegramRelay.guild_id == guild_id,
            )
        )
        if relay is None:
            return None
        username = relay.tg_username
        await session.delete(relay)
        await session.commit()
        return username


async def update_relay_channel(
    relay_id: int, guild_id: int, discord_channel_id: int
) -> str | None:
    """Move a relay to a different Discord channel.

    Args:
        relay_id: Primary key of the TelegramRelay row.
        guild_id: Discord guild ID (scope guard).
        discord_channel_id: New Discord channel ID.

    Returns:
        tg_username of the relay, or None if not found.

    Raises:
        ValueError: If another relay already forwards the same Telegram channel to
            the requested Discord channel in this guild.
    """
    async with AsyncSession() as session:
        relay = await session.scalar(
            select(TelegramRelay).where(
                TelegramRelay.id == relay_id,
                TelegramRelay.guild_id == guild_id,
            )
        )
        if relay is None:
            return None
        conflict = await session.scalar(
            select(TelegramRelay).where(
                TelegramRelay.guild_id == guild_id,
                TelegramRelay.tg_username == relay.tg_username,
                TelegramRelay.discord_channel_id == discord_channel_id,
                TelegramRelay.id != relay_id,
            )
        )
        if conflict is not None:
            raise ValueError('duplicate')
        relay.discord_channel_id = discord_channel_id
        await session.commit()
        return relay.tg_username


async def update_last_entry_id(relay_id: int, last_entry_id: str) -> None:
    """Persist the ID of the last relayed RSS entry.

    Args:
        relay_id: Primary key of the TelegramRelay row.
        last_entry_id: RSS entry ID of the most recently posted entry.
    """
    async with AsyncSession() as session:
        relay = await session.get(TelegramRelay, relay_id)
        if relay is not None:
            relay.last_entry_id = last_entry_id
            await session.commit()
