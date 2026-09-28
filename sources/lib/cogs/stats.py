"""Stats cog — per-guild message count statistics and leaderboard."""

from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from sources.lib.db.operations.stats import (
    get_all_channel_progress,
    get_leaderboard,
    upsert_import_job,
)
from sources.lib.stats_tracker import StatsTracker
from sources.lib.utils.logger import Logger

_FLUSH_INTERVAL_SECONDS = 30


class _ResetConfirmView(discord.ui.View):
    """Ephemeral confirmation for /stats reset."""

    def __init__(self, tracker: StatsTracker) -> None:
        """Initialise the view.

        Args:
            tracker: The stats tracker that performs the reset.
        """
        super().__init__(timeout=60)
        self.tracker = tracker

    @discord.ui.button(label='Reset statistics', style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Wipe the guild's statistics and start rebuilding them.

        Args:
            interaction: The button interaction.
            button: The pressed button.
        """
        self.stop()
        # Answer first: the reset can wait for a flush and outlast the 3 s deadline.
        await interaction.response.edit_message(content='Resetting…', view=None)
        await self.tracker.reset(interaction.guild)
        await interaction.edit_original_response(
            content='Statistics wiped. Rebuilding from history in the background; '
            'check `/stats import-status` for progress.'
        )


class StatsCog(commands.Cog):
    """Message statistics commands; counting is done by StatsTracker."""

    stats = app_commands.Group(
        name='stats', description='Message statistics and leaderboard'
    )

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise the cog.

        Args:
            bot: The Discord bot instance.
        """
        self.bot = bot
        self.logger = Logger()
        self.tracker = StatsTracker()

    async def cog_load(self) -> None:
        """Start the periodic flush of live counts."""
        self._flush_buffer.start()

    async def cog_unload(self) -> None:
        """Stop background work and write out what can be written."""
        self._flush_buffer.cancel()
        await self.tracker.close()

    @tasks.loop(seconds=_FLUSH_INTERVAL_SECONDS)
    async def _flush_buffer(self) -> None:
        """Periodically write buffered message counts to the database."""
        await self.tracker.flush()

    @_flush_buffer.before_loop
    async def _before_flush(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener('on_message')
    async def on_message(self, message: discord.Message) -> None:
        """Count a live message.

        Args:
            message: The incoming Discord message.
        """
        self.tracker.record(message)

    @commands.Cog.listener('on_ready')
    async def on_ready(self) -> None:
        """Start a counting epoch: catch up on downtime, resume imports."""
        await self.tracker.on_ready(self.bot.guilds)

    @commands.Cog.listener('on_guild_join')
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Start counting a newly joined guild.

        Args:
            guild: The guild the bot joined.
        """
        self.tracker.on_guild_join(guild)

    @commands.Cog.listener('on_guild_remove')
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Stop all stats work for a guild the bot has left.

        Args:
            guild: The guild the bot left.
        """
        await self.tracker.forget(guild.id)

    @stats.command(
        name='leaderboard', description='Show top message senders in this server'
    )
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        """Display the message count leaderboard for this guild.

        Args:
            interaction: The Discord interaction.
        """
        rows = await get_leaderboard(interaction.guild_id, limit=10)
        if not rows:
            await interaction.response.send_message(
                'No statistics yet. An admin can run `/stats import` to load message history.',
                ephemeral=True,
            )
            return

        embed = discord.Embed(title='Message Leaderboard', colour=discord.Colour.gold())
        lines = []
        for i, row in enumerate(rows, start=1):
            member = interaction.guild.get_member(row.user_id)
            if member:
                name = member.display_name
            else:
                try:
                    user = await self.bot.fetch_user(row.user_id)
                    name = user.display_name
                except discord.NotFound:
                    name = f'Unknown ({row.user_id})'
            lines.append(f'{i}. **{name}** — {row.message_count:,}')
        embed.description = '\n'.join(lines)
        await interaction.response.send_message(embed=embed)

    @stats.command(
        name='import', description='Import message history to build statistics'
    )
    @app_commands.describe(
        since='Only import messages from this date forward (YYYY-MM-DD).'
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def import_history(
        self,
        interaction: discord.Interaction,
        since: str | None = None,
    ) -> None:
        """Start or resume the background history import for this guild.

        Args:
            interaction: The Discord interaction.
            since: Optional ISO date (YYYY-MM-DD) limiting how far back the import goes.
        """
        if self.tracker.is_importing(interaction.guild_id):
            await interaction.response.send_message(
                'An import is already running. Check progress with `/stats import-status`.',
                ephemeral=True,
            )
            return

        since_dt: datetime | None = None
        if since:
            try:
                since_dt = datetime.strptime(since, '%Y-%m-%d').replace(tzinfo=UTC)
            except ValueError:
                await interaction.response.send_message(
                    'Invalid date format. Use YYYY-MM-DD, e.g. `2023-01-01`.',
                    ephemeral=True,
                )
                return

        await upsert_import_job(interaction.guild_id, since_dt)
        await self.tracker.request_import(interaction.guild, since_dt)
        await interaction.response.send_message(
            'Import started in the background. Use `/stats import-status` to check progress.',
            ephemeral=True,
        )

    @stats.command(
        name='import-status', description='Show message history import progress'
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def import_status(self, interaction: discord.Interaction) -> None:
        """Show the state of the history import and downtime catch-up.

        Args:
            interaction: The Discord interaction.
        """
        guild_id = interaction.guild_id
        progress_rows = await get_all_channel_progress(guild_id)
        readable_channels = sum(
            1
            for ch in interaction.guild.text_channels
            if ch.permissions_for(interaction.guild.me).read_message_history
        )
        completed = sum(1 for r in progress_rows if r.is_completed)
        catching_up = self.tracker.catching_up_count(guild_id)

        embed = discord.Embed(title='Import Status', colour=discord.Colour.blurple())
        embed.add_field(
            name='Running',
            value='Yes' if self.tracker.is_importing(guild_id) else 'No',
            inline=True,
        )
        embed.add_field(
            name='Progress',
            value=f'{completed} / {readable_channels} channels done',
            inline=True,
        )
        if catching_up:
            embed.add_field(
                name='Catching up', value=f'{catching_up} channel(s)', inline=True
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @stats.command(
        name='reset', description='Wipe statistics and rebuild them from history'
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reset(self, interaction: discord.Interaction) -> None:
        """Ask for confirmation before wiping this guild's statistics.

        Args:
            interaction: The Discord interaction.
        """
        await interaction.response.send_message(
            'This deletes all message statistics for this server and rebuilds them '
            'from channel history. Continue?',
            view=_ResetConfirmView(self.tracker),
            ephemeral=True,
        )
