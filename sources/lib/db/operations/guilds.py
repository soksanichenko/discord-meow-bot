"""
Operations with DB table `guilds`
"""

import discord
from sqlalchemy import select

from sources.lib.db import AsyncSession
from sources.lib.db.models import Guild


async def add_guild(discord_guild: discord.Guild):
    """Add a guild to a database"""
    async with AsyncSession() as db_session:
        async with db_session.begin():
            guild = select(Guild).where(
                Guild.id == discord_guild.id,
            )
            guild = (await db_session.scalars(guild)).one_or_none()
            if guild is None:
                guild = Guild(
                    name=discord_guild.name,
                    id=discord_guild.id,
                )
                db_session.add(guild)
            elif guild.name != discord_guild.name:
                guild.name = discord_guild.name
                db_session.add(guild)
