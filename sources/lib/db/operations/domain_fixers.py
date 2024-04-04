"""
Operations with DB table `users`
"""

from sqlalchemy import select

from sources.lib.db import AsyncSession
from sources.lib.db.models import DomainFixer


async def get_domain_fixers(only_enabled: bool = False) -> list[DomainFixer]:
    """Get all enabled domain fixers"""
    async with AsyncSession() as db_session:
        domain_fixers = select(DomainFixer)
        if only_enabled:
            domain_fixers = domain_fixers.filter(DomainFixer.enabled.is_(True))
        return (await db_session.scalars(domain_fixers)).all()
