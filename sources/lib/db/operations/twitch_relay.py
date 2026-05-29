"""Operations with DB table `twitch_relays`"""

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sources.lib.db import AsyncSession
from sources.lib.db.models import TwitchRelay


async def get_guild_relays(guild_id: int) -> list[TwitchRelay]:
    """Return all Twitch relays configured for a guild.

    Args:
        guild_id: Discord guild ID.

    Returns:
        List of TwitchRelay rows for the guild.
    """
    async with AsyncSession() as session:
        result = await session.scalars(
            select(TwitchRelay).where(TwitchRelay.guild_id == guild_id)
        )
        return list(result.all())


async def get_all_relays() -> list[TwitchRelay]:
    """Return all Twitch relays across all guilds.

    Returns:
        List of all TwitchRelay rows.
    """
    async with AsyncSession() as session:
        result = await session.scalars(select(TwitchRelay))
        return list(result.all())


async def add_relay(
    guild_id: int,
    twitch_user_id: str,
    twitch_login: str,
    discord_channel_id: int,
) -> bool:
    """Add a new Twitch relay. No-op if an identical relay already exists.

    Args:
        guild_id: Discord guild ID.
        twitch_user_id: Twitch numeric user ID (stable across renames).
        twitch_login: Twitch login name (display).
        discord_channel_id: Discord channel to post notifications to.

    Returns:
        True if inserted, False if already existed.
    """
    async with AsyncSession() as session:
        stmt = (
            pg_insert(TwitchRelay)
            .values(
                guild_id=guild_id,
                twitch_user_id=twitch_user_id,
                twitch_login=twitch_login,
                discord_channel_id=discord_channel_id,
            )
            .on_conflict_do_nothing(
                index_elements=['guild_id', 'twitch_user_id', 'discord_channel_id']
            )
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount == 1


async def remove_relay(relay_id: int, guild_id: int) -> tuple[str, str] | None:
    """Remove a Twitch relay by ID, scoped to a guild.

    Args:
        relay_id: Primary key of the TwitchRelay row.
        guild_id: Discord guild ID (guards against cross-guild deletion).

    Returns:
        (twitch_login, twitch_user_id) of the removed relay, or None if not found.
    """
    async with AsyncSession() as session:
        relay = await session.scalar(
            select(TwitchRelay).where(
                TwitchRelay.id == relay_id,
                TwitchRelay.guild_id == guild_id,
            )
        )
        if relay is None:
            return None
        result = relay.twitch_login, relay.twitch_user_id
        await session.delete(relay)
        await session.commit()
        return result


async def get_relay_by_id(relay_id: int, guild_id: int) -> TwitchRelay | None:
    """Return a single relay by ID, scoped to a guild.

    Args:
        relay_id: Primary key of the TwitchRelay row.
        guild_id: Discord guild ID (scope guard).

    Returns:
        TwitchRelay row, or None if not found.
    """
    async with AsyncSession() as session:
        return await session.scalar(
            select(TwitchRelay).where(
                TwitchRelay.id == relay_id,
                TwitchRelay.guild_id == guild_id,
            )
        )


async def set_relay_message(
    relay_id: int, guild_id: int, message: str | None
) -> str | None:
    """Set or clear the custom notification message for a relay.

    Args:
        relay_id: Primary key of the TwitchRelay row.
        guild_id: Discord guild ID (scope guard).
        message: New message text, or None to restore the built-in default.

    Returns:
        twitch_login of the relay, or None if not found.
    """
    async with AsyncSession() as session:
        relay = await session.scalar(
            select(TwitchRelay).where(
                TwitchRelay.id == relay_id,
                TwitchRelay.guild_id == guild_id,
            )
        )
        if relay is None:
            return None
        relay.custom_message = message
        await session.commit()
        return relay.twitch_login


async def update_relay_channel(
    relay_id: int, guild_id: int, discord_channel_id: int
) -> str | None:
    """Move a relay to a different Discord channel.

    Args:
        relay_id: Primary key of the TwitchRelay row.
        guild_id: Discord guild ID (scope guard).
        discord_channel_id: New Discord channel ID.

    Returns:
        twitch_login of the relay, or None if not found.

    Raises:
        ValueError: If another relay already forwards the same Twitch channel to
            the requested Discord channel in this guild.
    """
    async with AsyncSession() as session:
        relay = await session.scalar(
            select(TwitchRelay).where(
                TwitchRelay.id == relay_id,
                TwitchRelay.guild_id == guild_id,
            )
        )
        if relay is None:
            return None
        conflict = await session.scalar(
            select(TwitchRelay).where(
                TwitchRelay.guild_id == guild_id,
                TwitchRelay.twitch_user_id == relay.twitch_user_id,
                TwitchRelay.discord_channel_id == discord_channel_id,
                TwitchRelay.id != relay_id,
            )
        )
        if conflict is not None:
            raise ValueError('duplicate')
        relay.discord_channel_id = discord_channel_id
        await session.commit()
        return relay.twitch_login


async def update_login(twitch_user_id: str, twitch_login: str) -> None:
    """Update the stored login for a Twitch user ID (handles username changes).

    Args:
        twitch_user_id: Twitch numeric user ID.
        twitch_login: New login name.
    """
    async with AsyncSession() as session:
        await session.execute(
            update(TwitchRelay)
            .where(TwitchRelay.twitch_user_id == twitch_user_id)
            .values(twitch_login=twitch_login)
        )
        await session.commit()
