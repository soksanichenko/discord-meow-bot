"""DB operations for birthday-related tables."""

from sqlalchemy import select

from sources.lib.db import AsyncSession
from sources.lib.db.crud.base import get_db_entity, update_db_entity_or_create
from sources.lib.db.models import GuildMemberBirthday, GuildSettings


async def get_guild_member_birthday(guild_id: int, user_id: int) -> GuildMemberBirthday | None:
    """Return the birthday record for a specific guild member, or None."""
    async with AsyncSession() as session:
        return await get_db_entity(
            session, GuildMemberBirthday, guild_id=guild_id, user_id=user_id,
        )


async def set_guild_member_birthday(
    guild_id: int,
    user_id: int,
    day: int,
    month: int,
    year: int | None,
) -> None:
    """Create or update a birthday record for a guild member.

    Resets last_announced_year so the user gets a new announcement if
    their birthday was already announced this calendar year.

    Args:
        guild_id: Discord guild ID.
        user_id: Discord user ID.
        day: Birth day (1–31).
        month: Birth month (1–12).
        year: Birth year, or None if the user chose not to share it.
    """
    async with AsyncSession() as session:
        await update_db_entity_or_create(
            db_session=session,
            table_class=GuildMemberBirthday,
            filters={'guild_id': guild_id, 'user_id': user_id},
            updates={
                'birthday_day': day,
                'birthday_month': month,
                'birth_year': year,
                'last_announced_year': None,
            },
        )


async def remove_guild_member_birthday(guild_id: int, user_id: int) -> bool:
    """Delete a birthday record. Returns True if a record was deleted.

    Args:
        guild_id: Discord guild ID.
        user_id: Discord user ID.
    """
    async with AsyncSession() as session:
        record = await get_db_entity(
            session, GuildMemberBirthday, guild_id=guild_id, user_id=user_id,
        )
        if record is None:
            return False
        await session.delete(record)
        await session.commit()
        return True


async def get_guild_birthdays(guild_id: int) -> list[GuildMemberBirthday]:
    """Return all birthday records for a guild, ordered by month then day.

    Args:
        guild_id: Discord guild ID.
    """
    async with AsyncSession() as session:
        rows = await session.scalars(
            select(GuildMemberBirthday)
            .where(GuildMemberBirthday.guild_id == guild_id)
            .order_by(GuildMemberBirthday.birthday_month, GuildMemberBirthday.birthday_day)
        )
        return list(rows)


async def get_all_unannounced_birthdays_for_guild(
    guild_id: int,
    current_year: int,
) -> list[GuildMemberBirthday]:
    """Return all birthday records for a guild that haven't been announced this year.

    The caller is responsible for filtering by the user's local date.

    Args:
        guild_id: Discord guild ID.
        current_year: Current UTC calendar year — used to skip already-announced records.
    """
    async with AsyncSession() as session:
        rows = await session.scalars(
            select(GuildMemberBirthday).where(
                GuildMemberBirthday.guild_id == guild_id,
                (GuildMemberBirthday.last_announced_year == None)  # noqa: E711
                | (GuildMemberBirthday.last_announced_year < current_year),
            )
        )
        return list(rows)


async def mark_birthday_announced(guild_id: int, user_id: int, year: int) -> None:
    """Set last_announced_year to prevent duplicate announcements.

    Args:
        guild_id: Discord guild ID.
        user_id: Discord user ID.
        year: The calendar year being marked.
    """
    async with AsyncSession() as session:
        record = await get_db_entity(
            session, GuildMemberBirthday, guild_id=guild_id, user_id=user_id,
        )
        if record is not None:
            record.last_announced_year = year
            await session.commit()


async def get_guild_settings(guild_id: int) -> GuildSettings | None:
    """Return guild settings, or None if not yet configured.

    Args:
        guild_id: Discord guild ID.
    """
    async with AsyncSession() as session:
        return await get_db_entity(session, GuildSettings, guild_id=guild_id)


async def upsert_guild_settings(guild_id: int, **updates: object) -> None:
    """Create or update guild settings with the provided field values.

    Args:
        guild_id: Discord guild ID.
        **updates: Field names and values to set on GuildSettings.
    """
    async with AsyncSession() as session:
        await update_db_entity_or_create(
            db_session=session,
            table_class=GuildSettings,
            filters={'guild_id': guild_id},
            updates=updates,
        )
