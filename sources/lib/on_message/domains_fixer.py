"""Domains fixer module"""

from copy import copy
from urllib.parse import ParseResult
from urllib.parse import urlparse

import discord
from tldextract import extract
from tldextract.tldextract import ExtractResult

from sources.lib.utils import Logger


def fix_urls(message: discord.Message) -> str:
    """
    Fix the URLs by replacing an original domain by a fixer
    :param message: a message from Discord
    :return: a fixed message content
    """
    # subdomain=None means keep the original subdomain from the URL
    domains = {
        'reddit.com':  {'domain': 'rxddit',   'subdomain': None},
        'x.com':       {'domain': 'fixupx',   'subdomain': None},
        'twitter.com': {'domain': 'fxtwitter', 'subdomain': None},
        'tiktok.com':  {'domain': 'tnktok',   'subdomain': None},
    }

    msg_content_lines = message.content.split()
    parsed_urls = {
        (parsed_url := urlparse(line)): extract(parsed_url.netloc)
        for line in msg_content_lines
        if line.startswith("http://") or line.startswith("https://")
    }
    if all(
        parsed_domain.top_domain_under_public_suffix not in domains
        for parsed_domain in parsed_urls.values()
    ):
        Logger().info("No suitable domain or any URL found")
        return message.content
    final_urls = {
        parsed_url.geturl(): ParseResult(
            parsed_url.scheme,
            netloc=ExtractResult(
                subdomain=fixer.get('subdomain') or parsed_domain.subdomain,
                domain=fixer['domain'],
                suffix=parsed_domain.suffix,
                is_private=parsed_domain.is_private,
                registry_suffix=parsed_domain.registry_suffix,
            ).fqdn,
            path=parsed_url.path,
            query=parsed_url.query,
            params=parsed_url.params,
            fragment=parsed_url.fragment,
        ).geturl()
        for parsed_url, parsed_domain in parsed_urls.items()
        for fixer in (domains[parsed_domain.top_domain_under_public_suffix],)
    }
    content = copy(message.content)
    for original_url, fixed_url in final_urls.items():
        content = content.replace(original_url, fixed_url)
    content += f"\nOriginal message posted by {message.author.mention}"
    return content
