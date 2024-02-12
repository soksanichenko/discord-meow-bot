"""Main module of the bot"""
import asyncio
import logging
import os
from collections import defaultdict
from copy import copy
from dataclasses import dataclass
from tldextract import extract
from tldextract.tldextract import ExtractResult
from typing import Optional
from urllib.parse import urlparse, ParseResult

import discord
from discord.ext.commands import Bot

intents = discord.Intents.default()
# access to a message content
intents.message_content = True
bot = Bot(command_prefix="", intents=intents)

logger = logging.getLogger('discord')


@dataclass
class DomainFixer:
    fixer: str
    second_fixer: Optional[str] = None
    second_domain: Optional[str] = None


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


def fix_url(msg_content: str, url: str) -> tuple[str, str]:
    """
    Fix a URL by replacing an original domain by a fixer
    :param msg_content: content of an original message
    :param url: an original parsed URL
    :return: a fixed URL
    """
    domains = defaultdict(lambda: DomainFixer(
        fixer='',
    ), **{
        'reddit.com': DomainFixer(
            fixer='rxddit',
        ),
        'tiktok.com': DomainFixer(
            fixer='vxtiktok',
        ),
        'twitter.com': DomainFixer(
            fixer='fxtwitter',
            second_fixer='fixupx',
            second_domain='x',
        ),
        'x.com': DomainFixer(
            fixer='fixupx',
            second_fixer='fxtwitter',
            second_domain='twitter',
        ),
    })
    parsed_url = urlparse(url)
    parsed_domain = extract(parsed_url.netloc)
    fixer = domains[parsed_domain.registered_domain]
    if url in msg_content:
        fixed_domain = fixer.fixer
        replaced_url = url
    else:
        fixed_domain = fixer.second_fixer
        replaced_url = ParseResult(
            parsed_url.scheme,
            netloc=ExtractResult(
                subdomain=parsed_domain.subdomain,
                domain=fixer.second_domain,
                suffix=parsed_domain.suffix,
                is_private=parsed_domain.is_private,
            ).fqdn,
            path=parsed_url.path,
            query=parsed_url.query,
            params=parsed_url.params,
            fragment=parsed_url.fragment,
        ).geturl()
    if not fixer.fixer:
        return url, url
    return replaced_url, ParseResult(
        parsed_url.scheme,
        netloc=ExtractResult(
            subdomain=parsed_domain.subdomain,
            domain=fixed_domain,
            suffix=parsed_domain.suffix,
            is_private=parsed_domain.is_private,
        ).fqdn,
        path=parsed_url.path,
        query=parsed_url.query,
        params=parsed_url.params,
        fragment=parsed_url.fragment,
    ).geturl()


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
    final_urls = dict(
        fix_url(
            msg_content=message.content,
            url=embed.url,
        ) for embed in message.embeds
    )
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
