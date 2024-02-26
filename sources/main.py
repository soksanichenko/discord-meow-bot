"""Main module of the bot"""
import logging
import os
from copy import copy
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, ParseResult
from tldextract import extract
from tldextract.tldextract import ExtractResult

import discord
from discord.ext.commands import Bot

intents = discord.Intents.default()
# access to a message content
intents.message_content = True
bot = Bot(command_prefix="", intents=intents)

logger = logging.getLogger('discord')


@dataclass
class DomainFixer:
    """
    data-class for a domain fixer
    """
    fixer: str
    second_fixer: Optional[str] = None
    second_domain: Optional[str] = None


# class MyView(discord.ui.View):
#     @discord.ui.select( # the decorator that lets you specify the properties of the select menu
#         placeholder = "Choose a Flavor!", # the placeholder text that will be displayed if nothing is selected
#         min_values = 1, # the minimum number of values that must be selected by the users
#         max_values = 1, # the maximum number of values that can be selected by the users
#         options = [ # the list of options from which users can choose, a required field
#             discord.SelectOption(
#                 label="Vanilla",
#                 description="Pick this if you like vanilla!"
#             ),
#             discord.SelectOption(
#                 label="Chocolate",
#                 description="Pick this if you like chocolate!"
#             ),
#             discord.SelectOption(
#                 label="Strawberry",
#                 description="Pick this if you like strawberry!"
#             )
#         ]
#     )
#     async def select_callback(self, interaction, select): # the function called when the user is done selecting options
#         await interaction.response.send_message(f"Awesome! I like {select.values[0]} too!")


# @bot.tree.command()
# async def flavor(ctx):
#     await ctx.response.send_message("Choose a flavor!", view=MyView())


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
