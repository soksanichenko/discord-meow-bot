"""Tests for DB operations with mocked AsyncSession.

Each operation module creates its own AsyncSession internally, so tests patch
`AsyncSession` in the relevant module and inject a pre-configured mock session.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Session factory helper
# ---------------------------------------------------------------------------


def _make_session(*, get=None, scalars_rows=None, scalars_one=None, scalar=None):
    """Return (session_mock, async-context-manager mock) pair."""
    session = AsyncMock()
    # session.add() is synchronous in SQLAlchemy — override the AsyncMock default.
    session.add = MagicMock()
    session.get.return_value = get

    scalars_mock = MagicMock()
    scalars_mock.all.return_value = scalars_rows if scalars_rows is not None else []
    scalars_mock.one_or_none.return_value = scalars_one
    # allow iteration directly (used in list comprehensions)
    scalars_mock.__iter__ = MagicMock(
        return_value=iter(scalars_rows if scalars_rows is not None else [])
    )
    session.scalars.return_value = scalars_mock
    session.scalar.return_value = scalar

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return session, ctx


# ---------------------------------------------------------------------------
# reminders operations
# ---------------------------------------------------------------------------


class TestDeleteReminder:
    async def test_returns_false_when_reminder_not_found(self):
        session, ctx = _make_session(get=None)
        with patch(
            'sources.lib.db.operations.reminders.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.reminders import delete_reminder

            result = await delete_reminder(99, user_id=1)
        assert result is False
        session.delete.assert_not_awaited()

    async def test_returns_false_when_wrong_user(self):
        reminder = SimpleNamespace(user_id=1)
        session, ctx = _make_session(get=reminder)
        with patch(
            'sources.lib.db.operations.reminders.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.reminders import delete_reminder

            result = await delete_reminder(1, user_id=2)
        assert result is False
        session.delete.assert_not_awaited()

    async def test_returns_true_and_deletes_when_owner(self):
        reminder = SimpleNamespace(user_id=7)
        session, ctx = _make_session(get=reminder)
        with patch(
            'sources.lib.db.operations.reminders.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.reminders import delete_reminder

            result = await delete_reminder(1, user_id=7)
        assert result is True
        session.delete.assert_awaited_once_with(reminder)
        session.commit.assert_awaited_once()


class TestGetUserReminders:
    async def test_returns_list_from_db(self):
        r1 = SimpleNamespace(id=1)
        r2 = SimpleNamespace(id=2)
        session, ctx = _make_session(scalars_rows=[r1, r2])
        with patch(
            'sources.lib.db.operations.reminders.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.reminders import get_user_reminders

            result = await get_user_reminders(user_id=5)
        assert result == [r1, r2]

    async def test_returns_empty_list_when_none(self):
        session, ctx = _make_session(scalars_rows=[])
        with patch(
            'sources.lib.db.operations.reminders.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.reminders import get_user_reminders

            result = await get_user_reminders(user_id=5)
        assert result == []


# ---------------------------------------------------------------------------
# music_links operations
# ---------------------------------------------------------------------------


class TestAddAllowedChannel:
    async def test_returns_false_when_already_exists(self):
        existing = SimpleNamespace(guild_id=1, channel_id=100)
        session, ctx = _make_session(get=existing)
        with patch(
            'sources.lib.db.operations.music_links.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.music_links import add_allowed_channel

            result = await add_allowed_channel(guild_id=1, channel_id=100)
        assert result is False
        session.add.assert_not_called()

    async def test_adds_and_returns_true_when_new(self):
        session, ctx = _make_session(get=None)
        with patch(
            'sources.lib.db.operations.music_links.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.music_links import add_allowed_channel

            result = await add_allowed_channel(guild_id=1, channel_id=200)
        assert result is True
        session.add.assert_called_once()
        session.commit.assert_awaited_once()


class TestRemoveAllowedChannel:
    async def test_returns_false_when_not_found(self):
        session, ctx = _make_session(get=None)
        with patch(
            'sources.lib.db.operations.music_links.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.music_links import remove_allowed_channel

            result = await remove_allowed_channel(guild_id=1, channel_id=999)
        assert result is False
        session.delete.assert_not_awaited()

    async def test_deletes_and_returns_true_when_found(self):
        row = SimpleNamespace(guild_id=1, channel_id=100)
        session, ctx = _make_session(get=row)
        with patch(
            'sources.lib.db.operations.music_links.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.music_links import remove_allowed_channel

            result = await remove_allowed_channel(guild_id=1, channel_id=100)
        assert result is True
        session.delete.assert_awaited_once_with(row)
        session.commit.assert_awaited_once()


class TestGetAllowedChannels:
    async def test_returns_channel_ids(self):
        rows = [SimpleNamespace(channel_id=10), SimpleNamespace(channel_id=20)]
        session, ctx = _make_session(scalars_rows=rows)
        with patch(
            'sources.lib.db.operations.music_links.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.music_links import get_allowed_channels

            result = await get_allowed_channels(guild_id=1)
        assert result == [10, 20]

    async def test_returns_empty_when_none(self):
        session, ctx = _make_session(scalars_rows=[])
        with patch(
            'sources.lib.db.operations.music_links.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.music_links import get_allowed_channels

            result = await get_allowed_channels(guild_id=1)
        assert result == []


# ---------------------------------------------------------------------------
# youtube_relay operations
# ---------------------------------------------------------------------------


class TestRemoveRelayById:
    async def test_returns_false_when_not_found(self):
        session, ctx = _make_session(get=None)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import remove_relay_by_id

            result = await remove_relay_by_id(relay_id=99)
        assert result is False
        session.delete.assert_not_awaited()

    async def test_returns_true_and_deletes_when_found(self):
        relay = SimpleNamespace(id=1)
        session, ctx = _make_session(get=relay)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import remove_relay_by_id

            result = await remove_relay_by_id(relay_id=1)
        assert result is True
        session.delete.assert_awaited_once_with(relay)
        session.commit.assert_awaited_once()


class TestSetRelayMessageById:
    def _relay(self, **kwargs):
        defaults = {
            'id': 1,
            'yt_channel_title': 'My Channel',
            'message_video': None,
            'message_short': None,
            'message_live': None,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    async def test_returns_none_when_not_found(self):
        session, ctx = _make_session(get=None)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import set_relay_message_by_id

            result = await set_relay_message_by_id(1, 'video', 'Hi!')
        assert result is None

    async def test_sets_video_field_and_returns_title(self):
        relay = self._relay()
        session, ctx = _make_session(get=relay)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import set_relay_message_by_id

            result = await set_relay_message_by_id(1, 'video', 'New video!')
        assert result == 'My Channel'
        assert relay.message_video == 'New video!'

    async def test_sets_short_field(self):
        relay = self._relay()
        session, ctx = _make_session(get=relay)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import set_relay_message_by_id

            await set_relay_message_by_id(1, 'short', 'Short dropped!')
        assert relay.message_short == 'Short dropped!'

    async def test_sets_live_field(self):
        relay = self._relay()
        session, ctx = _make_session(get=relay)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import set_relay_message_by_id

            await set_relay_message_by_id(1, 'live', 'Live now!')
        assert relay.message_live == 'Live now!'

    async def test_clears_field_with_none(self):
        relay = self._relay(message_video='Old message')
        session, ctx = _make_session(get=relay)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import set_relay_message_by_id

            await set_relay_message_by_id(1, 'video', None)
        assert relay.message_video is None


class TestUpdateRelayContentFlags:
    async def test_updates_flags_when_found(self):
        relay = SimpleNamespace(post_videos=False, post_shorts=False, post_lives=False)
        session, ctx = _make_session(get=relay)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import (
                update_relay_content_flags,
            )

            await update_relay_content_flags(
                1, post_videos=True, post_shorts=False, post_lives=True
            )
        assert relay.post_videos is True
        assert relay.post_shorts is False
        assert relay.post_lives is True
        session.commit.assert_awaited_once()

    async def test_no_op_when_not_found(self):
        session, ctx = _make_session(get=None)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import (
                update_relay_content_flags,
            )

            await update_relay_content_flags(
                99, post_videos=True, post_shorts=True, post_lives=True
            )
        session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# youtube_live_session operations
# ---------------------------------------------------------------------------


class TestRemoveLiveSession:
    async def test_deletes_when_found(self):
        obj = SimpleNamespace(id=5)
        session, ctx = _make_session(get=obj)
        with patch(
            'sources.lib.db.operations.youtube_live_session.AsyncSession',
            return_value=ctx,
        ):
            from sources.lib.db.operations.youtube_live_session import (
                remove_live_session,
            )

            await remove_live_session(5)
        session.delete.assert_awaited_once_with(obj)
        session.commit.assert_awaited_once()

    async def test_no_op_when_not_found(self):
        session, ctx = _make_session(get=None)
        with patch(
            'sources.lib.db.operations.youtube_live_session.AsyncSession',
            return_value=ctx,
        ):
            from sources.lib.db.operations.youtube_live_session import (
                remove_live_session,
            )

            await remove_live_session(99)
        session.delete.assert_not_awaited()
        session.commit.assert_not_awaited()


class TestGetAllLiveSessions:
    async def test_returns_live_sessions_list(self):
        s1 = SimpleNamespace(id=1, video_id='abc')
        session, ctx = _make_session(scalars_rows=[s1])
        with patch(
            'sources.lib.db.operations.youtube_live_session.AsyncSession',
            return_value=ctx,
        ):
            from sources.lib.db.operations.youtube_live_session import (
                get_all_live_sessions,
            )

            result = await get_all_live_sessions()
        assert result == [s1]

    async def test_returns_empty_list_when_none(self):
        session, ctx = _make_session(scalars_rows=[])
        with patch(
            'sources.lib.db.operations.youtube_live_session.AsyncSession',
            return_value=ctx,
        ):
            from sources.lib.db.operations.youtube_live_session import (
                get_all_live_sessions,
            )

            result = await get_all_live_sessions()
        assert result == []


# ---------------------------------------------------------------------------
# reminders operations (remaining)
# ---------------------------------------------------------------------------


class TestCreateReminder:
    async def test_sets_is_sent_false_and_created_at(self):
        from datetime import UTC, datetime

        session, ctx = _make_session()
        with patch(
            'sources.lib.db.operations.reminders.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.reminders import create_reminder

            remind_at = datetime.now(UTC)
            await create_reminder(user_id=1, channel_id=2, remind_at=remind_at)
        added = session.add.call_args[0][0]
        assert added.is_sent is False
        assert added.created_at is not None
        session.commit.assert_awaited_once()
        session.refresh.assert_awaited_once()

    async def test_stores_optional_fields(self):
        from datetime import UTC, datetime

        session, ctx = _make_session()
        with patch(
            'sources.lib.db.operations.reminders.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.reminders import create_reminder

            await create_reminder(
                user_id=1,
                channel_id=2,
                remind_at=datetime.now(UTC),
                message_url='https://discord.com/1',
                message_content='Hello',
                note='Buy milk',
            )
        added = session.add.call_args[0][0]
        assert added.message_url == 'https://discord.com/1'
        assert added.message_content == 'Hello'
        assert added.note == 'Buy milk'


class TestGetPendingReminders:
    async def test_returns_list_of_unsent(self):
        r1, r2 = SimpleNamespace(id=1), SimpleNamespace(id=2)
        session, ctx = _make_session(scalars_rows=[r1, r2])
        with patch(
            'sources.lib.db.operations.reminders.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.reminders import get_pending_reminders

            result = await get_pending_reminders()
        assert result == [r1, r2]

    async def test_returns_empty_list_when_none(self):
        session, ctx = _make_session(scalars_rows=[])
        with patch(
            'sources.lib.db.operations.reminders.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.reminders import get_pending_reminders

            result = await get_pending_reminders()
        assert result == []


class TestMarkReminderSent:
    async def test_sets_is_sent_true_when_found(self):
        reminder = SimpleNamespace(is_sent=False)
        session, ctx = _make_session(get=reminder)
        with patch(
            'sources.lib.db.operations.reminders.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.reminders import mark_reminder_sent

            await mark_reminder_sent(1)
        assert reminder.is_sent is True
        session.commit.assert_awaited_once()

    async def test_no_op_when_not_found(self):
        session, ctx = _make_session(get=None)
        with patch(
            'sources.lib.db.operations.reminders.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.reminders import mark_reminder_sent

            await mark_reminder_sent(99)
        session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# youtube_relay operations (remaining)
# ---------------------------------------------------------------------------


class TestGetAllYouTubeRelays:
    async def test_returns_relay_list(self):
        r1, r2 = SimpleNamespace(id=1), SimpleNamespace(id=2)
        session, ctx = _make_session(scalars_rows=[r1, r2])
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import get_all_relays

            result = await get_all_relays()
        assert result == [r1, r2]


class TestGetRelayById:
    async def test_returns_relay_when_found(self):
        relay = SimpleNamespace(id=1)
        session, ctx = _make_session(get=relay)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import get_relay_by_id

            result = await get_relay_by_id(1)
        assert result is relay

    async def test_returns_none_when_not_found(self):
        session, ctx = _make_session(get=None)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import get_relay_by_id

            result = await get_relay_by_id(99)
        assert result is None


class TestUpdateLastVideoId:
    async def test_sets_field_when_found(self):
        relay = SimpleNamespace(last_video_id=None)
        session, ctx = _make_session(get=relay)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import update_last_video_id

            await update_last_video_id(relay_id=1, last_video_id='abc123')
        assert relay.last_video_id == 'abc123'
        session.commit.assert_awaited_once()

    async def test_no_op_when_not_found(self):
        session, ctx = _make_session(get=None)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import update_last_video_id

            await update_last_video_id(relay_id=99, last_video_id='abc123')
        session.commit.assert_not_awaited()


class TestEnableRelayType:
    async def test_sets_flag_on_existing_relay(self):
        relay = SimpleNamespace(post_videos=False, post_shorts=False, post_lives=False)
        session, ctx = _make_session(scalar=relay)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import enable_relay_type

            await enable_relay_type(
                guild_id=1,
                yt_channel_id='UCxxx',
                yt_channel_title='My Channel',
                discord_channel_id=100,
                flag_key='post_videos',
                last_video_id='vid1',
            )
        assert relay.post_videos is True
        session.commit.assert_awaited_once()

    async def test_creates_relay_and_sets_flag_when_not_found(self):
        from sources.lib.db.models import YouTubeRelay

        session, ctx = _make_session(scalar=None)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import enable_relay_type

            await enable_relay_type(
                guild_id=1,
                yt_channel_id='UCxxx',
                yt_channel_title='My Channel',
                discord_channel_id=100,
                flag_key='post_shorts',
                last_video_id=None,
            )
        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        assert isinstance(added, YouTubeRelay)
        assert added.post_shorts is True
        assert added.post_videos is False
        assert added.post_lives is False
        session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# users operations
# ---------------------------------------------------------------------------


class TestGetUser:
    async def test_returns_user_when_found(self):
        user = SimpleNamespace(id=1, name='Alice')
        session, ctx = _make_session(scalars_one=user)
        with patch('sources.lib.db.operations.users.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.users import get_user

            result = await get_user(1)
        assert result is user

    async def test_returns_none_when_not_found(self):
        session, ctx = _make_session(scalars_one=None)
        with patch('sources.lib.db.operations.users.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.users import get_user

            result = await get_user(99)
        assert result is None


# ---------------------------------------------------------------------------
# telegram relay operations
# ---------------------------------------------------------------------------


class TestGetGuildTelegramRelays:
    async def test_returns_relay_list(self):
        r1 = SimpleNamespace(id=1, tg_username='chan')
        session, ctx = _make_session(scalars_rows=[r1])
        with patch(
            'sources.lib.db.operations.telegram_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.telegram_relay import get_guild_relays

            result = await get_guild_relays(guild_id=1)
        assert result == [r1]

    async def test_returns_empty_list_when_none(self):
        session, ctx = _make_session(scalars_rows=[])
        with patch(
            'sources.lib.db.operations.telegram_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.telegram_relay import get_guild_relays

            result = await get_guild_relays(guild_id=1)
        assert result == []


class TestRemoveTelegramRelay:
    async def test_returns_false_when_not_found(self):
        session, ctx = _make_session(scalar=None)
        with patch(
            'sources.lib.db.operations.telegram_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.telegram_relay import remove_relay

            result = await remove_relay(guild_id=1, tg_username='chan')
        assert result is False
        session.delete.assert_not_awaited()

    async def test_returns_true_and_deletes_when_found(self):
        relay = SimpleNamespace(guild_id=1, tg_username='chan')
        session, ctx = _make_session(scalar=relay)
        with patch(
            'sources.lib.db.operations.telegram_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.telegram_relay import remove_relay

            result = await remove_relay(guild_id=1, tg_username='chan')
        assert result is True
        session.delete.assert_awaited_once_with(relay)
        session.commit.assert_awaited_once()


class TestUpdateTelegramRelayChannel:
    async def test_returns_none_when_not_found(self):
        session, ctx = _make_session()
        session.scalar.side_effect = [None]
        with patch(
            'sources.lib.db.operations.telegram_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.telegram_relay import update_relay_channel

            result = await update_relay_channel(
                relay_id=99, guild_id=1, discord_channel_id=200
            )
        assert result is None
        session.commit.assert_not_awaited()

    async def test_raises_value_error_on_conflict(self):
        relay = SimpleNamespace(tg_username='chan', discord_channel_id=100)
        conflict = SimpleNamespace(id=2)
        session, ctx = _make_session()
        session.scalar.side_effect = [relay, conflict]
        with patch(
            'sources.lib.db.operations.telegram_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.telegram_relay import update_relay_channel

            with pytest.raises(ValueError, match='duplicate'):
                await update_relay_channel(
                    relay_id=1, guild_id=1, discord_channel_id=200
                )
        session.commit.assert_not_awaited()

    async def test_updates_channel_and_returns_username(self):
        relay = SimpleNamespace(tg_username='chan', discord_channel_id=100)
        session, ctx = _make_session()
        session.scalar.side_effect = [relay, None]
        with patch(
            'sources.lib.db.operations.telegram_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.telegram_relay import update_relay_channel

            result = await update_relay_channel(
                relay_id=1, guild_id=1, discord_channel_id=200
            )
        assert result == 'chan'
        assert relay.discord_channel_id == 200
        session.commit.assert_awaited_once()


class TestUpdateLastEntryId:
    async def test_sets_field_when_found(self):
        relay = SimpleNamespace(last_entry_id=None)
        session, ctx = _make_session(get=relay)
        with patch(
            'sources.lib.db.operations.telegram_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.telegram_relay import update_last_entry_id

            await update_last_entry_id(relay_id=1, last_entry_id='entry123')
        assert relay.last_entry_id == 'entry123'
        session.commit.assert_awaited_once()

    async def test_no_op_when_not_found(self):
        session, ctx = _make_session(get=None)
        with patch(
            'sources.lib.db.operations.telegram_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.telegram_relay import update_last_entry_id

            await update_last_entry_id(relay_id=99, last_entry_id='entry123')
        session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# guilds operations
# ---------------------------------------------------------------------------


class TestUpsertGuild:
    async def test_creates_guild_when_not_exists(self):
        session, ctx = _make_session(scalars_one=None)
        with patch('sources.lib.db.operations.guilds.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.guilds import upsert_guild

            await upsert_guild(guild_id=1, guild_name='Test Guild')
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

    async def test_updates_guild_when_exists(self):
        existing = SimpleNamespace(id=1, name='Old Name')
        session, ctx = _make_session(scalars_one=existing)
        with patch('sources.lib.db.operations.guilds.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.guilds import upsert_guild

            await upsert_guild(guild_id=1, guild_name='New Name')
        assert existing.name == 'New Name'
        session.commit.assert_awaited_once()


class TestDeleteGuild:
    async def test_deletes_when_found(self):
        guild = SimpleNamespace(id=1)
        session, ctx = _make_session(scalars_one=guild)
        with patch('sources.lib.db.operations.guilds.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.guilds import delete_guild

            await delete_guild(guild_id=1)
        session.delete.assert_awaited_once_with(guild)
        session.commit.assert_awaited_once()

    async def test_no_op_when_not_found(self):
        session, ctx = _make_session(scalars_one=None)
        with patch('sources.lib.db.operations.guilds.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.guilds import delete_guild

            await delete_guild(guild_id=99)
        session.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# stats operations
# ---------------------------------------------------------------------------


class TestGetLeaderboard:
    async def test_returns_stats_list(self):
        s1 = SimpleNamespace(user_id=1, message_count=100)
        s2 = SimpleNamespace(user_id=2, message_count=50)
        session, ctx = _make_session(scalars_rows=[s1, s2])
        with patch('sources.lib.db.operations.stats.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.stats import get_leaderboard

            result = await get_leaderboard(guild_id=1)
        assert result == [s1, s2]

    async def test_returns_empty_list_when_none(self):
        session, ctx = _make_session(scalars_rows=[])
        with patch('sources.lib.db.operations.stats.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.stats import get_leaderboard

            result = await get_leaderboard(guild_id=1)
        assert result == []


class TestGetChannelProgress:
    async def test_returns_progress_when_found(self):
        progress = SimpleNamespace(guild_id=1, channel_id=100, is_completed=False)
        session, ctx = _make_session(get=progress)
        with patch('sources.lib.db.operations.stats.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.stats import get_channel_progress

            result = await get_channel_progress(guild_id=1, channel_id=100)
        assert result is progress

    async def test_returns_none_when_not_found(self):
        session, ctx = _make_session(get=None)
        with patch('sources.lib.db.operations.stats.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.stats import get_channel_progress

            result = await get_channel_progress(guild_id=1, channel_id=999)
        assert result is None


class TestGetGuildsWithIncompleteImport:
    async def test_returns_guild_id_list(self):
        session, ctx = _make_session(scalars_rows=[1, 2, 3])
        with patch('sources.lib.db.operations.stats.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.stats import (
                get_guilds_with_incomplete_import,
            )

            result = await get_guilds_with_incomplete_import()
        assert result == [1, 2, 3]


# ---------------------------------------------------------------------------
# birthdays operations
# ---------------------------------------------------------------------------


class TestRemoveGuildMemberBirthday:
    async def test_returns_false_when_not_found(self):
        session, ctx = _make_session(scalars_one=None)
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import remove_guild_member_birthday

            result = await remove_guild_member_birthday(guild_id=1, user_id=2)
        assert result is False
        session.delete.assert_not_awaited()

    async def test_deletes_and_returns_true_when_found(self):
        record = SimpleNamespace(guild_id=1, user_id=2)
        session, ctx = _make_session(scalars_one=record)
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import remove_guild_member_birthday

            result = await remove_guild_member_birthday(guild_id=1, user_id=2)
        assert result is True
        session.delete.assert_awaited_once_with(record)
        session.commit.assert_awaited_once()


class TestMarkBirthdayAnnounced:
    async def test_sets_year_when_found(self):
        record = SimpleNamespace(last_announced_year=None)
        session, ctx = _make_session(scalars_one=record)
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import mark_birthday_announced

            await mark_birthday_announced(guild_id=1, user_id=2, year=2026)
        assert record.last_announced_year == 2026
        session.commit.assert_awaited_once()

    async def test_no_op_when_not_found(self):
        session, ctx = _make_session(scalars_one=None)
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import mark_birthday_announced

            await mark_birthday_announced(guild_id=1, user_id=99, year=2026)
        session.commit.assert_not_awaited()


class TestGetGuildBirthdays:
    async def test_returns_birthday_list(self):
        b1, b2 = SimpleNamespace(user_id=1), SimpleNamespace(user_id=2)
        session, ctx = _make_session(scalars_rows=[b1, b2])
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import get_guild_birthdays

            result = await get_guild_birthdays(guild_id=1)
        assert result == [b1, b2]


# ---------------------------------------------------------------------------
# domain_fixers operations
# ---------------------------------------------------------------------------


class TestFindOrCreateRule:
    """_find_or_create_rule accepts an injected session — no ctx needed."""

    def _session(self, *, scalars_one=None):
        session = AsyncMock()
        session.add = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.one_or_none.return_value = scalars_one
        session.scalars.return_value = scalars_mock
        return session

    async def test_returns_existing_rule_when_found(self):
        existing = SimpleNamespace(source_domain='reddit.com')
        session = self._session(scalars_one=existing)
        from sources.lib.db.operations.domain_fixers import _find_or_create_rule

        result = await _find_or_create_rule(session, 'reddit.com', 'rxddit', None)
        assert result is existing
        session.add.assert_not_called()
        session.flush.assert_not_awaited()

    async def test_creates_rule_when_not_found(self):
        session = self._session(scalars_one=None)
        from sources.lib.db.models import DomainFixer
        from sources.lib.db.operations.domain_fixers import _find_or_create_rule

        result = await _find_or_create_rule(session, 'reddit.com', 'rxddit', None)
        assert isinstance(result, DomainFixer)
        assert result.source_domain == 'reddit.com'
        assert result.replacement_domain == 'rxddit'
        session.add.assert_called_once_with(result)
        session.flush.assert_awaited_once()


class TestDeleteDomainFixer:
    async def test_deletes_junction_when_found(self):
        junction = SimpleNamespace()
        session, ctx = _make_session(scalars_one=junction)
        with patch(
            'sources.lib.db.operations.domain_fixers.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.domain_fixers import delete_domain_fixer

            await delete_domain_fixer(guild_id=1, source_domain='reddit.com')
        session.delete.assert_awaited_once_with(junction)
        session.commit.assert_awaited_once()

    async def test_no_op_when_not_found(self):
        session, ctx = _make_session(scalars_one=None)
        with patch(
            'sources.lib.db.operations.domain_fixers.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.domain_fixers import delete_domain_fixer

            await delete_domain_fixer(guild_id=1, source_domain='unknown.com')
        session.delete.assert_not_awaited()
        session.commit.assert_not_awaited()


class TestGetAllDomainFixers:
    async def test_returns_fixer_list(self):
        f1 = SimpleNamespace(source_domain='reddit.com')
        session, ctx = _make_session(scalars_rows=[f1])
        with patch(
            'sources.lib.db.operations.domain_fixers.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.domain_fixers import get_all_domain_fixers

            result = await get_all_domain_fixers(guild_id=1)
        assert result == [f1]

    async def test_returns_empty_when_none(self):
        session, ctx = _make_session(scalars_rows=[])
        with patch(
            'sources.lib.db.operations.domain_fixers.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.domain_fixers import get_all_domain_fixers

            result = await get_all_domain_fixers(guild_id=1)
        assert result == []


class TestSeedDefaultDomainFixers:
    async def test_calls_upsert_for_each_default_rule(self):
        from sources.lib.db.operations.domain_fixers import DEFAULT_DOMAIN_FIXERS

        with patch(
            'sources.lib.db.operations.domain_fixers.upsert_domain_fixer',
            new=AsyncMock(),
        ) as mock_upsert:
            from sources.lib.db.operations.domain_fixers import (
                seed_default_domain_fixers,
            )

            await seed_default_domain_fixers(guild_id=1)
        assert mock_upsert.call_count == len(DEFAULT_DOMAIN_FIXERS)


class TestUpsertDomainFixer:
    async def test_adds_new_rule_when_no_existing_junction(self):
        """No existing junction → rule is created and junction is added without a delete."""
        session, ctx = _make_session(scalars_one=None)
        rule = SimpleNamespace(id=42)
        with (
            patch(
                'sources.lib.db.operations.domain_fixers.AsyncSession', return_value=ctx
            ),
            patch(
                'sources.lib.db.operations.domain_fixers._find_or_create_rule',
                new=AsyncMock(return_value=rule),
            ) as mock_find,
        ):
            from sources.lib.db.operations.domain_fixers import upsert_domain_fixer

            await upsert_domain_fixer(
                guild_id=1,
                source_domain='reddit.com',
                replacement_domain='redlib',
            )
        mock_find.assert_awaited_once_with(session, 'reddit.com', 'redlib', None)
        session.delete.assert_not_awaited()
        from sources.lib.db.models import GuildDomainFixer

        added = session.add.call_args[0][0]
        assert isinstance(added, GuildDomainFixer)
        assert added.guild_id == 1
        assert added.domain_fixer_id == 42
        session.commit.assert_awaited_once()

    async def test_replaces_existing_junction(self):
        """Existing junction for same source_domain is deleted before the new one is added."""
        existing_junction = SimpleNamespace()
        session, ctx = _make_session(scalars_one=existing_junction)
        rule = SimpleNamespace(id=99)
        with (
            patch(
                'sources.lib.db.operations.domain_fixers.AsyncSession', return_value=ctx
            ),
            patch(
                'sources.lib.db.operations.domain_fixers._find_or_create_rule',
                new=AsyncMock(return_value=rule),
            ),
        ):
            from sources.lib.db.operations.domain_fixers import upsert_domain_fixer

            await upsert_domain_fixer(
                guild_id=1,
                source_domain='reddit.com',
                replacement_domain='new-mirror',
            )
        session.delete.assert_awaited_once_with(existing_junction)
        session.flush.assert_awaited()
        session.commit.assert_awaited_once()

    async def test_passes_subdomain_override_to_find_or_create(self):
        """The override_subdomain argument is forwarded to _find_or_create_rule."""
        session, ctx = _make_session(scalars_one=None)
        rule = SimpleNamespace(id=7)
        with (
            patch(
                'sources.lib.db.operations.domain_fixers.AsyncSession', return_value=ctx
            ),
            patch(
                'sources.lib.db.operations.domain_fixers._find_or_create_rule',
                new=AsyncMock(return_value=rule),
            ) as mock_find,
        ):
            from sources.lib.db.operations.domain_fixers import upsert_domain_fixer

            await upsert_domain_fixer(
                guild_id=1,
                source_domain='twitter.com',
                replacement_domain='fxtwitter',
                override_subdomain='www',
            )
        mock_find.assert_awaited_once_with(session, 'twitter.com', 'fxtwitter', 'www')


# ---------------------------------------------------------------------------
# youtube_relay list/update operations (remaining)
# ---------------------------------------------------------------------------


class TestGetGuildYouTubeRelays:
    async def test_returns_relay_list(self):
        r1 = SimpleNamespace(id=1, yt_channel_id='UCxxx')
        session, ctx = _make_session(scalars_rows=[r1])
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import get_guild_relays

            result = await get_guild_relays(guild_id=1)
        assert result == [r1]


class TestRemoveYouTubeRelay:
    async def test_returns_false_when_not_found(self):
        session, ctx = _make_session(scalar=None)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import remove_relay

            result = await remove_relay(guild_id=1, yt_channel_id='UCxxx')
        assert result is False
        session.delete.assert_not_awaited()

    async def test_returns_true_and_deletes_when_found(self):
        relay = SimpleNamespace(guild_id=1, yt_channel_id='UCxxx')
        session, ctx = _make_session(scalar=relay)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import remove_relay

            result = await remove_relay(guild_id=1, yt_channel_id='UCxxx')
        assert result is True
        session.delete.assert_awaited_once_with(relay)
        session.commit.assert_awaited_once()


class TestSetRelayMessage:
    def _relay(self, **kwargs):
        defaults = {
            'yt_channel_title': 'My Channel',
            'message_video': None,
            'message_short': None,
            'message_live': None,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    async def test_returns_none_when_relay_not_found(self):
        session, ctx = _make_session(scalar=None)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import set_relay_message

            result = await set_relay_message(1, 'UCxxx', 'video', 'Hi!')
        assert result is None

    async def test_sets_video_field_and_returns_title(self):
        relay = self._relay()
        session, ctx = _make_session(scalar=relay)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import set_relay_message

            result = await set_relay_message(1, 'UCxxx', 'video', 'New video!')
        assert result == 'My Channel'
        assert relay.message_video == 'New video!'

    async def test_sets_short_field(self):
        relay = self._relay()
        session, ctx = _make_session(scalar=relay)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import set_relay_message

            await set_relay_message(1, 'UCxxx', 'short', 'Short dropped!')
        assert relay.message_short == 'Short dropped!'

    async def test_sets_live_field(self):
        relay = self._relay()
        session, ctx = _make_session(scalar=relay)
        with patch(
            'sources.lib.db.operations.youtube_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.youtube_relay import set_relay_message

            await set_relay_message(1, 'UCxxx', 'live', 'Live now!')
        assert relay.message_live == 'Live now!'


# ---------------------------------------------------------------------------
# birthdays operations (remaining)
# ---------------------------------------------------------------------------


class TestGetGuildMemberBirthday:
    async def test_returns_record_when_found(self):
        record = SimpleNamespace(guild_id=1, user_id=2)
        session, ctx = _make_session(scalars_one=record)
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import get_guild_member_birthday

            result = await get_guild_member_birthday(guild_id=1, user_id=2)
        assert result is record

    async def test_returns_none_when_not_found(self):
        session, ctx = _make_session(scalars_one=None)
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import get_guild_member_birthday

            result = await get_guild_member_birthday(guild_id=1, user_id=99)
        assert result is None


class TestSetGuildMemberBirthday:
    async def test_creates_record_when_not_exists(self):
        from sources.lib.db.models import GuildMemberBirthday

        session, ctx = _make_session(scalars_one=None)
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import set_guild_member_birthday

            await set_guild_member_birthday(
                guild_id=1, user_id=2, day=15, month=3, year=1990
            )
        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        assert isinstance(added, GuildMemberBirthday)
        assert added.birthday_day == 15
        assert added.birthday_month == 3

    async def test_updates_existing_and_resets_announced_year(self):
        existing = SimpleNamespace(
            guild_id=1,
            user_id=2,
            birthday_day=1,
            birthday_month=1,
            last_announced_year=2025,
        )
        session, ctx = _make_session(scalars_one=existing)
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import set_guild_member_birthday

            await set_guild_member_birthday(
                guild_id=1, user_id=2, day=15, month=3, year=None
            )
        assert existing.birthday_day == 15
        assert existing.birthday_month == 3
        assert existing.last_announced_year is None


class TestGetAllUnannouncedBirthdays:
    async def test_returns_unannounced_list(self):
        b1 = SimpleNamespace(user_id=1, last_announced_year=None)
        session, ctx = _make_session(scalars_rows=[b1])
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import (
                get_all_unannounced_birthdays_for_guild,
            )

            result = await get_all_unannounced_birthdays_for_guild(
                guild_id=1, current_year=2026
            )
        assert result == [b1]


class TestGetGuildSettings:
    async def test_returns_settings_when_found(self):
        settings = SimpleNamespace(guild_id=1, birthday_channel_id=100)
        session, ctx = _make_session(scalars_one=settings)
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import get_guild_settings

            result = await get_guild_settings(guild_id=1)
        assert result is settings

    async def test_returns_none_when_not_found(self):
        session, ctx = _make_session(scalars_one=None)
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import get_guild_settings

            result = await get_guild_settings(guild_id=1)
        assert result is None


class TestUpsertGuildSettings:
    async def test_creates_settings_when_not_exists(self):
        from sources.lib.db.models import GuildSettings

        session, ctx = _make_session(scalars_one=None)
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import upsert_guild_settings

            await upsert_guild_settings(guild_id=1, birthday_channel_id=100)
        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        assert isinstance(added, GuildSettings)

    async def test_updates_existing_settings(self):
        existing = SimpleNamespace(guild_id=1, birthday_channel_id=None)
        session, ctx = _make_session(scalars_one=existing)
        with patch(
            'sources.lib.db.operations.birthdays.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.birthdays import upsert_guild_settings

            await upsert_guild_settings(guild_id=1, birthday_channel_id=200)
        assert existing.birthday_channel_id == 200


# ---------------------------------------------------------------------------
# users operations (remaining)
# ---------------------------------------------------------------------------


class TestUpsertUser:
    async def test_creates_user_when_not_exists(self):
        from sources.lib.db.models import User

        session, ctx = _make_session(scalars_one=None)
        with patch('sources.lib.db.operations.users.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.users import upsert_user

            await upsert_user(user_id=1, name='Alice', timezone='UTC')
        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        assert isinstance(added, User)
        assert added.name == 'Alice'
        assert added.timezone == 'UTC'

    async def test_updates_existing_user(self):
        existing = SimpleNamespace(id=1, name='Old', timezone='Europe/London')
        session, ctx = _make_session(scalars_one=existing)
        with patch('sources.lib.db.operations.users.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.users import upsert_user

            await upsert_user(user_id=1, name='New', timezone='America/New_York')
        assert existing.name == 'New'
        assert existing.timezone == 'America/New_York'


# ---------------------------------------------------------------------------
# stats operations (remaining)
# ---------------------------------------------------------------------------


class TestGetAllChannelProgress:
    async def test_returns_progress_list(self):
        p1 = SimpleNamespace(channel_id=100, is_completed=False)
        p2 = SimpleNamespace(channel_id=200, is_completed=True)
        session, ctx = _make_session(scalars_rows=[p1, p2])
        with patch('sources.lib.db.operations.stats.AsyncSession', return_value=ctx):
            from sources.lib.db.operations.stats import get_all_channel_progress

            result = await get_all_channel_progress(guild_id=1)
        assert result == [p1, p2]


# ---------------------------------------------------------------------------
# telegram relay (remaining)
# ---------------------------------------------------------------------------


class TestGetAllTelegramRelays:
    async def test_returns_all_relays(self):
        r1, r2 = SimpleNamespace(id=1), SimpleNamespace(id=2)
        session, ctx = _make_session(scalars_rows=[r1, r2])
        with patch(
            'sources.lib.db.operations.telegram_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.telegram_relay import get_all_relays

            result = await get_all_relays()
        assert result == [r1, r2]


# ---------------------------------------------------------------------------
# twitch relay operations
# ---------------------------------------------------------------------------


class TestGetGuildTwitchRelays:
    async def test_returns_relay_list(self):
        r1 = SimpleNamespace(id=1, twitch_login='streamer')
        session, ctx = _make_session(scalars_rows=[r1])
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import get_guild_relays

            result = await get_guild_relays(guild_id=1)
        assert result == [r1]

    async def test_returns_empty_list_when_none(self):
        session, ctx = _make_session(scalars_rows=[])
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import get_guild_relays

            result = await get_guild_relays(guild_id=1)
        assert result == []


class TestGetAllTwitchRelays:
    async def test_returns_all_relays(self):
        r1, r2 = SimpleNamespace(id=1), SimpleNamespace(id=2)
        session, ctx = _make_session(scalars_rows=[r1, r2])
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import get_all_relays

            result = await get_all_relays()
        assert result == [r1, r2]

    async def test_returns_empty_when_none(self):
        session, ctx = _make_session(scalars_rows=[])
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import get_all_relays

            result = await get_all_relays()
        assert result == []


class TestAddTwitchRelay:
    async def test_returns_true_when_inserted(self):
        session, ctx = _make_session()
        execute_result = MagicMock()
        execute_result.rowcount = 1
        session.execute.return_value = execute_result
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import add_relay

            result = await add_relay(
                guild_id=1,
                twitch_user_id='42',
                twitch_login='streamer',
                discord_channel_id=100,
            )
        assert result is True
        session.commit.assert_awaited_once()

    async def test_returns_false_on_conflict(self):
        session, ctx = _make_session()
        execute_result = MagicMock()
        execute_result.rowcount = 0
        session.execute.return_value = execute_result
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import add_relay

            result = await add_relay(
                guild_id=1,
                twitch_user_id='42',
                twitch_login='streamer',
                discord_channel_id=100,
            )
        assert result is False


class TestRemoveTwitchRelay:
    async def test_returns_none_when_not_found(self):
        session, ctx = _make_session(scalar=None)
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import remove_relay

            result = await remove_relay(relay_id=99, guild_id=1)
        assert result is None
        session.delete.assert_not_awaited()

    async def test_returns_login_and_deletes_when_found(self):
        relay = SimpleNamespace(twitch_login='streamer', twitch_user_id='42')
        session, ctx = _make_session(scalar=relay)
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import remove_relay

            result = await remove_relay(relay_id=1, guild_id=1)
        assert result == ('streamer', '42')
        session.delete.assert_awaited_once_with(relay)
        session.commit.assert_awaited_once()

    async def test_guild_scope_enforced(self):
        # scalar returns None when guild_id does not match (the WHERE clause filters it out)
        session, ctx = _make_session(scalar=None)
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import remove_relay

            result = await remove_relay(relay_id=1, guild_id=999)
        assert result is None


class TestGetTwitchRelayById:
    async def test_returns_relay_when_found(self):
        relay = SimpleNamespace(id=1, twitch_login='streamer')
        session, ctx = _make_session(scalar=relay)
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import get_relay_by_id

            result = await get_relay_by_id(relay_id=1, guild_id=1)
        assert result is relay

    async def test_returns_none_when_not_found(self):
        session, ctx = _make_session(scalar=None)
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import get_relay_by_id

            result = await get_relay_by_id(relay_id=99, guild_id=1)
        assert result is None


class TestSetTwitchRelayMessage:
    async def test_returns_none_when_relay_not_found(self):
        session, ctx = _make_session(scalar=None)
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import set_relay_message

            result = await set_relay_message(relay_id=99, guild_id=1, message='Hi!')
        assert result is None
        session.commit.assert_not_awaited()

    async def test_sets_message_and_returns_login(self):
        relay = SimpleNamespace(twitch_login='streamer', custom_message=None)
        session, ctx = _make_session(scalar=relay)
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import set_relay_message

            result = await set_relay_message(
                relay_id=1, guild_id=1, message='Live now!'
            )
        assert result == 'streamer'
        assert relay.custom_message == 'Live now!'
        session.commit.assert_awaited_once()

    async def test_clears_message_with_none(self):
        relay = SimpleNamespace(twitch_login='streamer', custom_message='Old message')
        session, ctx = _make_session(scalar=relay)
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import set_relay_message

            result = await set_relay_message(relay_id=1, guild_id=1, message=None)
        assert result == 'streamer'
        assert relay.custom_message is None
        session.commit.assert_awaited_once()


class TestUpdateTwitchRelayChannel:
    async def test_returns_none_when_relay_not_found(self):
        session, ctx = _make_session()
        session.scalar.side_effect = [None]
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import update_relay_channel

            result = await update_relay_channel(
                relay_id=99, guild_id=1, discord_channel_id=200
            )
        assert result is None
        session.commit.assert_not_awaited()

    async def test_raises_value_error_on_conflict(self):
        relay = SimpleNamespace(
            twitch_login='streamer', twitch_user_id='42', discord_channel_id=100
        )
        conflict = SimpleNamespace(id=2)
        session, ctx = _make_session()
        session.scalar.side_effect = [relay, conflict]
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import update_relay_channel

            with pytest.raises(ValueError, match='duplicate'):
                await update_relay_channel(
                    relay_id=1, guild_id=1, discord_channel_id=200
                )
        session.commit.assert_not_awaited()

    async def test_updates_channel_and_returns_login(self):
        relay = SimpleNamespace(
            twitch_login='streamer', twitch_user_id='42', discord_channel_id=100
        )
        session, ctx = _make_session()
        session.scalar.side_effect = [relay, None]  # relay found, no conflict
        with patch(
            'sources.lib.db.operations.twitch_relay.AsyncSession', return_value=ctx
        ):
            from sources.lib.db.operations.twitch_relay import update_relay_channel

            result = await update_relay_channel(
                relay_id=1, guild_id=1, discord_channel_id=200
            )
        assert result == 'streamer'
        assert relay.discord_channel_id == 200
        session.commit.assert_awaited_once()
