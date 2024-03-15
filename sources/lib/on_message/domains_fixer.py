"""Domains fixer module"""
from copy import copy
from urllib.parse import urlparse, ParseResult

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
    domains = {
        "reddit.com": "rxddit",
        "tiktok.com": "vxtiktok",
        "x.com": "fixupx",
        "twitter.com": "fxtwitter",
        "instagram.com": "ddinstagram",
    }

    msg_content_lines = message.content.split()
    parsed_urls = {
        (parsed_url := urlparse(line)): extract(parsed_url.netloc)
        for line in msg_content_lines
        if line.startswith("http://") or line.startswith("https://")
    }
    if all(
        parsed_domain.registered_domain not in domains
        for parsed_domain in parsed_urls.values()
    ):
        Logger().logger.info("No suitable domain or any URL found")
        return message.content
    final_urls = {
        parsed_url.geturl(): ParseResult(
            parsed_url.scheme,
            netloc=ExtractResult(
                subdomain=parsed_domain.subdomain,
                domain=domains[parsed_domain.registered_domain],
                suffix=parsed_domain.suffix,
                is_private=parsed_domain.is_private,
            ).fqdn,
            path=parsed_url.path,
            query=parsed_url.query,
            params=parsed_url.params,
            fragment=parsed_url.fragment,
        ).geturl()
        for parsed_url, parsed_domain in parsed_urls.items()
    }
    content = copy(message.content)
    for original_url, fixed_url in final_urls.items():
        content = content.replace(original_url, fixed_url)
    content += f"\nOriginal message posted by {message.author.mention}"
    return content
