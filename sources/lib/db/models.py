"""DB models"""

from sqlalchemy import Text, BigInteger, Boolean
from sqlalchemy.orm import declarative_base, Mapped, mapped_column


Base = declarative_base()


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
    A table describes the discord users
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)


class DomainFixer(Base):
    """
    A table describes fixers for domains
    """

    __tablename__ = "domain_fixers"

    original: Mapped[str] = mapped_column(Text, primary_key=True)
    fixer: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
