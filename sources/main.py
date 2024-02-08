"""Main module of the bot"""
import asyncio
import logging
import os
from copy import copy
from urllib.parse import urlparse, ParseResult

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
async def ping(interaction):
    """
    Test ping command
    :param interaction: the command's interaction
    :return: None
    """
    await interaction.response.send_message('pong')


def replace_domain(url: ParseResult):
    """
    Replace an original domain by a fixer
    :param url: an original parsed URL
    :return: a fixed parsed URL
    """
    domains = {
        'reddit.com': 'rxddit.com',
        'www.reddit.com': 'rxddit.com',
        'tiktok.com': 'vxtiktok.com',
        'www.tiktok.com': 'vxtiktok.com',
        'vm.tiktok.com': 'vm.vxtiktok.com',
        'www.vm.tiktok.com': 'vm.vxtiktok.com',
        'twitter.com': 'vxtwitter.com',
        'www.twitter.com': 'vxtwitter.com',
        'instagram.com': 'ddinstagram.com',
        'www.instagram.com': 'ddinstagram.com',
    }
    for key, value in domains.items():
        if url.netloc == key:
            return ParseResult(
                url.scheme,
                netloc=value,
                path=url.path,
                query=url.query,
                params=url.params,
                fragment=url.fragment,
            )
    return url


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
    # An embeds can appear with delay, so we need to wait them
    await asyncio.sleep(1.5)
    if not message.embeds:
        logger.info('The message does not contain embeds')
        return
    processed_urls = (
        replace_domain(urlparse(embed.url)) for embed in message.embeds
    )
    final_urls = {
        embed.url: processed_url.geturl() for processed_url, embed in zip(
            processed_urls,
            message.embeds
        )
    }
    content = copy(message.content)
    for origin_url, final_url in final_urls.items():
        content = content.replace(origin_url, final_url)
    # An embed URL uses original domain even if you replaced it by a fixer.
    # So you need to compare replaced content and original one
    # and do nothing if they are identical.
    if message.content == content:
        logger.info('The original message already fixed')
        return
    content += f'\nOriginal message posted by {message.author.mention}'
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
