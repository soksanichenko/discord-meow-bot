"""DB models"""

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class Guild(Base):
    """
    A table contains IDs and names of the discord servers
    there is the bot is connected
    """

    __tablename__ = "guilds"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class User(Base):
    """
    A tables describes of the discord users
    """

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)


class DomainFixer(Base):
    """
    Deduplicated URL domain replacement rules.

    Rules are shared across guilds via GuildDomainFixer.
    The combination (source_domain, replacement_domain, override_subdomain) is unique —
    NULLs in override_subdomain are treated as equal (NULLS NOT DISTINCT).
    """

    __tablename__ = 'domain_fixers'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_domain: Mapped[str] = mapped_column(Text, nullable=False)
    replacement_domain: Mapped[str] = mapped_column(Text, nullable=False)
    override_subdomain: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            'uq_domain_fixers_rule',
            'source_domain', 'replacement_domain', 'override_subdomain',
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class GuildDomainFixer(Base):
    """Junction table linking guilds to their domain fixer rules (many-to-many)."""

    __tablename__ = 'guild_domain_fixers'

    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey('guilds.id', ondelete='CASCADE'), primary_key=True,
    )
    domain_fixer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('domain_fixers.id', ondelete='CASCADE'), primary_key=True,
    )
