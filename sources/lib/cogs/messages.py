"""Messages cog"""

import discord
from discord import app_commands
from discord.ext import commands

from sources.lib.on_message.domains_fixer import fix_urls
from sources.lib.utils import Logger


class MessagesCog(commands.Cog):
    """Message-related commands and listeners."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        ctx_menu = app_commands.ContextMenu(
            name='Remove fixed message',
            callback=self.remove_fixed_message,
        )
        self.bot.tree.add_command(ctx_menu)

    async def cog_unload(self) -> None:
        """Remove context menu on cog unload."""
        self.bot.tree.remove_command(
            'Remove fixed message',
            type=app_commands.AppCommandType.message,
        )

    async def remove_fixed_message(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        """Remove fixed message using a bot's command."""
        if message.author == self.bot.user and message.content.endswith(
            f'\nOriginal message posted by {interaction.user.mention}',
        ):
            await message.delete()
            await interaction.response.send_message(
                'The message is deleted',
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                'That message is not yours',
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Process links in a new message."""
        Logger().info('Get message from %s', message.author.name)
        if message.author == self.bot.user:
            Logger().info('That message is mine')
            return
        content = await fix_urls(message=message)
        if content == message.content:
            Logger().info('The original message is already fine')
            return
        if message.reference is None or message.reference.resolved is None:
            await message.channel.send(content=content, silent=True)
        else:
            await message.reference.resolved.reply(content=content, silent=True)
        await message.delete()
