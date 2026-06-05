"""Admin cog"""

import time

import discord
from discord import app_commands
from discord.ext import commands
from prometheus_client import REGISTRY

from sources.lib.utils.logger import Logger


def _collect_samples() -> dict[str, list]:
    """Return all non-_created Prometheus samples keyed by sample name."""
    result: dict[str, list] = {}
    for family in REGISTRY.collect():
        for s in family.samples:
            if not s.name.endswith('_created'):
                result.setdefault(s.name, []).append(s)
    return result


def _counter(samples: dict, name: str, **labels: str) -> int:
    """Return the integer value of a counter sample matching the given labels."""
    for s in samples.get(name, []):
        if all(s.labels.get(k) == v for k, v in labels.items()):
            return int(s.value)
    return 0


def _gauge(samples: dict, name: str, **labels: str) -> float | None:
    """Return the float value of a gauge sample matching the given labels, or None."""
    for s in samples.get(name, []):
        if all(s.labels.get(k) == v for k, v in labels.items()):
            return s.value
    return None


def _fmt_ago(ts: float | None) -> str:
    """Format a Unix timestamp as a human-readable 'X ago' string."""
    if ts is None:
        return 'never'
    delta = int(time.time() - ts)
    if delta < 60:
        return f'{delta}s ago'
    return f'{delta // 60}m ago'


class AdminCog(commands.Cog):
    """Admin commands cog."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(
        name='sync-tree',
        description='Sync a tree of the commands',
    )
    async def sync_tree(self, context: commands.Context) -> None:
        """Sync a tree of the commands."""
        if await self.bot.is_owner(context.author):
            await self.bot.tree.sync()
            message = 'Syncing is completed'
        else:
            message = 'You are not an owner of the bot'
        Logger().info(message)
        await context.reply(message)

    @app_commands.command(name='bot-stats', description='Show bot metrics (owner only)')
    async def bot_stats(self, interaction: discord.Interaction) -> None:
        """Display live bot metrics as a Discord embed.

        Args:
            interaction: The Discord interaction.
        """
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message('Owner only.', ephemeral=True)
            return

        samples = _collect_samples()

        embed = discord.Embed(title='Bot stats', colour=discord.Colour.blurple())

        embed.add_field(
            name='Bot',
            value=(
                f'Latency: **{round(self.bot.latency * 1000, 1)} ms** · '
                f'Guilds: **{len(self.bot.guilds)}** · '
                f'Members: **{len(self.bot.users):,}**'
            ),
            inline=False,
        )

        tg_posts = _counter(
            samples, 'relay_posts_total', service='telegram', type='post'
        )
        tg_errors = _counter(samples, 'relay_fetch_errors_total', service='telegram')
        tg_last = _gauge(samples, 'relay_last_poll_timestamp', service='telegram')
        embed.add_field(
            name='Telegram relay (since restart)',
            value=(
                f'Posts: **{tg_posts}** · '
                f'Errors: **{tg_errors}** · '
                f'Last poll: {_fmt_ago(tg_last)}'
            ),
            inline=False,
        )

        yt_videos = _counter(
            samples, 'relay_posts_total', service='youtube', type='video'
        )
        yt_shorts = _counter(
            samples, 'relay_posts_total', service='youtube', type='short'
        )
        yt_lives = _counter(
            samples, 'relay_posts_total', service='youtube', type='live'
        )
        yt_errors = _counter(samples, 'relay_fetch_errors_total', service='youtube')
        yt_last = _gauge(samples, 'relay_last_poll_timestamp', service='youtube')
        embed.add_field(
            name='YouTube relay (since restart)',
            value=(
                f'Videos: **{yt_videos}** · Shorts: **{yt_shorts}** · Lives: **{yt_lives}**\n'
                f'Errors: **{yt_errors}** · Last poll: {_fmt_ago(yt_last)}'
            ),
            inline=False,
        )

        fixes = _counter(samples, 'domain_fixes_total')
        cmd_err_samples = [
            s for s in samples.get('command_errors_total', []) if s.value > 0
        ]
        cmd_err_str = (
            ' · '.join(
                f'{s.labels.get("command", "?")}={int(s.value)}'
                for s in cmd_err_samples
            )
            or 'none'
        )
        embed.add_field(
            name='Other (since restart)',
            value=f'Domain fixes: **{fixes}**\nCommand errors: {cmd_err_str}',
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)
