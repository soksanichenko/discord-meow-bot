"""Reminders cog — set and manage personal reminders."""

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from sources.lib.commands.utils import require_timezone
from sources.lib.db.operations.reminders import (
    create_reminder,
    delete_reminder,
    get_user_reminders,
)
from sources.lib.scheduler import ReminderScheduler
from sources.lib.utils import Logger
from sources.lib.views.reminders import MAX_FUTURE_DAYS, RemindModal, parse_when


class RemindersCog(commands.Cog):
    """Commands and context menus for managing personal reminders."""

    reminders = app_commands.Group(
        name='reminders',
        description='List and cancel your pending reminders',
    )

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise the cog and register the message context menu.

        Args:
            bot: The Discord bot instance.
        """
        self.bot = bot
        self.logger = Logger()
        self.scheduler = ReminderScheduler()
        self.ctx_menu = app_commands.ContextMenu(
            name='Remind me about this',
            callback=self.remind_about_message,
        )
        self.bot.tree.add_command(self.ctx_menu)

    async def cog_load(self) -> None:
        """Start the scheduler and restore pending reminders from the database."""
        self.scheduler.start()
        await self.scheduler.load_pending(self.bot)

    def cog_unload(self) -> None:
        """Stop the scheduler and remove the context menu from the command tree."""
        self.scheduler.shutdown()
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    async def remind_about_message(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        """Context menu callback: open a reminder modal anchored to a message.

        Args:
            interaction: The Discord interaction.
            message: The message the user right-clicked on.
        """
        if await require_timezone(self.bot, interaction) is None:
            return
        await interaction.response.send_modal(
            RemindModal(scheduler=self.scheduler, bot=self.bot, source_message=message)
        )

    @reminders.command(name='add', description='Set a reminder')
    @app_commands.describe(
        when='When to remind you, e.g. "in 1 hour", "tomorrow 9am", "25 mar 15:00"',
        note='Optional note to include in the reminder',
    )
    async def remind(
        self,
        interaction: discord.Interaction,
        when: str,
        note: str | None = None,
    ) -> None:
        """Set a standalone reminder with a natural language time expression.

        Args:
            interaction: The Discord interaction.
            when: Natural language time string.
            note: Optional note to display in the reminder notification.
        """
        db_user = await require_timezone(self.bot, interaction)
        if db_user is None:
            return

        remind_at = parse_when(when, db_user.timezone)
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

        reminder = await create_reminder(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            remind_at=remind_at,
            note=note or None,
        )
        self.scheduler.add(reminder, self.bot)

        ts = discord.utils.format_dt(remind_at, style='F')
        await interaction.response.send_message(
            "Got it! I'll remind you %s." % ts,
            ephemeral=True,
        )

    @reminders.command(name='list', description='Show all your pending reminders')
    async def reminders_list(self, interaction: discord.Interaction) -> None:
        """Display the user's pending reminders as an ephemeral embed.

        Args:
            interaction: The Discord interaction.
        """
        user_reminders = await get_user_reminders(interaction.user.id)
        if not user_reminders:
            await interaction.response.send_message(
                'You have no pending reminders.',
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title='Your pending reminders',
            colour=discord.Colour.blurple(),
        )
        for reminder in user_reminders:
            name = '#%d — %s' % (
                reminder.id,
                discord.utils.format_dt(reminder.remind_at, style='f'),
            )
            parts = []
            if reminder.message_url:
                parts.append('[Original message](%s)' % reminder.message_url)
            elif reminder.message_content:
                snippet = reminder.message_content[:100]
                if len(reminder.message_content) > 100:
                    snippet += '...'
                parts.append('> %s' % snippet)
            if reminder.note:
                parts.append('Note: %s' % reminder.note)
            embed.add_field(
                name=name,
                value='\n'.join(parts) if parts else '*(no note)*',
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _autocomplete_reminder_id(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[int]]:
        """Autocomplete callback listing the user's pending reminders.

        Args:
            interaction: The Discord interaction.
            current: The text the user has typed so far.

        Returns:
            Up to 25 matching reminder choices.
        """
        user_reminders = await get_user_reminders(interaction.user.id)
        choices = []
        for reminder in user_reminders:
            ts = reminder.remind_at.strftime('%d %b %Y %H:%M')
            label = '#%d: %s' % (reminder.id, ts)
            if reminder.note:
                label += ' — %s' % reminder.note[:30]
            choices.append(app_commands.Choice(name=label, value=reminder.id))
        return choices[:25]

    @reminders.command(name='cancel', description='Cancel a pending reminder')
    @app_commands.describe(reminder_id='The reminder to cancel (start typing to search)')
    @app_commands.autocomplete(reminder_id=_autocomplete_reminder_id)
    async def reminders_cancel(
        self,
        interaction: discord.Interaction,
        reminder_id: int,
    ) -> None:
        """Cancel a pending reminder by ID.

        Args:
            interaction: The Discord interaction.
            reminder_id: Primary key of the reminder to cancel.
        """
        deleted = await delete_reminder(reminder_id, interaction.user.id)
        if not deleted:
            await interaction.response.send_message(
                'Reminder #%d was not found or does not belong to you.' % reminder_id,
                ephemeral=True,
            )
            return

        self.scheduler.cancel(reminder_id)
        self.logger.info(
            'Reminder %d cancelled by user %d', reminder_id, interaction.user.id
        )
        await interaction.response.send_message(
            'Reminder #%d has been cancelled.' % reminder_id,
            ephemeral=True,
        )
