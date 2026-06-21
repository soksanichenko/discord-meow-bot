"""Auto-responder cog — reply automatically when a configured user is mentioned."""

from __future__ import annotations

from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from sources.lib.db.operations.auto_responder import (
    delete_auto_responder,
    delete_expired_auto_responders,
    get_auto_responder,
    list_auto_responders,
    upsert_auto_responder,
)
from sources.lib.db.operations.guilds import upsert_guild
from sources.lib.db.operations.users import get_user
from sources.lib.utils.logger import Logger
from sources.lib.views.reminders import parse_when

_COOLDOWN_SECONDS = 300
_MAX_RESPONSE_LEN = 500


class AutoResponderCog(commands.Cog):
    """Commands and listener for per-user auto-responses on mentions."""

    group = app_commands.Group(
        name='auto-responder',
        description='Manage your automatic reply when someone mentions you',
    )

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise the cog.

        Args:
            bot: The Discord bot instance.
        """
        self.bot = bot
        self.logger = Logger()
        # (guild_id, user_id) -> last fire time; reset on restart (acceptable for 5-min cooldown)
        self._cooldowns: dict[tuple[int, int], datetime] = {}

    async def cog_load(self) -> None:
        """Start the expired-responder cleanup loop."""
        self._cleanup_expired.start()

    def cog_unload(self) -> None:
        """Stop the cleanup loop."""
        self._cleanup_expired.cancel()

    @tasks.loop(hours=1)
    async def _cleanup_expired(self) -> None:
        """Delete auto-responders whose expiry time has passed."""
        count = await delete_expired_auto_responders()
        if count:
            self.logger.info('Removed %d expired auto-responders', count)

    @_cleanup_expired.before_loop
    async def _before_cleanup(self) -> None:
        await self.bot.wait_until_ready()

    @group.command(
        name='set',
        description='Set an automatic reply when someone mentions you',
    )
    @app_commands.describe(
        response='Message the bot will send when you are mentioned (max 500 characters)',
        expires='When this responder expires, e.g. "in 7 days", "1 jan 2027" (optional)',
    )
    async def set_responder(
        self,
        interaction: discord.Interaction,
        response: str,
        expires: str | None = None,
    ) -> None:
        """Create or update the caller's auto-responder for this guild.

        Args:
            interaction: The Discord interaction.
            response: Text the bot will send when the caller is mentioned.
            expires: Optional natural language expiry string.
        """
        if len(response) > _MAX_RESPONSE_LEN:
            await interaction.response.send_message(
                f'Response is too long ({len(response)} chars). Maximum is {_MAX_RESPONSE_LEN}.',
                ephemeral=True,
            )
            return

        expires_at: datetime | None = None
        if expires is not None:
            db_user = await get_user(interaction.user.id)
            timezone_str = db_user.timezone if db_user else None
            expires_at = parse_when(expires, timezone_str)
            if expires_at is None:
                await interaction.response.send_message(
                    "I couldn't understand that expiry time. "
                    'Try something like `in 7 days`, `1 jan 2027`, or `next monday`.',
                    ephemeral=True,
                )
                return
            if expires_at <= datetime.now(tz=UTC):
                await interaction.response.send_message(
                    'The expiry time must be in the future.',
                    ephemeral=True,
                )
                return

        await upsert_guild(
            guild_id=interaction.guild_id, guild_name=interaction.guild.name
        )
        await upsert_auto_responder(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            response_text=response,
            expires_at=expires_at,
        )
        self.logger.info(
            'Auto-responder set for user %d in guild %d',
            interaction.user.id,
            interaction.guild_id,
        )

        expiry_note = (
            f' (expires {discord.utils.format_dt(expires_at, style="f")})'
            if expires_at
            else ''
        )
        await interaction.response.send_message(
            f'Your auto-responder has been set{expiry_note}.',
            ephemeral=True,
        )

    @group.command(name='remove', description='Remove your automatic reply')
    async def remove_responder(self, interaction: discord.Interaction) -> None:
        """Remove the caller's auto-responder for this guild.

        Args:
            interaction: The Discord interaction.
        """
        deleted = await delete_auto_responder(
            guild_id=interaction.guild_id, user_id=interaction.user.id
        )
        if not deleted:
            await interaction.response.send_message(
                "You don't have an active auto-responder on this server.",
                ephemeral=True,
            )
            return

        self._cooldowns.pop((interaction.guild_id, interaction.user.id), None)
        self.logger.info(
            'Auto-responder removed for user %d in guild %d',
            interaction.user.id,
            interaction.guild_id,
        )
        await interaction.response.send_message(
            'Your auto-responder has been removed.',
            ephemeral=True,
        )

    @group.command(
        name='list', description='Show all active auto-responders on this server'
    )
    async def list_responders(self, interaction: discord.Interaction) -> None:
        """List active auto-responders for this guild.

        Args:
            interaction: The Discord interaction.
        """
        responders = await list_auto_responders(guild_id=interaction.guild_id)
        if not responders:
            await interaction.response.send_message(
                'No active auto-responders on this server.',
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title='Active auto-responders',
            colour=discord.Colour.blurple(),
        )
        for r in responders:
            expiry = (
                discord.utils.format_dt(r.expires_at, style='f')
                if r.expires_at
                else 'Never'
            )
            snippet = r.response_text[:100] + (
                '...' if len(r.response_text) > 100 else ''
            )
            embed.add_field(
                name=f'<@{r.user_id}>',
                value=f'{snippet}\nExpires: {expiry}',
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Send an auto-response when a configured user is mentioned.

        Args:
            message: The incoming Discord message.
        """
        if (
            message.guild is None
            or message.author == self.bot.user
            or not message.mentions
        ):
            return

        now = datetime.now(tz=UTC)
        for mentioned_user in message.mentions:
            cooldown_key = (message.guild.id, mentioned_user.id)
            last_fired = self._cooldowns.get(cooldown_key)
            if last_fired and (now - last_fired).total_seconds() < _COOLDOWN_SECONDS:
                continue

            responder = await get_auto_responder(
                guild_id=message.guild.id, user_id=mentioned_user.id
            )
            if responder is None:
                continue

            self._cooldowns[cooldown_key] = now
            await message.channel.send(responder.response_text)
            self.logger.info(
                'Auto-responder fired for user %d in guild %d',
                mentioned_user.id,
                message.guild.id,
            )
