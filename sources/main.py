"""Main module of the bot"""
import logging
import os
from copy import copy
from urllib.parse import urlparse, ParseResult
import dateparser
from tldextract import extract
from tldextract.tldextract import ExtractResult

import discord
from discord.ext.commands import Bot

intents = discord.Intents.default()
# access to a message content
intents.message_content = True
bot = Bot(command_prefix="", intents=intents)

logger = logging.getLogger('discord')


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


class TimestampFormatView(discord.ui.View):
    """
    View class for timestamp formatting
    """

    def __init__(self, timestamp: int):
        self.timestamp = timestamp
        super().__init__()

    @discord.ui.select(
        placeholder='Select format',
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(
                label='F',
                description='Wednesday, 1 January 2021, 23:50'
            ),
            discord.SelectOption(
                label='f',
                description='1 January 2021, 23:50'
            ),
            discord.SelectOption(
                label='D',
                description='1 January 2021'
            ),
            discord.SelectOption(
                label='d',
                description='01.01.2021'
            ),
            discord.SelectOption(
                label='t',
                description='23:50'
            ),
            discord.SelectOption(
                label='T',
                description='23:50:55'
            ),
            discord.SelectOption(
                label='R',
                description='2 hours ago'
            ),
        ]
    )
    async def select_callback(
        self,
        interaction: discord.Interaction,
        select: discord.ui.ChannelSelect,
    ):
        """
        Callback for selecting an option of formatting
        :param interaction: an object of interaction with a user
        :param select: a selected option
        :return: None
        """
        await interaction.response.send_message(
            f'<t:{self.timestamp}:{select.values[0]}>',
            ephemeral=True,
        )


@bot.tree.command(
    name='get-timestamp',
    description='Get formatted timestamp for any date and/or time',
)
@discord.app_commands.describe(
    time='Please input a time in any suitable format in your region'
)
@discord.app_commands.describe(
    date='Please input a date in any suitable format in your region'
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
    time_date = dateparser.parse(
        f'{time} {date}',
        locales=[interaction.locale.value],
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


def fix_urls(message: discord.Message) -> str:
    """
    Fix the URLs by replacing an original domain by a fixer
    :param message: a message from Discord
    :return: a fixed message content
    """
    domains = {
        'reddit.com': 'rxddit',
        'tiktok.com': 'vxtiktok',
        'x.com': 'fixupx',
        'twitter.com': 'fxtwitter',
        'instagram.com': 'ddinstagram'
    }

    msg_content_lines = message.content.split()
    parsed_urls = {
        (parsed_url := urlparse(line)): extract(parsed_url.netloc)
        for line in msg_content_lines
        if line.startswith('http://') or line.startswith('https://')
    }
    if all(
        parsed_domain.registered_domain not in domains
        for parsed_domain in parsed_urls.values()
    ):
        logger.info('No suitable domain or any URL found')
        return message.content
    final_urls = {
        parsed_url.geturl():
            ParseResult(
                parsed_url.scheme,
                netloc=ExtractResult(
                    subdomain=parsed_domain.subdomain,
                    domain=domains[parsed_domain.registered_domain],
                    suffix=parsed_domain.suffix,
                    is_private=parsed_domain.is_private,
                ).fqdn,
                path=parsed_url.path,
                query=parsed_url.query,
                params=parsed_url.params,
                fragment=parsed_url.fragment
            ).geturl()
        for parsed_url, parsed_domain in parsed_urls.items()
    }
    content = copy(message.content)
    for original_url, fixed_url in final_urls.items():
        content = content.replace(original_url, fixed_url)
    content += f'\nOriginal message posted by {message.author.mention}'
    return content


@bot.event
async def on_message(message: discord.Message):
    """
    Process a new message
    :param message: a new message posted in Discord
    :return: None
    """

    logger.info('Get message from %s', message.author.name)
    if message.author == bot.user:
        logger.info('That message is mine')
        return
    content = fix_urls(message=message)
    if content == message.content:
        logger.info('The original message is already fine')
        return
    await message.channel.send(content=content)
    await message.delete()


@bot.event
async def on_ready():
    """
    Sync a tree of the commands
    :return:None
    """
    await bot.tree.sync()
    logger.info('Syncing is completed')


bot.run(os.environ['DISCORD_TOKEN'])
