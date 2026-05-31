"""Domains fixer module"""

from urllib.parse import ParseResult, urlparse

import discord
from tldextract import extract
from tldextract.tldextract import ExtractResult

from sources.lib.db.operations.domain_fixers import get_all_domain_fixers
from sources.lib.utils.logger import Logger


class URLFixer:
    """Replaces source domains in a Discord message with configured alternatives.

    Usage: ``await URLFixer(message).fix()``

    Args:
        message: The Discord message whose URLs should be rewritten.
    """

    def __init__(self, message: discord.Message) -> None:
        """Initialise for a specific message.

        Args:
            message: The Discord message to process.
        """
        self._message = message
        self._logger = Logger()
        self._rules: dict[str, dict] = {}
        self._parsed_urls: dict[ParseResult, ExtractResult] = {}

    async def fix(self) -> str:
        """Return the message content with matched domains replaced.

        Returns the original content unchanged when the message is a DM,
        contains no URLs, or no rules match any URL.

        Returns:
            Rewritten content string, or original content if nothing matched.
        """
        if self._message.guild is None:
            return self._message.content
        await self._load_rules()
        self._parse_urls()
        if not self._has_matches():
            self._logger.info('No suitable domain or any URL found')
            return self._message.content
        return self._apply_replacements()

    async def _load_rules(self) -> None:
        fixers = await get_all_domain_fixers(guild_id=self._message.guild.id)
        self._rules = {
            f.source_domain: {
                'domain': f.replacement_domain,
                'subdomain': f.override_subdomain,
            }
            for f in fixers
        }

    def _parse_urls(self) -> None:
        self._parsed_urls = {}
        for token in self._message.content.split():
            if token.startswith('http://') or token.startswith('https://'):
                parsed = urlparse(token)
                self._parsed_urls[parsed] = extract(parsed.netloc)

    def _has_matches(self) -> bool:
        return any(
            d.top_domain_under_public_suffix in self._rules
            for d in self._parsed_urls.values()
        )

    def _rewrite_url(
        self, parsed_url: ParseResult, parsed_domain: ExtractResult
    ) -> str:
        """Build the replacement URL for a matched domain.

        Args:
            parsed_url: Parsed original URL.
            parsed_domain: Extracted domain components of the original URL.

        Returns:
            Rewritten URL string.
        """
        rule = self._rules[parsed_domain.top_domain_under_public_suffix]
        return ParseResult(
            parsed_url.scheme,
            netloc=ExtractResult(
                subdomain=rule.get('subdomain') or parsed_domain.subdomain,
                domain=rule['domain'],
                suffix=parsed_domain.suffix,
                is_private=parsed_domain.is_private,
                registry_suffix=parsed_domain.registry_suffix,
            ).fqdn,
            path=parsed_url.path,
            query=parsed_url.query,
            params=parsed_url.params,
            fragment=parsed_url.fragment,
        ).geturl()

    def _apply_replacements(self) -> str:
        content = self._message.content
        for parsed_url, parsed_domain in self._parsed_urls.items():
            if parsed_domain.top_domain_under_public_suffix not in self._rules:
                continue
            content = content.replace(
                parsed_url.geturl(), self._rewrite_url(parsed_url, parsed_domain)
            )
        return content + f'\nOriginal message posted by {self._message.author.mention}'


async def fix_urls(message: discord.Message) -> str:
    """Fix the URLs in a message by replacing source domains with their configured replacements.

    Rules are scoped to the guild the message was sent in.
    Returns the original content unchanged for DMs or when no rules match.

    Args:
        message: A message from Discord.

    Returns:
        The message content with fixed URLs, or the original content if no URLs matched.
    """
    return await URLFixer(message).fix()
