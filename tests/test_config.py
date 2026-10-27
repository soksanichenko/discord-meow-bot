"""Tests for the Twitch EventSub webhook settings in Config."""

import pytest
from pydantic import ValidationError

from sources.config import Config


def test_eventsub_defaults_are_empty_and_interval_is_five():
    cfg = Config()

    assert cfg.twitch_eventsub_secret.get_secret_value() == ''
    assert cfg.twitch_eventsub_callback_url == ''
    assert cfg.twitch_reconcile_interval_minutes == 5


def test_callback_url_must_be_https():
    with pytest.raises(ValidationError, match='must start with https://'):
        Config(twitch_eventsub_callback_url='http://example.com/x')

    cfg = Config(twitch_eventsub_callback_url='https://example.com/x')
    assert cfg.twitch_eventsub_callback_url == 'https://example.com/x'


@pytest.mark.parametrize('secret', ['a' * 9, 'a' * 101, 'sécret-with-non-ascii'])
def test_secret_rejects_wrong_length_or_charset(secret):
    with pytest.raises(ValidationError, match='10-100 ASCII characters'):
        Config(twitch_eventsub_secret=secret)


@pytest.mark.parametrize('secret', ['a' * 10, 'a' * 100, ''])
def test_secret_accepts_valid_values_and_empty(secret):
    cfg = Config(twitch_eventsub_secret=secret)

    assert cfg.twitch_eventsub_secret.get_secret_value() == secret
