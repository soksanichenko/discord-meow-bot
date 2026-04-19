"""Main module of the bot"""

import asyncio

import discord
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
from sources.lib.cogs.user import UserCog
from sources.lib.cogs.voice import VoiceCog

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
        await self.add_cog(UserCog(self))
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
    await bot.start(
        token=config.discord_token,
        reconnect=True,
    )


if __name__ == '__main__':
    asyncio.run(main())
