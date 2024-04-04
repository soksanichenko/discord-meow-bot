"""Main module of the bot"""

from __future__ import annotations

import asyncio

import discord
from discord import utils
from discord.ext.commands import Bot

from sources.config import config
from sources.lib.commands.get_timestamp import (
    parse_and_validate,
    autocomplete_timezone,
)
from sources.lib.commands.get_timestamp import TimestampFormatView
from sources.lib.commands.utils import get_command
from sources.lib.core import BotAvatar
from sources.lib.db.operations.guilds import add_guild
from sources.lib.db.operations.users import add_user, get_user_timezone
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


@bot.tree.command(
    name='set-timezone',
    description='Set a current timezone of user',
)
@discord.app_commands.autocomplete(timezone=autocomplete_timezone)
async def set_timezone(
    interaction: discord.Interaction,
    timezone: str,
):
    """
    Set a current timezone of user
    :param interaction: the command's interaction
    :param timezone: a current timezone of user
    :return: None
    """
    user = interaction.user
    await add_user(
        discord_user=user,
        user_timezone=timezone,
    )
    await interaction.response.send_message(
        f'Timezone for user **{user.display_name}** is set to **{timezone}**',
        ephemeral=True,
    )


@discord.app_commands.describe(
    time='Please input a time in any suitable format in your region'
)
@discord.app_commands.describe(
    date='Please input a date in any suitable format in your region'
)
@bot.tree.command(
    name='get-timestamp',
    description='Get formatted timestamp for any date and/or time',
)
async def get_timestamp(
    interaction: discord.Interaction,
    time: str = '',
    date: str = '',
):
    """
    Send any text by the bot
    :param time: an input time for converting
    :param date: an input date for converting
    :param interaction: the command's interaction
    :return: None
    """
    user = interaction.user
    timezone = await get_user_timezone(
        discord_user=user,
    )
    command_name = 'set-timezone'
    command = await get_command(commands_tree=bot.tree, command_name=command_name)
    if timezone is None:
        await interaction.response.send_message(
            f'User **{user.display_name}** does not have a timezone.\n'
            f'Please, use command </{command_name}:{command.id}> to set it'
        )
        return
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
    if message.mention_everyone:
        content = f'@silent {content}'
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
    utils.setup_logging()
    await bot.start(
        token=config.discord_token,
        reconnect=True,
    )


if __name__ == '__main__':
    asyncio.run(main())
