"""Admin cog"""

from discord.ext import commands

from sources.lib.utils import Logger


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
