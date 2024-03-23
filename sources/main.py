"""Main module of the bot"""

from __future__ import annotations

import asyncio

import discord
from discord import utils
from discord.ext.commands import Bot
from discord.utils import MISSING

from sources.config import config
from sources.lib.commands.get_timestamp import (
    parse_and_validate,
    autocomplete_timezone,
)
from sources.lib.commands.get_timestamp import TimestampFormatView
from sources.lib.core import BotAvatar
from sources.lib.db import create_db_if_not_exists
from sources.lib.db.utils import add_guild
from sources.lib.on_message.domains_fixer import fix_urls
from sources.lib.utils import Logger

intents = discord.Intents.default()
# access to a message content
intents.message_content = True
bot = Bot(command_prefix='/', intents=intents)


@bot.tree.command(
    name='ping',
    description='Test ping command',
)
async def ping(interaction: discord.Interaction):
    """
    Test ping command
    :param interaction: the command's interaction
    :return: None
    """
    await interaction.response.send_message('pong', ephemeral=True)


@discord.app_commands.describe(
    time='Please input a time in any suitable format in your region'
)
@discord.app_commands.describe(
    date='Please input a date in any suitable format in your region'
)
@discord.app_commands.autocomplete(timezone=autocomplete_timezone)
@bot.tree.command(
    name='get-timestamp',
    description='Get formatted timestamp for any date and/or time',
)
async def get_timestamp(
    interaction: discord.Interaction,
    timezone: str,
    time: str = '',
    date: str = '',
):
    """
    Send any text by the bot
    :param timezone: a current user's timezone
    :param time: an input time for converting
    :param date: an input date for converting
    :param interaction: the command's interaction
    :return: None
    """
    time_date = parse_and_validate(
        timezone=timezone,
        date=date,
        time=time,
        interaction=interaction,
    )
    if time_date is None:
        await interaction.response.send_message(
            'You sent a date/time in incorrect format',
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        'Select format',
        view=TimestampFormatView(int(time_date.timestamp())),
        ephemeral=True,
    )


@bot.listen('on_message')
async def process_links_in_message(message: discord.Message):
    """
    Process links in a new message
    :param message: a new message posted in Discord
    :return: None
    """

    Logger().info('Get message from %s', message.author.name)
    if message.author == bot.user:
        Logger().info('That message is mine')
        return
    content = fix_urls(message=message)
    if content == message.content:
        Logger().info('The original message is already fine')
        return
    await message.channel.send(content=content)
    await message.delete()


@bot.listen('on_ready')
@bot.listen('on_resumed')
async def start_bot_staff():
    """
    Sync a tree of the commands then a client is resumed
    :return:None
    """
    for guild in bot.guilds:
        await add_guild(discord_guild=guild)
    await bot.tree.sync()
    Logger().info('Syncing is completed')
    await bot.user.edit(avatar=BotAvatar())
    Logger().info('An avatar of the bot is changed')
    game = discord.Game('Rolling the balls of wool')
    await bot.change_presence(status=discord.Status.dnd, activity=game)
    Logger().info('A status of the bot is changed')


async def main():
    """Main run function"""
    utils.setup_logging(
        handler=MISSING,
        formatter=MISSING,
        level=MISSING,
        root=False,
    )
    Logger().info('Create DB if not exists')
    await create_db_if_not_exists()
    await bot.start(
        token=config.discord_token,
        reconnect=True,
    )


if __name__ == '__main__':
    asyncio.run(main())
