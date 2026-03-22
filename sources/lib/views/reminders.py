"""Discord UI components for the reminders feature."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import dateparser
import discord
from discord.ext import commands

from sources.lib.db.operations.reminders import create_reminder
from sources.lib.db.operations.users import get_user

if TYPE_CHECKING:
    from sources.lib.scheduler import ReminderScheduler

MAX_FUTURE_DAYS = 365


def parse_when(text: str, timezone_str: str | None) -> datetime | None:
    """Parse a natural language time string into a timezone-aware datetime.

    Args:
        text: Free-form time description, e.g. 'in 1 hour' or 'tomorrow 9am'.
        timezone_str: IANA timezone name to use as the parsing context, or None
            for UTC.

    Returns:
        A timezone-aware datetime, or None if parsing failed.
    """
    settings: dict = {
        'PREFER_DATES_FROM': 'future',
        'RETURN_AS_TIMEZONE_AWARE': True,
    }
    if timezone_str:
        settings['TIMEZONE'] = timezone_str
    return dateparser.parse(text, settings=settings)


class RemindModal(discord.ui.Modal):
    """Modal dialog for collecting reminder time and an optional note.

    Can be used in two modes:
    - From a message context menu: pass ``source_message`` to anchor the
      reminder to a specific Discord message.
    - From a reschedule button: pass ``message_url`` and ``message_content``
      directly from the stored reminder data.
    """

    when = discord.ui.TextInput(
        label='When?',
        placeholder='in 1 hour / tomorrow 9am / 25 mar 15:00',
        max_length=100,
    )
    note = discord.ui.TextInput(
        label='Note (optional)',
        placeholder='Remind me about...',
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )

    def __init__(
        self,
        scheduler: 'ReminderScheduler',
        bot: commands.Bot,
        source_message: discord.Message | None = None,
        message_url: str | None = None,
        message_content: str | None = None,
    ) -> None:
        """Initialise the modal.

        Args:
            scheduler: The active ReminderScheduler used to register the job.
            bot: The Discord bot instance.
            source_message: The Discord message this reminder is anchored to.
                Takes priority over ``message_url`` / ``message_content``.
            message_url: Pre-stored jump URL, used when rescheduling.
            message_content: Pre-stored message snippet, used when rescheduling.
        """
        super().__init__(title='Set a Reminder')
        self.scheduler = scheduler
        self.bot = bot
        self.source_message = source_message
        self._message_url = message_url
        self._message_content = message_content

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Parse user input, persist the reminder, and schedule it.

        Args:
            interaction: The Discord interaction from the modal submission.
        """
        when_text = self.when.value.strip()
        note_text = self.note.value.strip() or None

        db_user = await get_user(interaction.user.id)
        timezone_str = db_user.timezone if db_user else None

        remind_at = parse_when(when_text, timezone_str)
        if remind_at is None:
            await interaction.response.send_message(
                "I couldn't understand that time. "
                'Try something like `in 1 hour`, `tomorrow 9am`, or `25 mar 15:00`.',
                ephemeral=True,
            )
            return

        now = datetime.now(tz=timezone.utc)
        if remind_at <= now:
            await interaction.response.send_message(
                'That time appears to be in the past. Please enter a future date/time.',
                ephemeral=True,
            )
            return

        delta = remind_at - now
        if delta.days > MAX_FUTURE_DAYS:
            await interaction.response.send_message(
                'Reminders cannot be set more than %d days in the future.' % MAX_FUTURE_DAYS,
                ephemeral=True,
            )
            return

        if self.source_message is not None:
            message_url = self.source_message.jump_url
            message_content = self.source_message.content[:500] or None
        else:
            message_url = self._message_url
            message_content = self._message_content

        reminder = await create_reminder(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            remind_at=remind_at,
            message_url=message_url,
            message_content=message_content,
            note=note_text,
        )
        self.scheduler.add(reminder, self.bot)

        ts = discord.utils.format_dt(remind_at, style='F')
        await interaction.response.send_message(
            "Got it! I'll remind you %s." % ts,
            ephemeral=True,
        )


class RescheduleView(discord.ui.View):
    """A View attached to a reminder notification with a Reschedule button."""

    def __init__(
        self,
        scheduler: 'ReminderScheduler',
        bot: commands.Bot,
        message_url: str | None = None,
        message_content: str | None = None,
    ) -> None:
        """Initialise the view.

        Args:
            scheduler: The active ReminderScheduler used to register the new job.
            bot: The Discord bot instance.
            message_url: Jump URL of the original message, if any.
            message_content: Text snippet of the original message, if any.
        """
        super().__init__(timeout=None)
        self.scheduler = scheduler
        self.bot = bot
        self.message_url = message_url
        self.message_content = message_content

    @discord.ui.button(label='Reschedule', style=discord.ButtonStyle.secondary, emoji='🔁')
    async def reschedule(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Open the reminder modal so the user can pick a new time.

        Args:
            interaction: The Discord interaction from the button click.
            button: The button that was clicked.
        """
        await interaction.response.send_modal(
            RemindModal(
                scheduler=self.scheduler,
                bot=self.bot,
                message_url=self.message_url,
                message_content=self.message_content,
            )
        )


