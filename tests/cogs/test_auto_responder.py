"""Tests for the AutoResponderCog on_message listener and cooldown logic."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def _make_bot():
    bot = MagicMock()
    bot.user = SimpleNamespace(id=999)
    return bot


def _make_message(guild_id: int, author_id: int, mentions: list) -> MagicMock:
    msg = MagicMock()
    msg.guild = SimpleNamespace(id=guild_id)
    msg.author = SimpleNamespace(id=author_id)
    msg.mentions = mentions
    msg.channel = AsyncMock()
    msg.reply = AsyncMock()
    return msg


class TestOnMessage:
    async def test_ignores_bot_messages(self):
        from sources.lib.cogs.auto_responder import AutoResponderCog

        bot = _make_bot()
        cog = AutoResponderCog(bot)
        msg = _make_message(1, 999, [SimpleNamespace(id=1)])
        msg.author = bot.user

        with patch(
            'sources.lib.cogs.auto_responder.get_auto_responder', new_callable=AsyncMock
        ) as mock_get:
            await cog.on_message(msg)

        mock_get.assert_not_called()

    async def test_ignores_messages_without_mentions(self):
        from sources.lib.cogs.auto_responder import AutoResponderCog

        bot = _make_bot()
        cog = AutoResponderCog(bot)
        msg = _make_message(1, 1, [])

        with patch(
            'sources.lib.cogs.auto_responder.get_auto_responder', new_callable=AsyncMock
        ) as mock_get:
            await cog.on_message(msg)

        mock_get.assert_not_called()

    async def test_ignores_dm_messages(self):
        from sources.lib.cogs.auto_responder import AutoResponderCog

        bot = _make_bot()
        cog = AutoResponderCog(bot)
        msg = _make_message(1, 1, [SimpleNamespace(id=2)])
        msg.guild = None

        with patch(
            'sources.lib.cogs.auto_responder.get_auto_responder', new_callable=AsyncMock
        ) as mock_get:
            await cog.on_message(msg)

        mock_get.assert_not_called()

    async def test_sends_response_when_responder_exists(self):
        from sources.lib.cogs.auto_responder import AutoResponderCog

        bot = _make_bot()
        cog = AutoResponderCog(bot)
        mentioned = SimpleNamespace(id=2, display_name='Alice')
        msg = _make_message(1, 1, [mentioned])
        responder = SimpleNamespace(response_text='I am away')

        with patch(
            'sources.lib.cogs.auto_responder.get_auto_responder',
            new_callable=AsyncMock,
            return_value=responder,
        ):
            await cog.on_message(msg)

        msg.reply.assert_awaited_once_with('**Alice**: I am away')

    async def test_does_not_send_when_no_responder(self):
        from sources.lib.cogs.auto_responder import AutoResponderCog

        bot = _make_bot()
        cog = AutoResponderCog(bot)
        mentioned = SimpleNamespace(id=2, display_name='Alice')
        msg = _make_message(1, 1, [mentioned])

        with patch(
            'sources.lib.cogs.auto_responder.get_auto_responder',
            new_callable=AsyncMock,
            return_value=None,
        ):
            await cog.on_message(msg)

        msg.reply.assert_not_awaited()

    async def test_cooldown_suppresses_second_fire(self):
        from sources.lib.cogs.auto_responder import AutoResponderCog

        bot = _make_bot()
        cog = AutoResponderCog(bot)
        mentioned = SimpleNamespace(id=2, display_name='Alice')
        msg = _make_message(1, 1, [mentioned])
        responder = SimpleNamespace(response_text='away')

        with patch(
            'sources.lib.cogs.auto_responder.get_auto_responder',
            new_callable=AsyncMock,
            return_value=responder,
        ):
            await cog.on_message(msg)
            await cog.on_message(msg)

        # Only the first message should trigger a reply
        assert msg.reply.await_count == 1

    async def test_cooldown_expires_after_300_seconds(self):
        from sources.lib.cogs.auto_responder import AutoResponderCog

        bot = _make_bot()
        cog = AutoResponderCog(bot)
        mentioned = SimpleNamespace(id=2, display_name='Alice')
        msg = _make_message(1, 1, [mentioned])
        responder = SimpleNamespace(response_text='away')

        # Manually set the cooldown to 301 seconds ago
        past = datetime.now(tz=UTC) - timedelta(seconds=301)
        cog._cooldowns[(1, 2)] = past

        with patch(
            'sources.lib.cogs.auto_responder.get_auto_responder',
            new_callable=AsyncMock,
            return_value=responder,
        ):
            await cog.on_message(msg)

        msg.reply.assert_awaited_once_with('**Alice**: away')

    async def test_multiple_mentions_each_checked_independently(self):
        from sources.lib.cogs.auto_responder import AutoResponderCog

        bot = _make_bot()
        cog = AutoResponderCog(bot)
        user_a = SimpleNamespace(id=2, display_name='Alice')
        user_b = SimpleNamespace(id=3, display_name='Bob')
        msg = _make_message(1, 1, [user_a, user_b])

        responders = {
            2: SimpleNamespace(response_text='A away'),
            3: SimpleNamespace(response_text='B away'),
        }

        async def _get(guild_id, user_id):
            return responders.get(user_id)

        with patch(
            'sources.lib.cogs.auto_responder.get_auto_responder', side_effect=_get
        ):
            await cog.on_message(msg)

        assert msg.reply.await_count == 2
