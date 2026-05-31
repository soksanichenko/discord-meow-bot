"""Integration tests against a real PostgreSQL container.

These tests cover SQL-level behaviour that mocks cannot catch:
cascade deletes, unique constraints (including NULLS NOT DISTINCT),
and upsert semantics. Each test class uses a unique guild-ID range
so tests do not interfere with each other even without per-test rollback.

Run with Docker available. Skip with: pytest -m "not integration"
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sources.lib.db.models import (
    DomainFixer,
    Guild,
    GuildMemberBirthday,
    GuildSettings,
    MessageStats,
    TelegramRelay,
    TwitchAuth,
    TwitchLiveSession,
    TwitchRelay,
    YouTubeLiveSession,
    YouTubeRelay,
)

pytestmark = pytest.mark.integration


class TestSchema:
    """Alembic migrations produce the complete expected schema."""

    async def test_alembic_version_recorded(self, db_session: AsyncSession) -> None:
        """alembic_version table exists and contains a revision, proving migrations ran.

        Args:
            db_session: Async session bound to the test container.
        """
        result = await db_session.execute(
            text('SELECT version_num FROM alembic_version')
        )
        version = result.scalar()
        assert version is not None, 'alembic_version is empty — migrations did not run'

    async def test_all_tables_exist(self, db_session: AsyncSession) -> None:
        """Verify that every ORM-mapped table is present in the test database.

        Args:
            db_session: Async session bound to the test container.
        """
        expected_tables = [
            'guilds',
            'users',
            'domain_fixers',
            'guild_domain_fixers',
            'guild_settings',
            'guild_member_birthdays',
            'music_links_channels',
            'reminders',
            'message_stats',
            'stats_import_progress',
            'telegram_relays',
            'youtube_relays',
            'youtube_live_sessions',
            'twitch_auth',
            'twitch_relays',
            'twitch_live_sessions',
        ]
        for table in expected_tables:
            result = await db_session.execute(
                text(
                    'SELECT EXISTS ('
                    '  SELECT FROM information_schema.tables'
                    '  WHERE table_schema = :s AND table_name = :t'
                    ')'
                ),
                {'s': 'public', 't': table},
            )
            assert result.scalar() is True, f'Table {table!r} missing from schema'


class TestGuildCascade:
    """ON DELETE CASCADE from guilds removes all dependent rows."""

    # Guild IDs 910_001–910_099 reserved for this class.
    _GUILD_SETTINGS = 910_001
    _GUILD_BIRTHDAYS = 910_002

    async def test_cascade_deletes_guild_settings(
        self, db_session: AsyncSession
    ) -> None:
        """Deleting a guild row must also delete its GuildSettings row.

        Args:
            db_session: Async session bound to the test container.
        """
        db_session.add(Guild(id=self._GUILD_SETTINGS, name='Cascade Settings'))
        db_session.add(GuildSettings(guild_id=self._GUILD_SETTINGS))
        await db_session.commit()

        guild = await db_session.get(Guild, self._GUILD_SETTINGS)
        await db_session.delete(guild)
        await db_session.commit()

        assert await db_session.get(GuildSettings, self._GUILD_SETTINGS) is None

    async def test_cascade_deletes_member_birthdays(
        self, db_session: AsyncSession
    ) -> None:
        """Deleting a guild row must also delete all its GuildMemberBirthday rows.

        Args:
            db_session: Async session bound to the test container.
        """
        db_session.add(Guild(id=self._GUILD_BIRTHDAYS, name='Cascade Birthdays'))
        db_session.add(
            GuildMemberBirthday(
                guild_id=self._GUILD_BIRTHDAYS,
                user_id=1,
                birthday_day=1,
                birthday_month=6,
            )
        )
        await db_session.commit()

        guild = await db_session.get(Guild, self._GUILD_BIRTHDAYS)
        await db_session.delete(guild)
        await db_session.commit()

        remaining = (
            (
                await db_session.execute(
                    select(GuildMemberBirthday).where(
                        GuildMemberBirthday.guild_id == self._GUILD_BIRTHDAYS
                    )
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []


class TestDomainFixerConstraint:
    """NULLS NOT DISTINCT unique index on domain_fixers."""

    async def test_duplicate_null_subdomain_raises(
        self, db_session: AsyncSession
    ) -> None:
        """Two rows with identical domains and NULL override_subdomain must be rejected.

        Args:
            db_session: Async session bound to the test container.
        """
        db_session.add(
            DomainFixer(
                source_domain='inttest-dup.example',
                replacement_domain='mirror.inttest-dup.example',
                override_subdomain=None,
            )
        )
        await db_session.commit()

        db_session.add(
            DomainFixer(
                source_domain='inttest-dup.example',
                replacement_domain='mirror.inttest-dup.example',
                override_subdomain=None,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_same_domains_different_subdomain_allowed(
        self, db_session: AsyncSession
    ) -> None:
        """NULL and a non-NULL subdomain for the same domain pair are distinct rows.

        Args:
            db_session: Async session bound to the test container.
        """
        db_session.add(
            DomainFixer(
                source_domain='inttest-sub.example',
                replacement_domain='mirror.inttest-sub.example',
                override_subdomain=None,
            )
        )
        db_session.add(
            DomainFixer(
                source_domain='inttest-sub.example',
                replacement_domain='mirror.inttest-sub.example',
                override_subdomain='www',
            )
        )
        await db_session.commit()


class TestMessageStatsUpsert:
    """INSERT ... ON CONFLICT DO UPDATE increments message_count correctly."""

    # Guild ID 910_010 reserved for this class.
    _GUILD_ID = 910_010
    _USER_ID = 1

    async def test_upsert_increments_count(self, db_session: AsyncSession) -> None:
        """Two upsert executions for the same (guild, user) must produce count=2.

        Args:
            db_session: Async session bound to the test container.
        """
        db_session.add(Guild(id=self._GUILD_ID, name='Stats Test'))
        await db_session.commit()

        stmt = (
            pg_insert(MessageStats)
            .values(
                guild_id=self._GUILD_ID,
                user_id=self._USER_ID,
                message_count=1,
            )
            .on_conflict_do_update(
                index_elements=['guild_id', 'user_id'],
                set_={'message_count': MessageStats.message_count + 1},
            )
        )
        await db_session.execute(stmt)
        await db_session.execute(stmt)
        await db_session.commit()

        row = (
            await db_session.execute(
                select(MessageStats).where(
                    MessageStats.guild_id == self._GUILD_ID,
                    MessageStats.user_id == self._USER_ID,
                )
            )
        ).scalar_one()
        assert row.message_count == 2


class TestYouTubeRelayConstraint:
    """Unique constraint prevents duplicate relays for the same guild + channel pair."""

    # Guild ID 910_020 reserved for this class.
    _GUILD_ID = 910_020

    async def test_duplicate_relay_raises(self, db_session: AsyncSession) -> None:
        """Inserting two YouTubeRelay rows with the same guild+channel IDs must fail.

        Args:
            db_session: Async session bound to the test container.
        """
        db_session.add(Guild(id=self._GUILD_ID, name='YT Relay Test'))
        await db_session.commit()

        db_session.add(
            YouTubeRelay(
                guild_id=self._GUILD_ID,
                yt_channel_id='UCinttest001',
                yt_channel_title='Integration Test Channel',
                discord_channel_id=111_111,
            )
        )
        await db_session.commit()

        db_session.add(
            YouTubeRelay(
                guild_id=self._GUILD_ID,
                yt_channel_id='UCinttest001',
                yt_channel_title='Integration Test Channel',
                discord_channel_id=111_111,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()


class TestTwitchRelayConstraint:
    """Unique constraint prevents duplicate Twitch relays for the same guild + channel pair."""

    # Guild ID 910_030 reserved for this class.
    _GUILD_ID = 910_030

    async def test_duplicate_relay_raises(self, db_session: AsyncSession) -> None:
        """Inserting two TwitchRelay rows with the same guild+channel IDs must fail.

        Args:
            db_session: Async session bound to the test container.
        """
        db_session.add(Guild(id=self._GUILD_ID, name='Twitch Relay Test'))
        await db_session.commit()

        db_session.add(
            TwitchRelay(
                guild_id=self._GUILD_ID,
                twitch_user_id='12345',
                twitch_login='teststreamer',
                discord_channel_id=222_222,
            )
        )
        await db_session.commit()

        db_session.add(
            TwitchRelay(
                guild_id=self._GUILD_ID,
                twitch_user_id='12345',
                twitch_login='teststreamer',
                discord_channel_id=222_222,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()


class TestTwitchLiveSessionConstraint:
    """TwitchLiveSession allows only one active session per relay."""

    # Guild ID 910_040 reserved for this class.
    _GUILD_ID = 910_040

    async def test_duplicate_live_session_raises(
        self, db_session: AsyncSession
    ) -> None:
        """Inserting two TwitchLiveSession rows for the same relay_id must fail.

        Args:
            db_session: Async session bound to the test container.
        """
        db_session.add(Guild(id=self._GUILD_ID, name='Twitch Session Test'))
        await db_session.commit()

        db_session.add(
            TwitchRelay(
                id=80_001,
                guild_id=self._GUILD_ID,
                twitch_user_id='99999',
                twitch_login='sessionstreamer',
                discord_channel_id=333_333,
            )
        )
        await db_session.commit()

        db_session.add(TwitchLiveSession(relay_id=80_001, discord_message_id=1))
        await db_session.commit()

        db_session.add(TwitchLiveSession(relay_id=80_001, discord_message_id=2))
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()


class TestYouTubeLiveSessionConstraint:
    """YouTubeLiveSession unique constraint on (relay_id, video_id)."""

    # Guild ID 910_050 reserved for this class.
    _GUILD_ID = 910_050

    async def test_duplicate_session_raises(self, db_session: AsyncSession) -> None:
        """Inserting two YouTubeLiveSession rows with the same relay+video must fail.

        Args:
            db_session: Async session bound to the test container.
        """
        db_session.add(Guild(id=self._GUILD_ID, name='YT Session Test'))
        await db_session.commit()

        db_session.add(
            YouTubeRelay(
                id=80_002,
                guild_id=self._GUILD_ID,
                yt_channel_id='UCsessiontest',
                yt_channel_title='Session Test Channel',
                discord_channel_id=444_444,
            )
        )
        await db_session.commit()

        db_session.add(YouTubeLiveSession(relay_id=80_002, video_id='vid001'))
        await db_session.commit()

        db_session.add(YouTubeLiveSession(relay_id=80_002, video_id='vid001'))
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_different_video_id_allowed(self, db_session: AsyncSession) -> None:
        """Two YouTubeLiveSession rows with different video_ids for the same relay are valid.

        Args:
            db_session: Async session bound to the test container.
        """
        db_session.add(YouTubeLiveSession(relay_id=80_002, video_id='vid002'))
        await db_session.commit()


class TestRelayCascade:
    """ON DELETE CASCADE removes relay rows when the parent guild is deleted."""

    # Guild IDs 910_060–910_062 reserved for this class.
    _GUILD_YT = 910_060
    _GUILD_TWITCH = 910_061
    _GUILD_TELEGRAM = 910_062

    async def test_cascade_deletes_youtube_relays(
        self, db_session: AsyncSession
    ) -> None:
        """Deleting a guild must also delete its YouTubeRelay rows.

        Args:
            db_session: Async session bound to the test container.
        """
        db_session.add(Guild(id=self._GUILD_YT, name='YT Cascade'))
        db_session.add(
            YouTubeRelay(
                guild_id=self._GUILD_YT,
                yt_channel_id='UCcascade',
                yt_channel_title='Cascade Channel',
                discord_channel_id=555_001,
            )
        )
        await db_session.commit()

        guild = await db_session.get(Guild, self._GUILD_YT)
        await db_session.delete(guild)
        await db_session.commit()

        remaining = (
            (
                await db_session.execute(
                    select(YouTubeRelay).where(YouTubeRelay.guild_id == self._GUILD_YT)
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []

    async def test_cascade_deletes_twitch_relays(
        self, db_session: AsyncSession
    ) -> None:
        """Deleting a guild must also delete its TwitchRelay rows.

        Args:
            db_session: Async session bound to the test container.
        """
        db_session.add(Guild(id=self._GUILD_TWITCH, name='Twitch Cascade'))
        db_session.add(
            TwitchRelay(
                guild_id=self._GUILD_TWITCH,
                twitch_user_id='77777',
                twitch_login='cascadestreamer',
                discord_channel_id=555_002,
            )
        )
        await db_session.commit()

        guild = await db_session.get(Guild, self._GUILD_TWITCH)
        await db_session.delete(guild)
        await db_session.commit()

        remaining = (
            (
                await db_session.execute(
                    select(TwitchRelay).where(
                        TwitchRelay.guild_id == self._GUILD_TWITCH
                    )
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []

    async def test_cascade_deletes_telegram_relays(
        self, db_session: AsyncSession
    ) -> None:
        """Deleting a guild must also delete its TelegramRelay rows.

        Args:
            db_session: Async session bound to the test container.
        """
        db_session.add(Guild(id=self._GUILD_TELEGRAM, name='Telegram Cascade'))
        db_session.add(
            TelegramRelay(
                guild_id=self._GUILD_TELEGRAM,
                tg_username='cascadechannel',
                discord_channel_id=555_003,
            )
        )
        await db_session.commit()

        guild = await db_session.get(Guild, self._GUILD_TELEGRAM)
        await db_session.delete(guild)
        await db_session.commit()

        remaining = (
            (
                await db_session.execute(
                    select(TelegramRelay).where(
                        TelegramRelay.guild_id == self._GUILD_TELEGRAM
                    )
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []


class TestTwitchAuth:
    """TwitchAuth single-row upsert semantics (id always 1)."""

    async def test_insert_and_update(self, db_session: AsyncSession) -> None:
        """Inserting then updating TwitchAuth row 1 must succeed without duplicates.

        Args:
            db_session: Async session bound to the test container.
        """
        from datetime import UTC, datetime, timedelta

        expires = datetime.now(UTC) + timedelta(hours=1)
        db_session.add(
            TwitchAuth(
                id=1,
                access_token='token_a',
                refresh_token='refresh_a',
                expires_at=expires,
            )
        )
        await db_session.commit()

        row = await db_session.get(TwitchAuth, 1)
        row.access_token = 'token_b'
        await db_session.commit()

        updated = await db_session.get(TwitchAuth, 1)
        assert updated.access_token == 'token_b'
