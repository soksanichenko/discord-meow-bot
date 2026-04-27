"""Operations with DB table `reminders`"""

from datetime import UTC, datetime

from sqlalchemy import select

from sources.lib.db import AsyncSession
from sources.lib.db.models import Reminder


async def get_reminder(reminder_id: int) -> Reminder | None:
    """Fetch a single reminder by primary key.

    Args:
        reminder_id: The reminder ID.

    Returns:
        The Reminder instance or None if not found.
    """
    async with AsyncSession() as session:
        return await session.get(Reminder, reminder_id)


async def create_reminder(
    user_id: int,
    channel_id: int,
    remind_at: datetime,
    message_url: str | None = None,
    message_content: str | None = None,
    note: str | None = None,
) -> Reminder:
    """Create and persist a new reminder.

    Args:
        user_id: Discord user ID.
        channel_id: Discord channel ID where the reminder was set.
        remind_at: When to send the reminder (timezone-aware).
        message_url: Jump URL of the source message, if any.
        message_content: Text snippet of the source message, if any.
        note: Optional custom note from the user.

    Returns:
        The newly created Reminder instance.
    """
    reminder = Reminder(
        user_id=user_id,
        channel_id=channel_id,
        remind_at=remind_at,
        message_url=message_url,
        message_content=message_content,
        note=note,
        created_at=datetime.now(tz=UTC),
        is_sent=False,
    )
    async with AsyncSession() as session:
        session.add(reminder)
        await session.commit()
        await session.refresh(reminder)
        return reminder


async def get_pending_reminders() -> list[Reminder]:
    """Return all reminders that have not been sent yet.

    Returns:
        List of unsent Reminder instances.
    """
    async with AsyncSession() as session:
        result = await session.scalars(
            select(Reminder).where(Reminder.is_sent.is_(False))
        )
        return list(result.all())


async def get_user_reminders(user_id: int) -> list[Reminder]:
    """Return all pending reminders for a specific user, ordered by fire time.

    Args:
        user_id: Discord user ID.

    Returns:
        List of unsent Reminder instances for the user.
    """
    async with AsyncSession() as session:
        result = await session.scalars(
            select(Reminder)
            .where(Reminder.user_id == user_id, Reminder.is_sent.is_(False))
            .order_by(Reminder.remind_at)
        )
        return list(result.all())


async def mark_reminder_sent(reminder_id: int) -> None:
    """Mark a reminder as sent so it is not rescheduled after a restart.

    Args:
        reminder_id: The reminder ID.
    """
    async with AsyncSession() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is not None:
            reminder.is_sent = True
            await session.commit()


async def delete_reminder(reminder_id: int, user_id: int) -> bool:
    """Delete a reminder, verifying ownership.

    Args:
        reminder_id: The reminder ID.
        user_id: Discord user ID that must own the reminder.

    Returns:
        True if deleted, False if not found or not owned by the given user.
    """
    async with AsyncSession() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is None or reminder.user_id != user_id:
            return False
        await session.delete(reminder)
        await session.commit()
        return True
