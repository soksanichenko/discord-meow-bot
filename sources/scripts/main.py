"""Main module of the bot"""

import asyncio
import hashlib
import json
import pathlib

import discord
from aiohttp import web
from discord import utils
from discord.ext.commands import Bot

from sources.config import config
from sources.lib.cogs.admin import AdminCog
from sources.lib.cogs.birthdays import BirthdaysCog
from sources.lib.cogs.domain_fixer import DomainFixerCog
from sources.lib.cogs.events import EventsCog
from sources.lib.cogs.guild import GuildCog
from sources.lib.cogs.help import HelpCog
from sources.lib.cogs.messages import MessagesCog
from sources.lib.cogs.music_links import MusicLinksCog
from sources.lib.cogs.reminders import RemindersCog
from sources.lib.cogs.stats import StatsCog
from sources.lib.cogs.telegram_relay import TelegramRelayCog
from sources.lib.cogs.twitch_relay import TwitchRelayCog
from sources.lib.cogs.user import UserCog
from sources.lib.cogs.voice import VoiceCog
from sources.lib.cogs.youtube_relay import YouTubeRelayCog
from sources.lib.utils import Logger


async def _health_handler(request: web.Request) -> web.Response:
    """Return bot readiness and WebSocket latency."""
    bot_instance: MeowBot = request.app['bot']
    if not bot_instance.is_ready():
        return web.Response(
            status=503,
            content_type='application/json',
            text=json.dumps({'status': 'starting'}),
        )
    return web.Response(
        content_type='application/json',
        text=json.dumps(
            {'status': 'ok', 'latency_ms': round(bot_instance.latency * 1000, 1)}
        ),
    )


async def _start_health_server(bot_instance: 'MeowBot') -> None:
    """Start the health HTTP server in the background."""
    app = web.Application()
    app['bot'] = bot_instance
    app.router.add_get('/health', _health_handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', config.health_port).start()


intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.members = True
intents.presences = True
intents.guild_scheduled_events = True


_SYNC_HASH_PATH = pathlib.Path('/tmp/.discord_sync_hash')
_logger = Logger()


class MeowBot(Bot):
    """Discord bot."""

    async def on_tree_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        """Log all unhandled app-command errors through the main discord logger."""
        cmd = interaction.command.name if interaction.command else '<unknown>'
        _logger.error(
            'App command error in /%s: %s: %s',
            cmd,
            type(error).__name__,
            error,
            exc_info=error,
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(
                'An internal error occurred.', ephemeral=True
            )

    async def setup_hook(self) -> None:
        """Load all cogs and sync the slash command tree with Discord if it changed."""
        self.tree.on_error = self.on_tree_error
        await self.add_cog(AdminCog(self))
        await self.add_cog(EventsCog(self))
        await self.add_cog(HelpCog(self))
        await self.add_cog(BirthdaysCog(self))
        await self.add_cog(DomainFixerCog(self))
        await self.add_cog(GuildCog(self))
        await self.add_cog(MessagesCog(self))
        await self.add_cog(MusicLinksCog(self))
        await self.add_cog(RemindersCog(self))
        await self.add_cog(StatsCog(self))
        await self.add_cog(TelegramRelayCog(self))
        await self.add_cog(TwitchRelayCog(self))
        await self.add_cog(UserCog(self))
        await self.add_cog(YouTubeRelayCog(self))
        await self.add_cog(VoiceCog(self))
        await self._sync_command_tree()

    async def _sync_command_tree(self) -> None:
        """Sync slash commands with Discord only when the tree has changed.

        Computes a hash of the current command payloads and compares it with the
        last synced hash stored in a temp file. Skips the API call if unchanged.
        """
        current_hash = self._hash_command_tree()
        stored_hash = (
            _SYNC_HASH_PATH.read_text().strip() if _SYNC_HASH_PATH.exists() else ''
        )

        if current_hash == stored_hash:
            _logger.info('Command tree unchanged, skipping sync')
            return

        await self.tree.sync()
        _SYNC_HASH_PATH.write_text(current_hash)
        _logger.info('Command tree synced')

    def _hash_command_tree(self) -> str:
        """Compute a SHA-1 hash of the current command tree structure.

        Captures names, descriptions, subcommands, and parameter signatures.
        """

        def serialize(
            cmd: discord.app_commands.Command
            | discord.app_commands.Group
            | discord.app_commands.ContextMenu,
        ) -> dict:
            if isinstance(cmd, discord.app_commands.ContextMenu):
                return {'name': cmd.name, 'type': 'context_menu'}
            base: dict = {'name': cmd.name, 'description': cmd.description}
            if isinstance(cmd, discord.app_commands.Group):
                base['commands'] = sorted(
                    [serialize(c) for c in cmd.commands], key=lambda x: x['name']
                )
            else:
                base['params'] = sorted(
                    [{'name': p.name, 'required': p.required} for p in cmd.parameters],
                    key=lambda x: x['name'],
                )
            return base

        payload = sorted(
            [serialize(cmd) for cmd in self.tree.get_commands()],
            key=lambda x: x['name'],
        )
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()


bot = MeowBot(
    command_prefix='?',
    intents=intents,
    activity=discord.Game('Rolling the balls of wool'),
    status=discord.Status.online,
)


async def main():
    """Main run function."""
    utils.setup_logging()
    await _start_health_server(bot)
    await bot.start(token=config.discord_token, reconnect=True)


if __name__ == '__main__':
    asyncio.run(main())
