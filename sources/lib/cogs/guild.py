"""Guild cog"""

import discord
import pytz
from discord import Color, app_commands
from discord.ext import commands

from sources.lib.commands.get_timestamp import autocomplete_timezone, role_autocomplete
from sources.lib.db.operations.birthdays import get_guild_settings, upsert_guild_settings
from sources.lib.db.operations.guilds import delete_guild, upsert_guild


class GuildCog(commands.Cog):
    """Guild-related commands and listeners."""

    server = app_commands.Group(
        name='server',
        description='Server configuration',
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @server.command(name='settings', description='View current server configuration')
    @app_commands.default_permissions(manage_guild=True)
    async def server_settings(self, interaction: discord.Interaction) -> None:
        """Display all bot settings configured for this server.

        Args:
            interaction: The Discord interaction.
        """
        settings = await get_guild_settings(interaction.guild_id)

        embed = discord.Embed(title='Server settings', colour=discord.Colour.blurple())

        timezone_value = settings.timezone if settings and settings.timezone else '*not set*'
        embed.add_field(name='Timezone', value=timezone_value, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @server.command(name='timezone-set', description='Set the server timezone')
    @app_commands.describe(timezone='Timezone name, e.g. Europe/Kyiv, America/New_York')
    @app_commands.autocomplete(timezone=autocomplete_timezone)
    @app_commands.default_permissions(manage_guild=True)
    async def timezone_set(
        self,
        interaction: discord.Interaction,
        timezone: str,
    ) -> None:
        """Set the guild-level timezone used as fallback for birthday announcements.

        Args:
            interaction: The Discord interaction.
            timezone: A pytz timezone name.
        """
        try:
            pytz.timezone(timezone)
        except pytz.UnknownTimeZoneError:
            await interaction.response.send_message(
                '**%s** is not a valid timezone.' % timezone,
                ephemeral=True,
            )
            return

        await upsert_guild_settings(interaction.guild_id, timezone=timezone)
        await interaction.response.send_message(
            'Server timezone set to **%s**.' % timezone,
            ephemeral=True,
        )

    @server.command(name='timezone-remove', description='Remove the configured server timezone')
    @app_commands.default_permissions(manage_guild=True)
    async def timezone_remove(self, interaction: discord.Interaction) -> None:
        """Clear the guild-level timezone.

        Args:
            interaction: The Discord interaction.
        """
        await upsert_guild_settings(interaction.guild_id, timezone=None)
        await interaction.response.send_message(
            'Server timezone has been removed.',
            ephemeral=True,
        )

    @server.command(name='info', description='Get info about your server')
    async def info(self, interaction: discord.Interaction) -> None:
        """Get info about your server."""
        guild = interaction.guild
        embed_var = discord.Embed(
            title='Server info',
            description=f'Total information about {guild.name}',
            color=Color.brand_green(),
        )
        if guild.banner:
            embed_var.set_image(url=guild.banner.url)
        embed_var.add_field(name='Owner', value=guild.owner.mention, inline=False)
        embed_var.add_field(
            name='Creation date',
            value=guild.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            inline=False,
        )
        embed_var.add_field(name='Members count', value=guild.member_count, inline=False)
        embed_var.add_field(
            name='Nitro boost',
            value=f'Tier {guild.premium_tier}: '
            f'{guild.premium_subscription_count} boost out of 14',
            inline=False,
        )
        embed_var.add_field(
            name='Bitrate limit',
            value=f'{int(guild.bitrate_limit // 1000)} Kbps',
            inline=False,
        )
        embed_var.add_field(name='Emoji limit', value=guild.emoji_limit, inline=False)
        embed_var.add_field(name='Sticker limit', value=guild.sticker_limit, inline=False)
        embed_var.add_field(
            name='File size limit',
            value=f'{guild.filesize_limit // 1024 // 1024} MB',
            inline=False,
        )
        await interaction.response.send_message(embed=embed_var, ephemeral=True)

    @server.command(name='list-members', description='List of a role members')
    @app_commands.autocomplete(role=role_autocomplete)
    async def list_members(self, interaction: discord.Interaction, role: str) -> None:
        """Print a role members."""
        role_obj = interaction.guild.get_role(int(role))
        if not role_obj:
            await interaction.response.send_message('Role does not found', ephemeral=True)
            return
        members = sorted([member.mention for member in role_obj.members])
        embed = discord.Embed(
            title=f'Members of role "{role_obj.name}"',
            color=discord.Color.blue(),
        )
        if not members:
            embed.description = 'That role has no members'
        else:
            embed.description = '\n'.join(members)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener('on_ready')
    async def on_ready(self) -> None:
        """Sync all current guilds to DB on bot startup."""
        for guild in self.bot.guilds:
            await upsert_guild(guild_id=guild.id, guild_name=guild.name)

    @commands.Cog.listener('on_guild_join')
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Add a guild to DB when the bot joins."""
        await upsert_guild(guild_id=guild.id, guild_name=guild.name)

    @commands.Cog.listener('on_guild_update')
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        """Update a guild in DB when it is updated."""
        await upsert_guild(guild_id=after.id, guild_name=after.name)

    @commands.Cog.listener('on_guild_remove')
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Remove a guild from DB when the bot is kicked or leaves."""
        await delete_guild(guild_id=guild.id)
