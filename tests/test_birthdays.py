"""Tests for pure helper functions in birthdays.py."""

from unittest.mock import MagicMock

import discord

from sources.lib.cogs.birthdays import _format_message, _format_ordinal, _validate_date


def _member(
    name: str = 'alice',
    display_name: str = 'Alice',
    mention: str = '<@1>',
) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.name = name
    member.display_name = display_name
    member.mention = mention
    return member


class TestFormatOrdinal:
    """_format_ordinal returns correct English ordinal suffix."""

    def test_1st(self) -> None:
        assert _format_ordinal(1) == '1st'

    def test_2nd(self) -> None:
        assert _format_ordinal(2) == '2nd'

    def test_3rd(self) -> None:
        assert _format_ordinal(3) == '3rd'

    def test_4th(self) -> None:
        assert _format_ordinal(4) == '4th'

    def test_11th_special_case(self) -> None:
        assert _format_ordinal(11) == '11th'

    def test_12th_special_case(self) -> None:
        assert _format_ordinal(12) == '12th'

    def test_13th_special_case(self) -> None:
        assert _format_ordinal(13) == '13th'

    def test_21st(self) -> None:
        assert _format_ordinal(21) == '21st'

    def test_22nd(self) -> None:
        assert _format_ordinal(22) == '22nd'

    def test_23rd(self) -> None:
        assert _format_ordinal(23) == '23rd'

    def test_111th_special_case(self) -> None:
        assert _format_ordinal(111) == '111th'

    def test_121st(self) -> None:
        assert _format_ordinal(121) == '121st'


class TestValidateDate:
    """_validate_date enforces calendar correctness."""

    def test_valid_date_with_year(self) -> None:
        assert _validate_date(15, 6, 1990) is True

    def test_valid_date_without_year(self) -> None:
        assert _validate_date(15, 6, None) is True

    def test_first_day_of_year(self) -> None:
        assert _validate_date(1, 1, None) is True

    def test_last_day_of_january(self) -> None:
        assert _validate_date(31, 1, None) is True

    def test_feb_29_on_leap_year(self) -> None:
        assert _validate_date(29, 2, 2000) is True

    def test_feb_29_no_year_uses_2000_which_is_leap(self) -> None:
        assert _validate_date(29, 2, None) is True

    def test_invalid_month_13(self) -> None:
        assert _validate_date(1, 13, None) is False

    def test_invalid_month_0(self) -> None:
        assert _validate_date(1, 0, None) is False

    def test_day_zero(self) -> None:
        assert _validate_date(0, 1, None) is False

    def test_day_32(self) -> None:
        assert _validate_date(32, 1, None) is False

    def test_feb_30_invalid(self) -> None:
        assert _validate_date(30, 2, None) is False

    def test_feb_29_on_non_leap_year(self) -> None:
        assert _validate_date(29, 2, 1900) is False

    def test_april_has_no_31st(self) -> None:
        assert _validate_date(31, 4, None) is False


class TestFormatMessage:
    """_format_message renders birthday announcement text."""

    def test_default_without_birth_year_omits_age(self) -> None:
        result = _format_message(None, _member(), birth_year=None, current_year=2026)
        assert '<@1>' in result
        assert 'Happy birthday' in result
        assert 'turning' not in result

    def test_default_with_birth_year_includes_ordinal_age(self) -> None:
        result = _format_message(None, _member(), birth_year=2000, current_year=2026)
        assert '<@1>' in result
        assert '26th' in result

    def test_custom_template_mention(self) -> None:
        result = _format_message(
            'Hi {mention}!', _member(), birth_year=None, current_year=2026
        )
        assert result == 'Hi <@1>!'

    def test_custom_template_username_and_display_name(self) -> None:
        result = _format_message(
            '{username}/{display_name}',
            _member(name='alice', display_name='Alice'),
            birth_year=None,
            current_year=2026,
        )
        assert result == 'alice/Alice'

    def test_custom_template_age_placeholder(self) -> None:
        result = _format_message(
            'Turning {age}!', _member(), birth_year=2000, current_year=2026
        )
        assert result == 'Turning 26th!'

    def test_unknown_placeholder_preserved(self) -> None:
        result = _format_message(
            'Hello {unknown}!', _member(), birth_year=None, current_year=2026
        )
        assert result == 'Hello {unknown}!'
