"""Main module of the bot"""

import os
import discord

intents = discord.Intents.default()
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


@client.event
async def on_ready():
    """
    Sync a tree of the commands
    :return:None
    """
    await tree.sync()


client.run(os.environ['DISCORD_TOKEN'])
