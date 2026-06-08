"""User cog"""

from datetime import datetime

import discord
import pytz
from discord import app_commands
from discord.ext import commands

from sources.lib.db.operations.users import get_user, get_users_by_ids, upsert_user
from sources.lib.utils.discord_utils import require_timezone
from sources.lib.utils.get_timestamp import (
    TimestampFormatView,
    autocomplete_timezone,
    parse_and_validate,
)


class UserCog(commands.Cog):
    """User-related commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name='set-timezone',
        description='Set a current timezone of user',
    )
    @app_commands.autocomplete(timezone=autocomplete_timezone)
    async def set_timezone(
        self, interaction: discord.Interaction, timezone: str
    ) -> None:
        """Set a current timezone of user."""
        user = interaction.user
        await upsert_user(
            user_id=user.id,
            name=user.name,
            timezone=timezone,
        )
        await interaction.response.send_message(
            f'Timezone for user **{user.display_name}** is set to **{timezone}**',
            ephemeral=True,
        )

    @app_commands.command(
        name='force-timezone',
        description="Set another member's timezone (admin only)",
    )
    @app_commands.describe(
        user='The member whose timezone to set',
        timezone='Timezone name, e.g. Europe/Kyiv, America/New_York',
    )
    @app_commands.autocomplete(timezone=autocomplete_timezone)
    @app_commands.default_permissions(manage_guild=True)
    async def force_timezone(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        timezone: str,
    ) -> None:
        """Set a timezone for another member.

        Args:
            interaction: The Discord interaction.
            user: The target member.
            timezone: A valid pytz timezone string.
        """
        db_user = await get_user(user.id)
        name = db_user.name if db_user else user.name
        await upsert_user(user_id=user.id, name=name, timezone=timezone)
        await interaction.response.send_message(
            f'Timezone for **{user.display_name}** has been set to **{timezone}**.',
            ephemeral=True,
        )

    @app_commands.command(
        name='my-settings', description='View your personal bot settings'
    )
    async def my_settings(self, interaction: discord.Interaction) -> None:
        """Display personal bot settings for the calling user.

        Args:
            interaction: The Discord interaction.
        """
        db_user = await get_user(interaction.user.id)

        embed = discord.Embed(title='My settings', colour=discord.Colour.blurple())

        timezone_value = db_user.timezone if db_user else '*not set*'
        embed.add_field(name='Timezone', value=timezone_value, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name='timezones',
        description='Show timezone(s) for guild members (admin only)',
    )
    @app_commands.describe(user='Show timezone for a specific member; omit to list all')
    @app_commands.default_permissions(manage_guild=True)
    async def timezones(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        """Show timezone for one member or list all members who have one set.

        Args:
            interaction: The Discord interaction.
            user: Optional member to look up; shows the full guild list if omitted.
        """

        def _format_entry(display_name: str, tz_name: str) -> str:
            try:
                tz = pytz.timezone(tz_name)
                current_time = datetime.now(tz).strftime('%H:%M')
            except pytz.UnknownTimeZoneError:
                current_time = '?'
            return f'**{display_name}** — {tz_name} (`{current_time}`)'

        if user is not None:
            db_user = await get_user(user.id)
            tz = db_user.timezone if db_user else None
            embed = discord.Embed(
                title=f'Timezone — {user.display_name}', colour=discord.Colour.blurple()
            )
            embed.description = (
                _format_entry(user.display_name, tz) if tz else '*not set*'
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        member_ids = [m.id for m in interaction.guild.members if not m.bot]
        db_users = await get_users_by_ids(member_ids)

        if not db_users:
            await interaction.followup.send(
                'No guild members have a timezone set.', ephemeral=True
            )
            return

        id_to_member = {m.id: m for m in interaction.guild.members}
        lines = []
        for u in db_users:
            member = id_to_member.get(u.id)
            display = member.display_name if member else u.name
            lines.append(_format_entry(display, u.timezone))

        embed = discord.Embed(
            title='Member timezones',
            description='\n'.join(lines),
            colour=discord.Colour.blurple(),
        )
        embed.set_footer(text=f'{len(lines)} member(s) with timezone set')
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.describe(
        time='Please input a time in any suitable format in your region'
    )
    @app_commands.describe(
        date='Please input a date in any suitable format in your region'
    )
    @app_commands.command(
        name='get-timestamp',
        description='Get formatted timestamp for any date and/or time',
    )
    async def get_timestamp(
        self,
        interaction: discord.Interaction,
        time: str = '',
        date: str = '',
    ) -> None:
        """Get formatted timestamp for any date and/or time."""
        user = await require_timezone(self.bot, interaction)
        if user is None:
            return
        time_date = parse_and_validate(
            timezone=user.timezone,
            date=date,
            time=time,
            interaction=interaction,
        )
        if time_date is None:
            await interaction.response.send_message(
                'You sent a date/time in incorrect format',
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            'Select format',
            view=TimestampFormatView(int(time_date.timestamp())),
            ephemeral=True,
        )
