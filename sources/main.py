import discord
import os

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)


@tree.command(
    name='ping',
    description='Test ping command',
)
async def ping(interaction):
    await interaction.response.send_message('pong')


@client.event
async def on_ready():
    await tree.sync()


client.run(os.environ['DISCORD_TOKEN'])
