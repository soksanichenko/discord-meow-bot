"""Main module of the bot"""

import asyncio
import json

import discord
from aiohttp import web
from discord import utils
from discord.ext.commands import Bot

from sources.config import config
from sources.lib.cogs.admin import AdminCog
from sources.lib.cogs.birthdays import BirthdaysCog
from sources.lib.cogs.domain_fixer import DomainFixerCog
from sources.lib.cogs.guild import GuildCog
from sources.lib.cogs.messages import MessagesCog
from sources.lib.cogs.music_links import MusicLinksCog
from sources.lib.cogs.reminders import RemindersCog
from sources.lib.cogs.stats import StatsCog
from sources.lib.cogs.telegram_relay import TelegramRelayCog
from sources.lib.cogs.user import UserCog
from sources.lib.cogs.voice import VoiceCog
from sources.lib.cogs.youtube_relay import YouTubeRelayCog


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


class MeowBot(Bot):
    """Discord bot."""

    async def setup_hook(self) -> None:
        """Load all cogs before bot starts."""
        await self.add_cog(AdminCog(self))
        await self.add_cog(BirthdaysCog(self))
        await self.add_cog(DomainFixerCog(self))
        await self.add_cog(GuildCog(self))
        await self.add_cog(MessagesCog(self))
        await self.add_cog(MusicLinksCog(self))
        await self.add_cog(RemindersCog(self))
        await self.add_cog(StatsCog(self))
        await self.add_cog(TelegramRelayCog(self))
        await self.add_cog(UserCog(self))
        await self.add_cog(YouTubeRelayCog(self))
        await self.add_cog(VoiceCog(self))


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
