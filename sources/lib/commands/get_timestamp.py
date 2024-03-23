"""Get timestamp module"""

from __future__ import annotations

import typing
from datetime import datetime

import dateparser
import discord
import pytz


async def autocomplete_timezone(
    interaction: discord.Interaction,  # pylint: disable=W0613
    user_timezone: str,
) -> typing.List[discord.app_commands.Choice[str]]:
    """Autocomplete timezone for a user"""
    return [
        discord.app_commands.Choice(name=timezone, value=timezone)
        for timezone in pytz.all_timezones
        if user_timezone.lower() in timezone.lower()
    ][:25]


def parse_and_validate(
    date: str,
    time: str,
    interaction: discord.Interaction,
    timezone: typing.Optional[str] = None,
) -> typing.Optional[datetime]:
    """Parse and validate date and time text"""
    options = {
        'locales': [interaction.locale.value],
    }
    if timezone is not None:
        options['settings'] = {
            'TIMEZONE': timezone,
            'RETURN_AS_TIMEZONE_AWARE': True,
        }
    return dateparser.parse(
        f'{time} {date}',
        **options,
    )


class TimezoneView(discord.ui.View):
    """
    View class for a user's timezone
    """

    def __init__(self, date: str = '', time: str = ''):
        super().__init__()
        self.time = time
        self.date = date

    @discord.ui.select(
        placeholder='Select timezone',
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label=timezone, description='')
            for timezone in pytz.all_timezones
        ],
    )
    async def select_callback(
        self,
        interaction: discord.Interaction,  # pylint: disable=W0613
        select: discord.ui.Select,
    ):
        """
        Callback for selecting a current user's timezone
        :param interaction: an object of interaction with a user
        :param select: a select option
        :return: None
        """
        return select.values[0]


class TimestampFormatView(discord.ui.View):
    """
    View class for timestamp formatting
    """

    def __init__(self, timestamp: int):
        super().__init__()
        self.timestamp = timestamp

    @discord.ui.select(
        placeholder='Select format',
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(
                label='F', description='Wednesday, 1 March 2021, 23:50'
            ),
            discord.SelectOption(label='f', description='1 March 2021, 23:50'),
            discord.SelectOption(label='D', description='1 March 2021'),
            discord.SelectOption(label='d', description='01.01.2021'),
            discord.SelectOption(label='t', description='23:50'),
            discord.SelectOption(label='T', description='23:50:55'),
            discord.SelectOption(label='R', description='2 hours ago'),
        ],
    )
    async def select_callback(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ):
        """
        Callback for selecting an option of formatting
        :param interaction: an object of interaction with a user
        :param select: a selected option
        :return: None
        """
        timestamp = f'<t:{self.timestamp}:{select.values[0]}>'

        await interaction.response.send_message(
            f'{timestamp}\n`{timestamp}`',
            ephemeral=True,
        )
