"""Stats cog — per-guild message count statistics and leaderboard."""

import asyncio
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from sources.lib.db.operations.stats import (
    get_all_channel_progress,
    get_channel_progress,
    get_guilds_with_incomplete_import,
    get_leaderboard,
    increment_message_counts,
    save_channel_progress,
)
from sources.lib.utils import Logger

_CHECKPOINT_EVERY = 500


class StatsCog(commands.Cog):
    """Message statistics commands and realtime on_message listener."""

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
        self._import_tasks: dict[int, asyncio.Task] = {}

    async def cog_load(self) -> None:
        """Resume any imports that were in progress when the bot last stopped."""
        guild_ids = await get_guilds_with_incomplete_import()
        for guild_id in guild_ids:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            self.logger.info('Stats import: auto-resuming for guild %s', guild.name)
            self._import_tasks[guild_id] = asyncio.create_task(
                self._run_import(guild, since_dt=None)
            )

    @commands.Cog.listener('on_message')
    async def on_message(self, message: discord.Message) -> None:
        """Increment the sender's message count for every non-bot guild message.

        Args:
            message: The incoming Discord message.
        """
        if message.author.bot or message.guild is None:
            return
        await increment_message_counts(message.guild.id, {message.author.id: 1})

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
        since='Only import messages from this date forward (YYYY-MM-DD). Ignored when resuming.'
    )
    @app_commands.default_permissions(manage_guild=True)
    async def import_history(
        self,
        interaction: discord.Interaction,
        since: str | None = None,
    ) -> None:
        """Start a background historical message import for this guild.

        Args:
            interaction: The Discord interaction.
            since: Optional ISO date (YYYY-MM-DD) limiting how far back the import goes.
        """
        guild_id = interaction.guild_id
        running_task = self._import_tasks.get(guild_id)
        if running_task and not running_task.done():
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

        self._import_tasks[guild_id] = asyncio.create_task(
            self._run_import(interaction.guild, since_dt)
        )
        await interaction.response.send_message(
            'Import started in the background. Use `/stats import-status` to check progress.',
            ephemeral=True,
        )

    @stats.command(
        name='import-status', description='Show message history import progress'
    )
    @app_commands.default_permissions(manage_guild=True)
    async def import_status(self, interaction: discord.Interaction) -> None:
        """Show the current state of the historical import for this guild.

        Args:
            interaction: The Discord interaction.
        """
        guild_id = interaction.guild_id
        running_task = self._import_tasks.get(guild_id)
        running = running_task is not None and not running_task.done()
        progress_rows = await get_all_channel_progress(interaction.guild_id)

        readable_channels = sum(
            1
            for ch in interaction.guild.text_channels
            if ch.permissions_for(interaction.guild.me).read_message_history
        )
        completed = sum(1 for r in progress_rows if r.is_completed)
        in_progress = sum(1 for r in progress_rows if not r.is_completed)

        embed = discord.Embed(title='Import Status', colour=discord.Colour.blurple())
        embed.add_field(name='Running', value='Yes' if running else 'No', inline=True)
        embed.add_field(
            name='Progress',
            value=f'{completed} / {readable_channels} channels done',
            inline=True,
        )
        if in_progress:
            embed.add_field(
                name='In progress', value=f'{in_progress} channel(s)', inline=True
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _run_import(
        self, guild: discord.Guild, since_dt: datetime | None
    ) -> None:
        """Scan all readable text channels and accumulate per-user message counts.

        Saves a checkpoint to the database every _CHECKPOINT_EVERY messages so the
        import can resume from where it left off if the bot restarts.

        Args:
            guild: The Discord guild to import.
            since_dt: Lower bound for messages (only used for channels with no prior progress).
        """
        text_channels = [
            ch
            for ch in guild.text_channels
            if ch.permissions_for(guild.me).read_message_history
        ]
        self.logger.info(
            'Stats import started for guild %s: %d channels',
            guild.name,
            len(text_channels),
        )

        for channel in text_channels:
            progress = await get_channel_progress(guild.id, channel.id)
            if progress and progress.is_completed:
                continue

            after: discord.Object | datetime | None
            if progress and progress.last_message_id:
                after = discord.Object(id=progress.last_message_id)
            else:
                after = since_dt

            counts: dict[int, int] = {}
            processed = 0
            last_id: int | None = progress.last_message_id if progress else None

            try:
                async for message in channel.history(
                    limit=None, oldest_first=True, after=after
                ):
                    if not message.author.bot:
                        counts[message.author.id] = counts.get(message.author.id, 0) + 1
                    last_id = message.id
                    processed += 1

                    if processed % _CHECKPOINT_EVERY == 0:
                        if counts:
                            await increment_message_counts(guild.id, counts)
                            counts = {}
                        await save_channel_progress(
                            guild.id, channel.id, last_id, False
                        )
                        self.logger.info(
                            'Stats import: %s — checkpoint at %d messages',
                            channel.name,
                            processed,
                        )

                if counts:
                    await increment_message_counts(guild.id, counts)
                await save_channel_progress(guild.id, channel.id, last_id, True)
                self.logger.info(
                    'Stats import: channel %s done (%d messages)',
                    channel.name,
                    processed,
                )

            except discord.Forbidden:
                self.logger.warning(
                    'Stats import: no permission for #%s, skipping', channel.name
                )
                await save_channel_progress(guild.id, channel.id, last_id, True)

        self.logger.info('Stats import complete for guild %s', guild.name)
        self._import_tasks.pop(guild.id, None)
