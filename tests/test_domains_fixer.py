"""Tests for URL domain fixing logic."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sources.lib.utils.domains_fixer import fix_urls


def _message(content: str, guild_id: int = 1) -> SimpleNamespace:
    guild = SimpleNamespace(id=guild_id)
    author = SimpleNamespace(mention='@user')
    return SimpleNamespace(content=content, guild=guild, author=author)


def _dm_message(content: str) -> SimpleNamespace:
    author = SimpleNamespace(mention='@user')
    return SimpleNamespace(content=content, guild=None, author=author)


def _fixer(
    source: str, replacement: str, subdomain: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        source_domain=source,
        replacement_domain=replacement,
        override_subdomain=subdomain,
    )


class TestFixUrls:
    async def test_dm_returns_original_content(self):
        msg = _dm_message('hello https://reddit.com/r/python')
        result = await fix_urls(msg)
        assert result == 'hello https://reddit.com/r/python'

    async def test_no_matching_rules_returns_original(self):
        msg = _message('check https://example.com/post')
        with patch(
            'sources.lib.utils.domains_fixer.get_all_domain_fixers',
            new=AsyncMock(return_value=[]),
        ):
            result = await fix_urls(msg)
        assert result == 'check https://example.com/post'

    async def test_replaces_matched_domain(self):
        msg = _message('check https://reddit.com/r/python')
        fixer = _fixer('reddit.com', 'rxddit')
        with patch(
            'sources.lib.utils.domains_fixer.get_all_domain_fixers',
            new=AsyncMock(return_value=[fixer]),
        ):
            result = await fix_urls(msg)
        assert 'https://rxddit.com' in result
        assert 'reddit.com' not in result.split('\n')[0]

    async def test_appends_original_author_mention(self):
        msg = _message('check https://reddit.com/r/python')
        fixer = _fixer('reddit.com', 'rxddit')
        with patch(
            'sources.lib.utils.domains_fixer.get_all_domain_fixers',
            new=AsyncMock(return_value=[fixer]),
        ):
            result = await fix_urls(msg)
        assert '@user' in result

    async def test_no_url_returns_original(self):
        msg = _message('just a plain text message')
        with patch(
            'sources.lib.utils.domains_fixer.get_all_domain_fixers',
            new=AsyncMock(return_value=[_fixer('reddit.com', 'rxddit')]),
        ):
            result = await fix_urls(msg)
        assert result == 'just a plain text message'

    async def test_override_subdomain_replaces_subdomain(self):
        msg = _message('check https://www.reddit.com/r/python')
        fixer = _fixer('reddit.com', 'rxddit', subdomain='old')
        with patch(
            'sources.lib.utils.domains_fixer.get_all_domain_fixers',
            new=AsyncMock(return_value=[fixer]),
        ):
            result = await fix_urls(msg)
        assert 'https://old.rxddit.com' in result

    async def test_no_override_subdomain_preserves_original_subdomain(self):
        msg = _message('check https://www.reddit.com/r/python')
        fixer = _fixer('reddit.com', 'rxddit', subdomain=None)
        with patch(
            'sources.lib.utils.domains_fixer.get_all_domain_fixers',
            new=AsyncMock(return_value=[fixer]),
        ):
            result = await fix_urls(msg)
        assert 'https://www.rxddit.com' in result

    async def test_preserves_url_path(self):
        msg = _message('https://twitter.com/user/status/123456')
        fixer = _fixer('twitter.com', 'fxtwitter')
        with patch(
            'sources.lib.utils.domains_fixer.get_all_domain_fixers',
            new=AsyncMock(return_value=[fixer]),
        ):
            result = await fix_urls(msg)
        assert '/user/status/123456' in result
