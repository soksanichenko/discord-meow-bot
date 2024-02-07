"""Main module of the bot"""

import os
from urllib.parse import urlparse, ParseResult

import discord

intents = discord.Intents.default()
# access to a message content
intents.message_content = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)


@tree.command(
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
    domains = {
        'reddit.com': 'rxddit.com',
        'tiktok.com': 'vxtiktok.com',
        'vm.tiktok.com': 'vm.vxtiktok.com',
        'twitter.com': 'vxtwitter.com',
    }
    domain = url.netloc
    for key, value in domains.items():
        domain = domain.replace(key, value)
    return ParseResult(
        url.scheme,
        netloc=domain,
        path=url.path,
        query=url.query,
        params=url.params,
        fragment=url.fragment,
    )


@client.event
async def on_message(message: discord.Message):

    if message.author == client.user:
        return
    if not message.embeds:
        return
    parsed_urls = (urlparse(embed.url) for embed in message.embeds)
    processed_urls = map(replace_domain, parsed_urls)
    final_urls = {
        embed.url: processed_url.geturl() for processed_url, embed in zip(
            processed_urls,
            message.embeds
        )
    }
    content = message.content
    for origin_url, final_url in final_urls.items():
        content = content.replace(origin_url, final_url)
    # An embed URL uses original domain even if you replaced it by a fixer.
    # So you need to compare replaced content and original one
    # and do nothing if they are identical.
    if message.content == content:
        return
    content += f'\nOriginal message posted by {message.author.mention}'
    await message.channel.send(content=content)
    await message.delete()


@client.event
async def on_ready():
    """
    Sync a tree of the commands
    :return:None
    """
    await tree.sync()


client.run(os.environ['DISCORD_TOKEN'])
