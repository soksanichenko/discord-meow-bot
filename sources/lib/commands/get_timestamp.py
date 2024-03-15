"""Get timestamp module"""
from __future__ import annotations

import typing
from datetime import datetime

import dateparser
import discord


def parse_and_validate(
    date: str,
    time: str,
    interaction: discord.Interaction,
) -> typing.Optional[datetime]:
    """Parse and validate date and time text"""

    return dateparser.parse(
        f"{time} {date}",
        locales=[interaction.locale.value],
    )


class TimestampFormatView(discord.ui.View):
    """
    View class for timestamp formatting
    """

    def __init__(self, timestamp: int):
        self.timestamp = timestamp
        super().__init__()

    @discord.ui.select(
        placeholder="Select format",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(
                label="F", description="Wednesday, 1 January 2021, 23:50"
            ),
            discord.SelectOption(label="f", description="1 January 2021, 23:50"),
            discord.SelectOption(label="D", description="1 January 2021"),
            discord.SelectOption(label="d", description="01.01.2021"),
            discord.SelectOption(label="t", description="23:50"),
            discord.SelectOption(label="T", description="23:50:55"),
            discord.SelectOption(label="R", description="2 hours ago"),
        ],
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
        timestamp = f"<t:{self.timestamp}:{select.values[0]}>"

        await interaction.response.send_message(
            f"{timestamp}\n`{timestamp}`",
            ephemeral=True,
        )
