"""Tests for ReminderScheduler and _build_reminder_embed."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sources.lib.scheduler import (
    _OVERDUE_DELAY_SECONDS,
    ReminderScheduler,
    _build_reminder_embed,
)


def _reminder(**kwargs):
    defaults = {
        'id': 1,
        'user_id': 100,
        'channel_id': 200,
        'remind_at': datetime.now(UTC) + timedelta(hours=1),
        'created_at': datetime(2026, 3, 15, 14, 30, 0, tzinfo=UTC),
        'message_content': None,
        'message_url': None,
        'note': None,
        'is_sent': False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# _build_reminder_embed
# ---------------------------------------------------------------------------


class TestBuildReminderEmbed:
    def test_fallback_description_when_no_content(self):
        embed = _build_reminder_embed(_reminder())
        assert embed.description == 'You asked me to remind you — here I am!'
        assert len(embed.fields) == 0

    def test_adds_original_message_field_for_content(self):
        embed = _build_reminder_embed(_reminder(message_content='Hello world'))
        field_names = [f.name for f in embed.fields]
        assert 'Original message' in field_names

    def test_adds_jump_to_message_field_for_url(self):
        embed = _build_reminder_embed(
            _reminder(message_url='https://discord.com/1/2/3')
        )
        field_names = [f.name for f in embed.fields]
        assert 'Jump to message' in field_names

    def test_adds_note_field(self):
        embed = _build_reminder_embed(_reminder(note='Buy milk'))
        field_names = [f.name for f in embed.fields]
        assert 'Note' in field_names

    def test_all_three_fields_when_all_set(self):
        embed = _build_reminder_embed(
            _reminder(
                message_content='x', message_url='https://discord.com/1', note='y'
            )
        )
        field_names = [f.name for f in embed.fields]
        assert 'Original message' in field_names
        assert 'Jump to message' in field_names
        assert 'Note' in field_names

    def test_truncates_message_content_over_300_chars(self):
        long_content = 'a' * 400
        embed = _build_reminder_embed(_reminder(message_content=long_content))
        field = next(f for f in embed.fields if f.name == 'Original message')
        assert '...' in field.value
        assert 'a' * 401 not in field.value

    def test_does_not_truncate_content_at_300_chars(self):
        exact = 'b' * 300
        embed = _build_reminder_embed(_reminder(message_content=exact))
        field = next(f for f in embed.fields if f.name == 'Original message')
        assert '...' not in field.value

    def test_footer_contains_created_at(self):
        embed = _build_reminder_embed(
            _reminder(created_at=datetime(2026, 3, 15, 14, 30, 0, tzinfo=UTC))
        )
        assert '15 Mar 2026' in embed.footer.text
        assert '14:30' in embed.footer.text

    def test_no_fallback_description_when_content_present(self):
        embed = _build_reminder_embed(_reminder(message_content='Something'))
        assert embed.description != 'You asked me to remind you — here I am!'


# ---------------------------------------------------------------------------
# ReminderScheduler
# ---------------------------------------------------------------------------


class TestReminderSchedulerAdd:
    def _make_scheduler(self):
        s = ReminderScheduler()
        s._scheduler = MagicMock()
        return s

    def test_schedules_job_with_correct_run_date(self):
        s = self._make_scheduler()
        remind_at = datetime.now(UTC) + timedelta(hours=2)
        s.add(_reminder(id=5, remind_at=remind_at), MagicMock())
        kwargs = s._scheduler.add_job.call_args.kwargs
        assert kwargs['run_date'] == remind_at

    def test_job_id_is_derived_from_reminder_id(self):
        s = self._make_scheduler()
        s.add(_reminder(id=42), MagicMock())
        kwargs = s._scheduler.add_job.call_args.kwargs
        assert kwargs['id'] == 'reminder_42'

    def test_overdue_reminder_scheduled_with_short_delay(self):
        s = self._make_scheduler()
        past = datetime.now(UTC) - timedelta(hours=1)
        before = datetime.now(UTC)
        s.add(_reminder(remind_at=past), MagicMock())
        after = datetime.now(UTC)
        scheduled = s._scheduler.add_job.call_args.kwargs['run_date']
        assert before + timedelta(seconds=_OVERDUE_DELAY_SECONDS - 1) <= scheduled
        assert scheduled <= after + timedelta(seconds=_OVERDUE_DELAY_SECONDS + 1)

    def test_naive_datetime_gets_utc_tzinfo(self):
        s = self._make_scheduler()
        naive_future = datetime.now() + timedelta(hours=1)
        assert naive_future.tzinfo is None
        s.add(_reminder(remind_at=naive_future), MagicMock())
        scheduled = s._scheduler.add_job.call_args.kwargs['run_date']
        assert scheduled.tzinfo is not None

    def test_replace_existing_is_true(self):
        s = self._make_scheduler()
        s.add(_reminder(), MagicMock())
        kwargs = s._scheduler.add_job.call_args.kwargs
        assert kwargs['replace_existing'] is True


class TestReminderSchedulerCancel:
    def _make_scheduler(self):
        s = ReminderScheduler()
        s._scheduler = MagicMock()
        return s

    def test_removes_job_when_found(self):
        s = self._make_scheduler()
        mock_job = MagicMock()
        s._scheduler.get_job.return_value = mock_job
        s.cancel(1)
        s._scheduler.get_job.assert_called_once_with('reminder_1')
        mock_job.remove.assert_called_once()

    def test_no_op_when_job_not_found(self):
        s = self._make_scheduler()
        s._scheduler.get_job.return_value = None
        s.cancel(999)  # must not raise


class TestReminderSchedulerLoadPending:
    async def test_schedules_all_pending_reminders(self):
        s = ReminderScheduler()
        s._scheduler = MagicMock()
        r1, r2 = _reminder(id=1), _reminder(id=2)
        bot = MagicMock()
        with patch(
            'sources.lib.scheduler.get_pending_reminders',
            AsyncMock(return_value=[r1, r2]),
        ):
            await s.load_pending(bot)
        assert s._scheduler.add_job.call_count == 2

    async def test_no_jobs_when_no_pending(self):
        s = ReminderScheduler()
        s._scheduler = MagicMock()
        with patch(
            'sources.lib.scheduler.get_pending_reminders',
            AsyncMock(return_value=[]),
        ):
            await s.load_pending(MagicMock())
        s._scheduler.add_job.assert_not_called()


class TestReminderSchedulerShutdown:
    def test_shuts_down_running_scheduler(self):
        s = ReminderScheduler()
        s._scheduler = MagicMock()
        s._scheduler.running = True
        s.shutdown()
        s._scheduler.shutdown.assert_called_once_with(wait=False)

    def test_no_op_when_scheduler_not_running(self):
        s = ReminderScheduler()
        s._scheduler = MagicMock()
        s._scheduler.running = False
        s.shutdown()
        s._scheduler.shutdown.assert_not_called()
