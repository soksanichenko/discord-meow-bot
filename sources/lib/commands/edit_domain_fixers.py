"""Edit domain fixer module"""

import discord


class EditDomainFixers(discord.ui.View):
    """View class for editing domain fixers"""

    def __init__(self):
        super().__init__()

    @discord.ui.button(
        style=discord.ButtonStyle.green,
        label='Enable',
    )
    async def click_enable_callback(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # pylint: disable=W0613
    ):
        """Click callback"""
        await interaction.response.send_message('ok', ephemeral=True)

    @discord.ui.button(
        style=discord.ButtonStyle.gray,
        label='Disable',
    )
    async def click_disable_callback(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,  # pylint: disable=W0613
    ):
        """Click callback"""
        await interaction.response.send_message('ok', ephemeral=True)
