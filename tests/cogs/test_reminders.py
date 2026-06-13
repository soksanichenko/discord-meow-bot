"""Tests for reminder parsing logic."""

from datetime import UTC, datetime, timedelta

from sources.lib.views.reminders import MAX_FUTURE_DAYS, parse_when


class TestParseWhen:
    def test_returns_future_datetime_for_relative_input(self):
        result = parse_when('in 1 hour', None)
        assert result is not None
        assert result > datetime.now(tz=UTC)

    def test_returns_timezone_aware_datetime(self):
        result = parse_when('in 30 minutes', None)
        assert result is not None
        assert result.tzinfo is not None

    def test_returns_none_for_garbage_input(self):
        result = parse_when('not a date at all xyzzy', None)
        assert result is None

    def test_returns_none_for_empty_string(self):
        result = parse_when('', None)
        assert result is None

    def test_respects_timezone_str(self):
        utc_result = parse_when('in 2 hours', None)
        tz_result = parse_when('in 2 hours', 'America/New_York')
        assert utc_result is not None
        assert tz_result is not None
        # Both point to roughly the same future time (within a few seconds).
        diff = abs((utc_result - tz_result).total_seconds())
        assert diff < 5

    def test_in_one_day(self):
        result = parse_when('in 1 day', None)
        assert result is not None
        now = datetime.now(tz=UTC)
        assert result > now + timedelta(hours=23)
        assert result < now + timedelta(hours=25)

    def test_tomorrow(self):
        result = parse_when('tomorrow', None)
        assert result is not None
        assert result > datetime.now(tz=UTC)

    def test_max_future_days_constant(self):
        assert MAX_FUTURE_DAYS == 365
