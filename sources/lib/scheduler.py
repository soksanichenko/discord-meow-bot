"""Reminder scheduler — wraps APScheduler AsyncIOScheduler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord.ext.commands import Bot

from sources.lib.db.models import Reminder
from sources.lib.db.operations.reminders import (
    get_pending_reminders,
    get_reminder,
    mark_reminder_sent,
)
from sources.lib.utils import Logger
from sources.lib.views.reminders import RescheduleView

_OVERDUE_DELAY_SECONDS = 5


def _build_reminder_embed(reminder: Reminder) -> discord.Embed:
    """Build the Discord embed for a reminder notification.

    Args:
        reminder: The Reminder instance to render.

    Returns:
        A Discord Embed ready to send.
    """
    embed = discord.Embed(title='⏰ Reminder', colour=discord.Colour.blurple())

    if reminder.message_content:
        snippet = reminder.message_content
        if len(snippet) > 300:
            snippet = snippet[:300] + '...'
        embed.add_field(name='Original message', value=f'> {snippet}', inline=False)

    if reminder.message_url:
        embed.add_field(
            name='Jump to message',
            value=f'[Click here]({reminder.message_url})',
            inline=False,
        )

    if reminder.note:
        embed.add_field(name='Note', value=reminder.note, inline=False)

    if not reminder.message_content and not reminder.message_url and not reminder.note:
        embed.description = 'You asked me to remind you — here I am!'

    embed.set_footer(text='Set on %s' % reminder.created_at.strftime('%d %b %Y at %H:%M UTC'))
    return embed


async def _fire_reminder(reminder_id: int, bot: Bot, scheduler: 'ReminderScheduler') -> None:
    """Fetch a reminder from DB, send a DM to the user, and mark it as sent.

    Falls back to the original channel if the DM cannot be delivered.
    The notification includes a Reschedule button so the user can set a new
    reminder for the same message without returning to the original channel.

    Args:
        reminder_id: The reminder ID to fire.
        bot: The Discord bot instance used to send messages.
        scheduler: The active ReminderScheduler, passed to the Reschedule button.
    """
    logger = Logger()

    reminder = await get_reminder(reminder_id)
    if reminder is None or reminder.is_sent:
        logger.debug('Reminder %d skipped (not found or already sent)', reminder_id)
        return

    # Mark as sent first to prevent double-fire on restart
    await mark_reminder_sent(reminder_id)

    embed = _build_reminder_embed(reminder)
    view = RescheduleView(
        scheduler=scheduler,
        bot=bot,
        message_url=reminder.message_url,
        message_content=reminder.message_content,
    )

    user = bot.get_user(reminder.user_id)
    if user is None:
        try:
            user = await bot.fetch_user(reminder.user_id)
        except Exception as exc:
            logger.warning(
                'Could not fetch user %d for reminder %d: %s',
                reminder.user_id,
                reminder_id,
                exc,
            )
            return

    try:
        await user.send(embed=embed, view=view)
        logger.info('Reminder %d delivered to user %d', reminder_id, reminder.user_id)
    except discord.Forbidden:
        logger.warning(
            'Cannot DM user %d for reminder %d — falling back to channel',
            reminder.user_id,
            reminder_id,
        )
        channel = bot.get_channel(reminder.channel_id)
        if channel is not None:
            try:
                public_embed = embed.copy()
                public_embed.add_field(
                    name='⚠️ Direct message unavailable',
                    value='Your server DMs appear to be disabled, so this reminder was posted publicly.',
                    inline=False,
                )
                await channel.send(f'<@{reminder.user_id}>', embed=public_embed, view=view)
            except Exception as exc:
                logger.warning(
                    'Channel fallback failed for reminder %d: %s',
                    reminder_id,
                    exc,
                )
    except Exception as exc:
        logger.warning('Failed to send reminder %d: %s', reminder_id, exc)


class ReminderScheduler:
    """Manages scheduling of reminder jobs using APScheduler."""

    def __init__(self) -> None:
        """Initialise the scheduler (does not start it yet)."""
        self._scheduler = AsyncIOScheduler()
        self._logger = Logger()

    def start(self) -> None:
        """Start the underlying APScheduler."""
        self._scheduler.start()
        self._logger.info('Reminder scheduler started')

    def shutdown(self) -> None:
        """Stop the underlying APScheduler if it is running."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            self._logger.info('Reminder scheduler stopped')

    async def load_pending(self, bot: Bot) -> None:
        """Load all unsent reminders from the database and schedule them.

        Args:
            bot: The Discord bot instance passed through to each job.
        """
        reminders = await get_pending_reminders()
        for reminder in reminders:
            self.add(reminder, bot)
        self._logger.info('Loaded %d pending reminder(s) from DB', len(reminders))

    def add(self, reminder: Reminder, bot: Bot) -> None:
        """Schedule a single reminder job.

        Reminders that are already overdue are scheduled to fire after a short
        delay so that on-startup notifications are not lost.

        Args:
            reminder: The Reminder instance to schedule.
            bot: The Discord bot instance passed through to the job.
        """
        run_date = reminder.remind_at
        if run_date.tzinfo is None:
            run_date = run_date.replace(tzinfo=timezone.utc)

        now = datetime.now(tz=timezone.utc)
        if run_date <= now:
            run_date = now + timedelta(seconds=_OVERDUE_DELAY_SECONDS)

        self._scheduler.add_job(
            _fire_reminder,
            trigger='date',
            run_date=run_date,
            args=[reminder.id, bot, self],
            id='reminder_%d' % reminder.id,
            replace_existing=True,
        )
        self._logger.debug('Scheduled reminder %d for %s', reminder.id, run_date.isoformat())

    def cancel(self, reminder_id: int) -> None:
        """Remove a scheduled reminder job if it exists.

        Args:
            reminder_id: The reminder ID whose job should be removed.
        """
        job = self._scheduler.get_job('reminder_%d' % reminder_id)
        if job is not None:
            job.remove()
            self._logger.debug('Cancelled reminder job %d', reminder_id)
