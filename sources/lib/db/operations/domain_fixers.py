"""Operations with DB tables `domain_fixers` and `guild_domain_fixers`"""

from sqlalchemy import select

from sources.lib.db import AsyncSession
from sources.lib.db.models import DomainFixer, GuildDomainFixer

DEFAULT_DOMAIN_FIXERS: list[dict] = [
    {'source_domain': 'reddit.com',  'replacement_domain': 'rxddit',   'override_subdomain': None},
    {'source_domain': 'x.com',       'replacement_domain': 'fixupx',   'override_subdomain': None},
    {'source_domain': 'twitter.com', 'replacement_domain': 'fxtwitter', 'override_subdomain': None},
    {'source_domain': 'tiktok.com',  'replacement_domain': 'tnktok',   'override_subdomain': None},
]


async def _find_or_create_rule(
    db_session: AsyncSession,
    source_domain: str,
    replacement_domain: str,
    override_subdomain: str | None,
) -> DomainFixer:
    """Return an existing DomainFixer rule matching all three fields, or create one.

    Uses is_not_distinct_from for NULL-safe comparison of override_subdomain.

    Args:
        db_session: Active async DB session.
        source_domain: Domain to match.
        replacement_domain: Replacement domain name.
        override_subdomain: Subdomain override, or None to keep original.

    Returns:
        Existing or newly created DomainFixer instance.
    """
    rule = (await db_session.scalars(
        select(DomainFixer).where(
            DomainFixer.source_domain == source_domain,
            DomainFixer.replacement_domain == replacement_domain,
            DomainFixer.override_subdomain.is_not_distinct_from(override_subdomain),
        )
    )).one_or_none()

    if rule is None:
        rule = DomainFixer(
            source_domain=source_domain,
            replacement_domain=replacement_domain,
            override_subdomain=override_subdomain,
        )
        db_session.add(rule)
        await db_session.flush()

    return rule


async def get_all_domain_fixers(guild_id: int) -> list[DomainFixer]:
    """Return all domain fixer rules linked to the given guild.

    Args:
        guild_id: Discord guild ID.

    Returns:
        List of DomainFixer records for the guild.
    """
    async with AsyncSession() as db_session:
        result = await db_session.scalars(
            select(DomainFixer)
            .join(GuildDomainFixer, GuildDomainFixer.domain_fixer_id == DomainFixer.id)
            .where(GuildDomainFixer.guild_id == guild_id)
        )
        return list(result.all())


async def upsert_domain_fixer(
    guild_id: int,
    source_domain: str,
    replacement_domain: str,
    override_subdomain: str | None = None,
) -> None:
    """Link a domain fixer rule to the guild, replacing any existing rule for that source domain.

    If the exact rule (source, replacement, override) already exists in domain_fixers it is
    reused; otherwise a new row is created. Any previous association for this guild and
    source_domain is removed first to enforce one rule per source per guild.

    Args:
        guild_id: Discord guild ID.
        source_domain: Domain to match, e.g. 'reddit.com'.
        replacement_domain: Replacement domain name, e.g. 'rxddit'.
        override_subdomain: Subdomain override; None keeps the original subdomain.
    """
    async with AsyncSession() as db_session:
        # Remove any existing association for this guild + source_domain
        existing_junction = (await db_session.scalars(
            select(GuildDomainFixer)
            .join(DomainFixer, DomainFixer.id == GuildDomainFixer.domain_fixer_id)
            .where(
                GuildDomainFixer.guild_id == guild_id,
                DomainFixer.source_domain == source_domain,
            )
        )).one_or_none()

        if existing_junction is not None:
            await db_session.delete(existing_junction)
            await db_session.flush()

        rule = await _find_or_create_rule(
            db_session, source_domain, replacement_domain, override_subdomain,
        )
        db_session.add(GuildDomainFixer(guild_id=guild_id, domain_fixer_id=rule.id))
        await db_session.commit()


async def seed_default_domain_fixers(guild_id: int) -> None:
    """Upsert the default domain fixer rules for the given guild.

    Safe to call on a guild that already has rules — only the default source
    domains are overwritten, custom rules for other domains are left untouched.

    Args:
        guild_id: Discord guild ID.
    """
    for fixer in DEFAULT_DOMAIN_FIXERS:
        await upsert_domain_fixer(
            guild_id=guild_id,
            source_domain=fixer['source_domain'],
            replacement_domain=fixer['replacement_domain'],
            override_subdomain=fixer['override_subdomain'],
        )


async def delete_domain_fixer(guild_id: int, source_domain: str) -> None:
    """Remove the domain fixer rule for the given source domain from the guild.

    The rule row in domain_fixers is kept — it may still be used by other guilds.

    Args:
        guild_id: Discord guild ID.
        source_domain: Domain to remove.
    """
    async with AsyncSession() as db_session:
        junction = (await db_session.scalars(
            select(GuildDomainFixer)
            .join(DomainFixer, DomainFixer.id == GuildDomainFixer.domain_fixer_id)
            .where(
                GuildDomainFixer.guild_id == guild_id,
                DomainFixer.source_domain == source_domain,
            )
        )).one_or_none()

        if junction is not None:
            await db_session.delete(junction)
            await db_session.commit()
