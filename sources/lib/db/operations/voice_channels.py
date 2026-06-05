"""DB operations for voice channel tracking."""

from sqlalchemy import select

from sources.lib.db import AsyncSession
from sources.lib.db.crud.base import CRUDBase
from sources.lib.db.models import VoiceChannel


async def upsert_voice_channel(channel_id: int, guild_id: int, name: str) -> None:
    """Insert or update a voice channel record.

    Only updates the channel name and guild_id — the status column is preserved
    on update so that auto-managed status decisions remain correct.
    """
    async with AsyncSession() as session:
        await CRUDBase(session).upsert(
            VoiceChannel,
            filters={'channel_id': channel_id},
            updates={'guild_id': guild_id, 'name': name},
        )


async def set_voice_channel_status(channel_id: int, status: str | None) -> None:
    """Update the cached status for an existing voice channel.

    No-op if the channel is not yet in the DB.
    """
    async with AsyncSession() as session:
        channel = await CRUDBase(session).get(VoiceChannel, channel_id=channel_id)
        if channel is not None:
            await CRUDBase(session).update(channel, status=status)


async def get_voice_channel_status(channel_id: int) -> str | None:
    """Return the last known status for a channel, or None if not tracked."""
    async with AsyncSession() as session:
        channel = await CRUDBase(session).get(VoiceChannel, channel_id=channel_id)
        return channel.status if channel is not None else None


async def delete_voice_channel(channel_id: int) -> None:
    """Delete a voice channel record."""
    async with AsyncSession() as session:
        await CRUDBase(session).delete_if_exists(VoiceChannel, channel_id=channel_id)


async def sync_guild_voice_channels(guild_id: int, channel_map: dict[int, str]) -> None:
    """Sync voice channel records for a guild in a single transaction.

    Upserts all channels present in channel_map (updating names for existing
    rows without touching their status) and deletes stale rows for channels
    that no longer exist in Discord.
    """
    async with AsyncSession() as session:
        stmt = select(VoiceChannel).filter_by(guild_id=guild_id)
        existing = {row.channel_id: row for row in (await session.scalars(stmt)).all()}

        for channel_id, name in channel_map.items():
            if channel_id in existing:
                existing[channel_id].name = name
                session.add(existing[channel_id])
            else:
                session.add(
                    VoiceChannel(channel_id=channel_id, guild_id=guild_id, name=name)
                )

        for channel_id, row in existing.items():
            if channel_id not in channel_map:
                await session.delete(row)

        await session.commit()
